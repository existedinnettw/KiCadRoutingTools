#!/usr/bin/env python3
"""Copper-scope net-class clearance rules from the board's .kicad_dru.

An isolation rule with no Type term binds EVERY copper pair across the class
boundary (zectio_b's galvanic barrier):

    (rule "field logic isolation"
        (condition "A.NetClass == 'Logic*' && B.NetClass != 'Logic*'")
        (constraint clearance (min 0.75mm)))

KRT used to skip it with the "non-layer scope" note, and routing that board
left ~100 kicad-cli clearance violations of it. These tests pin:

  * the parser subset: the zectio rule, both A/B orders, KiCad's wildcard
    `==` (case-insensitive, `*` / `?`), the two-pattern pair forms, the
    single-class no-Type form, and the shapes still skipped with the note;
  * the #735 track-only forms parsing to the SAME TrackRule as before;
  * the pair resolver (raise-only, pair-exact; `track=False` sees only
    copper-scope rules) and that the shared per-obstacle map ignores them;
  * GridRouteConfig.pair_clearance / pad_pair_clearance for every kind;
  * the router's per-search overlay: binds against the other side only, and
    leaves the shared map exactly as it found it;
  * check_drc grading track-track and via-track pairs with the rule.
"""
import json
import os
import sys
import tempfile

_TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_TESTS)
for p in (ROOT, _TESTS, os.path.join(ROOT, 'py_router'),
          os.path.join(ROOT, 'py_placer'), os.path.join(ROOT, 'py_tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

from kicad_dru import (TrackRule, class_matches, rule_binds,  # noqa: E402
                       parse_dru_track_clearances, parse_dru_layer_clearances,
                       track_pair_clearance, effective_track_clearances)
from routing_config import GridRouteConfig  # noqa: E402

ZECTIO = ("A.NetClass == 'Logic*' && B.NetClass != 'Logic*'")
ISO = TrackRule('field logic isolation', 'Logic*', True, 0.75, scope='copper')
LOGIC, LPWR, FIELD, NONE = (frozenset({'Logic'}), frozenset({'Logic_Power'}),
                            frozenset({'Field_Power'}), frozenset())


def _rule(cond, val="0.75mm", name="field logic isolation"):
    return (f'(version 1)\n(rule "{name}" (condition "{cond}") '
            f'(constraint clearance (min {val})))')


def test_zectio_rule_parses_in_both_orders():
    for cond in (ZECTIO,
                 "B.NetClass != 'Logic*' && A.NetClass == 'Logic*'",
                 "A.NetClass != 'Logic*' && B.NetClass == 'Logic*'",
                 "B.NetClass == 'Logic*' && A.NetClass != 'Logic*'"):
        rules, notes = parse_dru_track_clearances(_rule(cond))
        assert rules == [ISO], (cond, rules)
        assert any('class-pair channel' in n for n in notes), notes
    print("  PASS: the isolation rule, every A/B spelling -> one copper rule")


def test_zectio_rule_never_reaches_the_layer_map():
    lmap, notes = parse_dru_layer_clearances(_rule(ZECTIO), ['F.Cu', 'B.Cu'])
    assert lmap == {}, lmap
    assert not any('skipped' in n for n in notes), notes
    print("  PASS: layer map untouched, no skip note")


def test_wildcard_matching_is_kicads():
    assert class_matches(LOGIC, 'Logic*') and class_matches(LPWR, 'Logic*')
    assert not class_matches(FIELD, 'Logic*')
    assert class_matches({'logic_power'}, 'LOGIC*')      # case-insensitive
    assert class_matches({'Logic'}, 'Lo?ic') and not class_matches({'Logc'}, 'Lo?ic')
    assert class_matches({'a[1]'}, 'a[1]')                # no regex classes
    # No class at all is KiCad's Default (net-0 copper too).
    assert class_matches(NONE, 'Default') and class_matches(None, 'Def*')
    assert not class_matches(NONE, 'Logic*')
    print("  PASS: * / ?, case-insensitive, unclassed = Default")


def test_the_isolation_rule_binds_only_across_the_boundary():
    assert rule_binds(ISO, LOGIC, FIELD) and rule_binds(ISO, FIELD, LOGIC)
    assert rule_binds(ISO, LPWR, NONE) and rule_binds(ISO, NONE, LOGIC)
    assert not rule_binds(ISO, LOGIC, LPWR)               # both Logic*
    assert not rule_binds(ISO, FIELD, NONE)               # both field side
    print("  PASS: Logic* vs everything else, symmetric; same side exempt")


def test_two_pattern_and_single_class_forms():
    rules, _ = parse_dru_track_clearances(
        _rule("A.NetClass == 'HV' && B.NetClass == 'LV*'", name='hv_lv'))
    assert rules == [TrackRule('hv_lv', 'HV', False, 0.75, scope='copper',
                               other=('==', 'LV*'))], rules
    r = rules[0]
    assert rule_binds(r, {'HV'}, {'LV_IO'}) and rule_binds(r, {'LV1'}, {'HV'})
    assert not rule_binds(r, {'HV'}, {'HV'}) and not rule_binds(r, {'HV'}, NONE)
    rules, _ = parse_dru_track_clearances(
        _rule("B.NetClass != 'GND' && A.NetClass == 'HV'", name='hv'))
    assert rules == [TrackRule('hv', 'HV', False, 0.75, scope='copper',
                               other=('!=', 'GND'))], rules
    assert rule_binds(rules[0], {'HV'}, {'HV'})
    assert not rule_binds(rules[0], {'HV'}, {'GND'})
    rules, _ = parse_dru_track_clearances(
        _rule("A.NetClass == 'HV'", name='hv1'))
    assert rules == [TrackRule('hv1', 'HV', False, 0.75, scope='copper')], rules
    assert rule_binds(rules[0], {'HV'}, NONE) and rule_binds(rules[0], NONE, {'HV'})
    assert not rule_binds(rules[0], NONE, NONE)
    print("  PASS: P==/Q==, P==/Q!=, and the single-class form")


def test_shapes_outside_the_subset_keep_the_skip_note():
    bad = [
        "A.NetClass != 'Logic*' && B.NetClass != 'Field*'",   # no == anchor
        "A.NetClass != 'Logic*'",
        "A.NetClass == 'Logic*' && A.NetClass != 'X'",        # two on one side
        "A.NetClass == 'Logic*' && A.Type == 'pad'",          # other predicate
        "A.NetClass == 'Logic*' || B.NetClass == 'Logic*'",   # ||
        "A.Type=='track' && A.NetClass=='Logic*'",            # one side typed
    ]
    for cond in bad:
        rules, notes = parse_dru_track_clearances(_rule(cond))
        assert rules == [], (cond, rules)
        assert any('skipped' in n for n in notes), (cond, notes)
    # A layer clause takes the rule out of the class subset as before.
    text = ('(version 1)\n(rule iso (layer "F.Cu") (condition "'
            + ZECTIO + '") (constraint clearance (min 0.75mm)))')
    rules, notes = parse_dru_track_clearances(text)
    assert rules == [] and any('skipped' in n for n in notes), (rules, notes)
    print(f"  PASS: {len(bad) + 1} out-of-subset shapes skipped, note kept")


def test_track_only_forms_are_unchanged():
    for cond, want in (
            ("A.Type=='track' && B.Type=='track' && A.NetClass=='USB'",
             TrackRule('usb', 'USB', False, 2.4)),
            ("A.Type=='track' && B.Type=='track' && "
             "A.NetClass=='USB' && B.NetClass!='USB'",
             TrackRule('usb', 'USB', True, 2.4))):
        rules, notes = parse_dru_track_clearances(_rule(cond, '2.4mm', 'usb'))
        assert rules == [want] and rules[0].scope == 'track', (cond, rules)
        assert any('track channel' in n for n in notes), notes
    # Still skipped on the track side: two different classes.
    rules, _ = parse_dru_track_clearances(_rule(
        "A.Type=='track' && B.Type=='track' && A.NetClass=='USB' "
        "&& B.NetClass=='CLK'"))
    assert rules == [], rules
    print("  PASS: #735 track rules parse to the same TrackRule as before")


def test_pair_resolver_is_raise_only_and_pair_exact():
    trk = TrackRule('usb', 'USB', False, 2.4)
    rules = [ISO, trk]
    assert track_pair_clearance(rules, LOGIC, FIELD, 0.2) == (0.75, ISO)
    assert track_pair_clearance(rules, LOGIC, LPWR, 0.2) == (0.2, None)
    assert track_pair_clearance(rules, FIELD, NONE, 0.2) == (0.2, None)
    assert track_pair_clearance(rules, LOGIC, FIELD, 1.0) == (1.0, None)
    # track=False (a via / pad pair): the track-scope rule cannot bind.
    assert track_pair_clearance(rules, {'USB'}, NONE, 0.2, track=False) == (0.2, None)
    assert track_pair_clearance(rules, {'USB'}, NONE, 0.2) == (2.4, trk)
    assert track_pair_clearance(rules, LOGIC, NONE, 0.2, track=False) == (0.75, ISO)
    print("  PASS: raise-only, pair-exact; track=False ignores track rules")


def test_shared_obstacle_map_ignores_copper_rules():
    mem = {1: LOGIC, 2: FIELD, 3: NONE}
    assert effective_track_clearances([ISO], mem, [1, 2, 3], [1, 2, 3]) == {}
    trk = TrackRule('usb', 'USB', False, 2.4)
    mem[4] = {'USB'}
    assert effective_track_clearances([ISO, trk], mem, [1, 2, 3, 4], [4]) == \
        {1: 2.4, 2: 2.4, 3: 2.4, 4: 2.4}
    print("  PASS: copper rules never inflate the shared per-obstacle map")


def _cfg():
    cfg = GridRouteConfig(layers=['F.Cu', 'B.Cu'], grid_step=0.05,
                          track_width=0.2, clearance=0.2)
    cfg.class_rules = [ISO]
    cfg.class_memberships = {1: LOGIC, 2: FIELD, 4: LPWR}
    return cfg


def test_config_pair_clearance_every_kind():
    from synth import make_pad
    cfg = _cfg()
    for kind in ('layer', 'track', 'stack'):
        assert cfg.pair_clearance(1, 2, 'F.Cu', kind=kind) == 0.75, kind
        assert cfg.pair_clearance(2, 3, 'F.Cu', kind=kind) == 0.2, kind
        assert cfg.pair_clearance(1, 4, 'F.Cu', kind=kind) == 0.2, kind
    assert cfg.pair_clearance(1, 0) == 0.75          # net 0 is Default
    assert cfg.pad_pair_clearance(make_pad(net_id=2), 1, 'F.Cu') == 0.75
    assert cfg.pad_pair_clearance(make_pad(net_id=2), 3) == 0.2
    assert cfg.max_pair_clearance() == 0.75 and not cfg.pair_clearance_inert()
    plain = GridRouteConfig(layers=['F.Cu'], grid_step=0.05, track_width=0.2,
                            clearance=0.2)
    assert plain.pair_clearance(1, 2) == 0.2 and plain.pair_clearance_inert()
    print("  PASS: layer/track/stack/pad pairs raised across the boundary only")


def _overlay_board():
    from synth import make_pcb, make_net, make_seg
    nets = {i: make_net(i, n) for i, n in
            ((1, 'LOGIC_A'), (2, 'FIELD_B'), (3, 'FIELD_C'))}
    segs = [make_seg(10, 10, 20, 10, net_id=2), make_seg(10, 20, 20, 20, net_id=1)]
    return make_pcb(nets=nets, segments=segs)


def test_overlay_binds_the_other_side_and_balances():
    from obstacle_cache import (GridObstacleMap, copper_rule_overlay,
                                with_copper_rule_overlay)
    if GridObstacleMap is None:
        print("  SKIP: grid_router not built")
        return
    pcb = _overlay_board()
    cfg = _cfg()
    cfg.class_memberships = {1: LOGIC}
    over = copper_rule_overlay(pcb, 1, cfg)   # Logic search: both field nets
    assert len(over) == 2, over
    assert len(copper_rule_overlay(pcb, 3, cfg)) == 1   # field: only Logic
    obstacles = GridObstacleMap(2)
    # A cell 0.5 mm off FIELD_B's track edge-to-centre: legal at 0.2, not at
    # 0.75 for a 0.2 mm Logic track (0.75 + 0.1 + 0.1 track half-widths).
    gx, gy = int(round(15 / 0.05)), int(round((10 + 0.6) / 0.05))
    seen = {}

    @with_copper_rule_overlay
    def search(pcb_data, net_id, config, obs):
        seen[net_id] = obs.is_blocked(gx, gy, 0)
        return net_id

    assert search(pcb, 1, cfg, obstacles) == 1 and seen[1] is True
    assert search(pcb, 3, cfg, obstacles) == 3 and seen[3] is False
    assert not obstacles.is_blocked(gx, gy, 0)
    assert obstacles.get_stats() == GridObstacleMap(2).get_stats()
    print("  PASS: Logic search sees field copper at 0.75; map restored")


def test_foreign_distance_gates_fold_the_rule():
    """The post-route gates (smoothing, necking, rescue) measure through the
    single_ended_routing foreign-distance helpers with `dist >= base + w/2`;
    the rule must reach them as an excess, for the moving net only."""
    from single_ended_routing import _seg_foreign_seg_dist, _seg_foreign_via_dist
    from synth import make_via
    pcb = _overlay_board()
    pcb.vias.append(make_via(15, 25, net_id=2))
    seg = (10, 10.6, 20, 10.6, 'F.Cu')      # 0.5 mm off FIELD_B's centreline
    plain = _seg_foreign_seg_dist(pcb, 1, *seg, base_clearance=0.2)
    pcb._class_rule_ctx = ([ISO], {1: LOGIC})
    ruled = _seg_foreign_seg_dist(pcb, 1, *seg, base_clearance=0.2)
    assert abs((plain - ruled) - 0.55) < 1e-9, (plain, ruled)
    assert _seg_foreign_seg_dist(pcb, 3, *seg, base_clearance=0.2) == plain
    vseg = (14, 25.8, 16, 25.8, 'F.Cu')
    vr = _seg_foreign_via_dist(pcb, 1, *vseg, base_clearance=0.2)
    vf = _seg_foreign_via_dist(pcb, 3, *vseg, base_clearance=0.2)
    assert abs((vf - vr) - 0.55) < 1e-9, (vf, vr)
    print("  PASS: seg/via distance gates see the 0.55 mm excess for Logic only")


def test_routing_net_prices_a_per_net_map():
    cfg = _cfg()
    assert cfg.obstacle_clearance(2) == 0.2           # shared map: untouched
    with cfg.routing_net(1):
        assert cfg.obstacle_clearance(2) == 0.75
        assert cfg.obstacle_clearance(4) == 0.2       # Logic_Power: same side
    assert cfg.obstacle_clearance(2) == 0.2 and cfg.class_rule_net is None
    print("  PASS: routing_net raises a one-net map, restores on exit")


def test_install_reads_the_rule_and_nested_runs_inherit_it():
    """install_track_clearances puts the copper rule on the config (beside an
    explicit track map too) and on the board; a nested engine handed the same
    in-memory board but the OUTPUT path (no .kicad_dru there yet) keeps it."""
    from kicad_dru import install_track_clearances
    from kicad_parser import parse_kicad_pcb
    with tempfile.TemporaryDirectory() as d:
        pcb_path = os.path.join(d, 'b.kicad_pcb')
        open(pcb_path, 'w').write(_PCB)
        open(os.path.join(d, 'b.kicad_pro'), 'w').write(json.dumps(_PRO))
        open(os.path.join(d, 'b.kicad_dru'), 'w').write(_rule(ZECTIO))
        pcb = parse_kicad_pcb(pcb_path)
        cfg = GridRouteConfig(layers=['F.Cu', 'B.Cu'], clearance=0.2)
        install_track_clearances(cfg, {}, pcb_path, pcb)
        assert cfg.class_rules == [ISO] and cfg.track_clearances == {}
        logic = [n for n, net in pcb.nets.items() if net.name == 'LOGIC_A']
        assert cfg.class_memberships.get(logic[0]) == LOGIC
        assert pcb._class_rule_ctx[0] == [ISO]
        nested = GridRouteConfig(layers=['F.Cu', 'B.Cu'], clearance=0.2)
        install_track_clearances(nested, None,
                                 os.path.join(d, 'out.kicad_pcb'), pcb)
        assert nested.class_rules == [ISO], nested.class_rules
        fresh = parse_kicad_pcb(pcb_path)
        plain = GridRouteConfig(layers=['F.Cu', 'B.Cu'], clearance=0.2)
        install_track_clearances(plain, None,
                                 os.path.join(d, 'out.kicad_pcb'), fresh)
        assert plain.class_rules == [] and fresh._class_rule_ctx is None
    print("  PASS: installed from the board's rules; nested runs inherit")


_PCB = """(kicad_pcb (version 20241229) (generator "synth")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (net 0 "") (net 1 "LOGIC_A") (net 2 "FIELD_B") (net 3 "FIELD_C")
  (gr_rect (start 0 0) (end 60 40) (layer "Edge.Cuts") (uuid "e"))
  (segment (start 10 10) (end 20 10) (width 0.2) (layer "F.Cu") (net 1) (uuid "s1"))
  (segment (start 10 10.6) (end 20 10.6) (width 0.2) (layer "F.Cu") (net 2) (uuid "s2"))
  (segment (start 10 11.2) (end 20 11.2) (width 0.2) (layer "F.Cu") (net 3) (uuid "s3"))
  (segment (start 29 9) (end 31 9) (width 0.2) (layer "F.Cu") (net 1) (uuid "s4"))
  (via (at 30 10) (size 0.5) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2) (uuid "v1"))
)
"""
_PRO = {"net_settings": {
    "classes": [{"name": "Default", "clearance": 0.2},
                {"name": "Logic", "clearance": 0.2}],
    "netclass_patterns": [{"netclass": "Logic", "pattern": "LOGIC_*"}]}}


def test_check_drc_grades_the_rule_on_every_pair():
    from check_drc import run_drc
    with tempfile.TemporaryDirectory() as d:
        pcb = os.path.join(d, 'b.kicad_pcb')
        open(pcb, 'w').write(_PCB)
        open(os.path.join(d, 'b.kicad_pro'), 'w').write(json.dumps(_PRO))

        def viols():
            return run_drc(pcb, clearance=0.2, quiet=True, print_summary=False,
                           check_sizes=False, clearance_margin=0.0)
        assert viols() == [], viols()                    # no rule: clean
        open(os.path.join(d, 'b.kicad_dru'), 'w').write(_rule(ZECTIO))
        v = viols()
        pairs = sorted(tuple(sorted((x.get('net1'), x.get('net2')))) for x in v)
        assert pairs == [('FIELD_B', 'LOGIC_A'), ('FIELD_B', 'LOGIC_A')], \
            [(x['type'], x.get('net1'), x.get('net2')) for x in v]
        assert {x['type'] for x in v} == {'segment-segment', 'via-segment'}, \
            [x['type'] for x in v]
    print("  PASS: seg-seg and via-seg across the boundary flagged; "
          "field-field at 0.4 mm passes")


def test_layer_rule_does_not_undo_the_class_rule():
    """A layer clearance replaces the class value, as in KiCad, but the class
    rule then raises it again (check_drc's order): both the per-net map
    (routing_net) and the overlay's per-value config keep 0.75 on a layer
    ruled at 0.2."""
    from dataclasses import replace
    cfg = _cfg()
    cfg.layer_clearances = {'F.Cu': 0.2}
    with cfg.routing_net(1):
        assert cfg.layer_clearance('F.Cu', cfg.obstacle_clearance(2)) == 0.75
        assert cfg.layer_clearance('F.Cu', cfg.obstacle_clearance(4)) == 0.2
    over = replace(cfg, class_rule_floor=0.75, class_rules=[])
    assert over.layer_clearance('F.Cu', over.obstacle_clearance(2)) == 0.75
    assert cfg.layer_clearance('F.Cu', cfg.obstacle_clearance(2)) == 0.2
    print("  PASS: the class rule survives the layer replacement")


def test_distance_windows_reach_the_largest_rule():
    from single_ended_routing import _foreign_window, _FOREIGN_PAD_WINDOW
    pcb = _overlay_board()
    assert _foreign_window(pcb, 0.2) == _FOREIGN_PAD_WINDOW
    big = ISO._replace(clearance_mm=8.0)
    pcb._class_rule_ctx = ([big], {1: LOGIC})
    assert abs(_foreign_window(pcb, 0.2) - (_FOREIGN_PAD_WINDOW + 7.8)) < 1e-9
    print("  PASS: an 8 mm rule widens the distance windows")


def test_overlay_includes_unconnected_copper_pads():
    """Net-0 copper pads are Default class in KiCad, so a Logic-vs-non-Logic
    rule binds against them; NPTH holes carry no copper and stay out."""
    from obstacle_cache import GridObstacleMap, copper_rule_overlay
    if GridObstacleMap is None:
        print("  SKIP: grid_router not built")
        return
    from synth import make_pad
    pcb = _overlay_board()
    cfg = _cfg()
    cfg.class_memberships = {1: LOGIC}
    before = len(copper_rule_overlay(pcb, 1, cfg))
    pcb.pads_by_net[0] = [make_pad(0, 30, 30, ref='H1'),
                          make_pad(0, 40, 30, ref='H2', drill=1.0,
                                   pad_type='np_thru_hole', layers=('F.Cu', 'B.Cu'))]
    pcb._copper_rule_overlay_cache = None
    over = copper_rule_overlay(pcb, 1, cfg)
    assert len(over) == before + 1, (before, len(over))
    print("  PASS: net-0 copper pads join the Logic overlay")


_PCB_EDGE = _PCB.replace('(start 10 10) (end 20 10) (width 0.2) (layer "F.Cu") (net 1) (uuid "s1")',
                         '(start 10 3.7) (end 20 3.7) (width 0.2) (layer "F.Cu") (net 1) (uuid "s1")') \
                .replace('(start 10 10.6) (end 20 10.6) (width 0.2) (layer "F.Cu") (net 2) (uuid "s2")',
                         '(start 10 4.3) (end 20 4.3) (width 0.2) (layer "F.Cu") (net 2) (uuid "s2")')


def test_check_drc_sees_pairs_across_a_cell_boundary():
    """Parallel tracks either side of a 2 mm index cell boundary (y=3.7 and
    y=4.3, legal at 0.2 mm, not at the 0.75 mm rule) must still be compared."""
    from check_drc import run_drc
    with tempfile.TemporaryDirectory() as d:
        pcb = os.path.join(d, 'b.kicad_pcb')
        open(pcb, 'w').write(_PCB_EDGE)
        open(os.path.join(d, 'b.kicad_pro'), 'w').write(json.dumps(_PRO))
        open(os.path.join(d, 'b.kicad_dru'), 'w').write(_rule(ZECTIO))
        v = run_drc(pcb, clearance=0.2, quiet=True, print_summary=False,
                    check_sizes=False, clearance_margin=0.0)
        segseg = [x for x in v if x['type'] == 'segment-segment'
                  and {x.get('net1'), x.get('net2')} == {'LOGIC_A', 'FIELD_B'}]
        assert segseg, [(x['type'], x.get('net1'), x.get('net2')) for x in v]
    print("  PASS: the cross-boundary pair is graded against the rule")


if __name__ == '__main__':
    for k, v in sorted(globals().items()):
        if k.startswith('test_'):
            print(f"--- {k}")
            v()
    print("ALL PASS")
