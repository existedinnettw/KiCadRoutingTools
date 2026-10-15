#!/usr/bin/env python3
"""#1193: `place_seed --repair` charges pad COPPER off the outline, not the
pad union's box, and a locked violator does not fail the gate -- unless its
pad copper is past the outline, where a lock is no waiver (#1241).

fa10 board 09 (StickHub): the north edge carries a 1 mm notch, and the locked
J5's pad UNION box had a corner in it (0.65 mm) while no pad did --
`grade_pad_legality` read `oob_pad_copper_refs []`, check_assembly read
buildable. The repair's off-board census measured that box, charged J5, put
the locked part in `unrepairable`, and exited 4 at `grade_errors 0`, against
place_seed's own contract that a pinned part's finding does not fail the
gate. JSON_SUMMARY said `unrepairable: 1` and named nothing.

The input is synthesized here: a 40 x 30 board whose north edge has a notch
(x 18..22, 1 mm deep), and parts placed so that

* J5 (locked): its union box's corner is in the notch, no pad is;
* J6 (locked): a pad really is in the notch;
* R3 (free): a pad in the notch, declared an edge part with no edge, which
  the repair will not guess -- a free violator it cannot seat;
* H9 (free): a pad-less NPTH mounting hole in the notch, which the per-pad
  census must still see (the old union box carried holes too).

What each case pins:

* J5 is not charged, and the run exits 0 -- the defect;
* J6 is charged, reported PINNED, named in `unrepairable_pinned_refs`, and,
  its pad copper being past the outline, named in
  `unrepairable_pinned_off_board_refs` and fails the gate (exit 4) -- a lock is
  no waiver there, since placement stamps its own locks (#1241);
* J7 (locked, well inside the board, declared on the north edge it does not
  sit on) is pinned for a finding that is not off-board copper, and the run
  exits 0 -- the pinned-part contract;
* R3 still exits 4, named in `unrepairable_failed_refs` -- the gate holds for
  what this tool could have moved;
* H9 is still a violator -- a drill off the outline is not dropped.

    python3 tests/test_1193_repair_pad_copper_census.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ('py_router', 'py_placer', 'py_tools'):
    sys.path.insert(0, os.path.join(ROOT, _d))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_utils import check, evidence, tool    # noqa: E402

RUN_ALL_TIMEOUT = 600

SEED = tool('place_seed.py')
FLOORPLAN = tool('check_floorplan.py')

NETS = {1: '/A', 2: '/B', 3: '/C'}


def _res(ref, x, y, uid):
    return ('  (footprint "R:R_0603" (layer "F.Cu") (uuid "%s") (at %s %s 0)\n'
            '    (property "Reference" "%s" (at 0 -1.2 0) (layer "F.SilkS"))\n'
            '    (fp_rect (start -1.5 -0.7) (end 1.5 0.7) (stroke (width 0.05) '
            '(type solid)) (fill none) (layer "F.CrtYd"))\n'
            '    (pad "1" smd rect (at -0.8 0) (size 0.8 0.9) '
            '(layers "F.Cu" "F.Mask" "F.Paste") (net 1 "/A"))\n'
            '    (pad "2" smd rect (at 0.8 0) (size 0.8 0.9) '
            '(layers "F.Cu" "F.Mask" "F.Paste") (net 2 "/B")))\n'
            % (uid, x, y, ref))


def _locked_pair(ref, x, y, p1, p2, uid):
    """A (locked yes) two-pad part, 0.8 mm square pads at local p1 / p2."""
    return ('  (footprint "C:Conn2" (locked yes) (layer "F.Cu") (uuid "%s") '
            '(at %s %s 0)\n'
            '    (property "Reference" "%s" (at 0 -3 0) (layer "F.SilkS"))\n'
            '    (pad "1" smd rect (at %s %s) (size 0.8 0.8) '
            '(layers "F.Cu" "F.Mask" "F.Paste") (net 1 "/A"))\n'
            '    (pad "2" smd rect (at %s %s) (size 0.8 0.8) '
            '(layers "F.Cu" "F.Mask" "F.Paste") (net 3 "/C")))\n'
            % (uid, x, y, ref, p1[0], p1[1], p2[0], p2[1]))


def _hole(ref, x, y, uid):
    return ('  (footprint "MountingHole:Hole_0.8" (layer "F.Cu") (uuid "%s") '
            '(at %s %s 0)\n'
            '    (property "Reference" "%s" (at 0 -2 0) (layer "F.SilkS"))\n'
            '    (pad "" np_thru_hole circle (at 0 0) (size 0.8 0.8) '
            '(drill 0.8) (layers "*.Cu" "*.Mask")))\n' % (uid, x, y, ref))


def board_text(extra=()):
    pts = [(0, 0), (18, 0), (18, 1), (22, 1), (22, 0), (40, 0), (40, 30),
           (0, 30)]
    edges = ''.join(
        '  (gr_line (start %s %s) (end %s %s) (stroke (width 0.1) '
        '(type solid)) (layer "Edge.Cuts"))\n' % (a + b)
        for a, b in zip(pts, pts[1:] + pts[:1]))
    # J5 at (22, 2): pad 1 spans y 1.6..2.4 (below the notch floor, y 1),
    # pad 2 spans x 22.6..23.4 (east of the notch wall, x 22); their union
    # box's corner (20.6, 0.6) is in the notch.
    parts = [_locked_pair('J5', 22, 2, (-1, 0), (1, -1), 'u5'),
             _res('R1', 10, 15, 'u1'), _res('R2', 30, 15, 'u2')]
    parts += list(extra)
    nets = ''.join(' (net %d "%s")\n' % kv for kv in NETS.items())
    return ('(kicad_pcb (version 20240108) (generator "t")\n'
            ' (general (thickness 1.6))\n'
            ' (layers (0 "F.Cu" signal) (31 "B.Cu" signal)\n'
            '  (44 "Edge.Cuts" user) (37 "F.SilkS" user) (39 "F.Mask" user)\n'
            '  (35 "F.Paste" user) (49 "F.Fab" user) (47 "F.CrtYd" user))\n'
            ' (setup)\n (net 0 "")\n' + nets + edges + ''.join(parts) + ')\n')


#: J6: a locked part whose pad 1 (18.4..19.2, 0.1..0.9) is IN the notch.
J6 = _locked_pair('J6', 18.8, 3, (0, -2.5), (0, 2.5), 'u6')
#: R3: free, pad 1 (19.2 +- 0.4, 0.5 +- 0.45) in the notch.
R3 = _res('R3', 20.0, 0.5, 'u7')
#: H9: free, a pad-less 0.8 mm NPTH in the notch.
H9 = _hole('H9', 21.0, 0.5, 'u9')
#: J7: locked, well inside the board (pads at y 22, 8 mm from the south edge).
J7 = _locked_pair('J7', 10, 22, (-1, 0), (1, 0), 'u7a')


def _stage(d, extra=(), edge_claims=()):
    """(board, intent): the board, and an intent emitted off it with the
    emitter's own edge_connectors replaced -- it reads the notch-side parts
    as north edge connectors with a band that exempts their overhang, which
    StickHub's J5 was not."""
    board = os.path.join(d, 'notch.kicad_pcb')
    with open(board, 'w', encoding='utf-8') as f:
        f.write(board_text(extra))
    intent = os.path.join(d, 'intent.json')
    r = subprocess.run([sys.executable, '-X', 'utf8', FLOORPLAN, board,
                        '--emit-intent', intent], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-400:] + r.stderr[-400:]
    with open(evidence(intent, 'emitted intent'), encoding='utf-8') as f:
        doc = json.load(f)
    doc['edge_connectors'] = list(edge_claims)
    with open(intent, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1)
    return evidence(board, 'notch board'), intent


def _summary(r):
    rows = [ln for ln in r.stdout.splitlines() if ln.startswith('JSON_SUMMARY')]
    assert len(rows) == 1, (r.returncode, r.stdout[-600:], r.stderr[-600:])
    return json.loads(rows[0].split(':', 1)[1])


def test_the_premise_box_in_the_notch_no_pad():
    """Without this the J5 case below would pass vacuously."""
    from kicad_parser import parse_kicad_pcb
    from placement import legality as L
    with tempfile.TemporaryDirectory() as d:
        board, _ = _stage(d)
        pcb = parse_kicad_pcb(board)
    gate = L.BoardOutlineGate(pcb.board_info, 0.0)
    fp = pcb.footprints['J5']
    pp = L.build_part_pads(pcb.footprints, 0.2)['J5']
    box = gate.rect_outside_amount(pp.extent(fp.x, fp.y, fp.rotation or 0))
    per_pad = L.pad_copper_outside_amount(pp, fp.x, fp.y, fp.rotation or 0,
                                          gate)
    assert box > 0.1, box
    assert per_pad == 0.0, per_pad
    g = L.grade_pad_legality(pcb, 0.2, pcb_file=board)
    assert not g['oob_pad_copper_refs'], g['oob_pad_copper_refs']
    print('  PASS: J5 union box %.3f past the outline, per pad %.3f' % (box,
                                                                        per_pad))


def test_a_box_corner_in_a_notch_is_not_a_violator():
    with tempfile.TemporaryDirectory() as d:
        board, intent = _stage(d)
        r = check([sys.executable, '-X', 'utf8', SEED, board,
                   os.path.join(d, 'out.kicad_pcb'), '--intent', intent,
                   '--repair'], accept=True)
    s = _summary(r)
    assert s['violators'] == 0 and s['unrepairable'] == 0, s
    assert s['unrepairable_refs'] == [], s
    assert 'J5 violates' not in r.stdout, r.stdout[-600:]
    print('  PASS: J5 not charged, exit 0')


def test_a_locked_off_board_violator_is_pinned_named_and_fails():
    with tempfile.TemporaryDirectory() as d:
        board, intent = _stage(d, extra=(J6,))
        from kicad_parser import parse_kicad_pcb
        from placement.legality import grade_pad_legality
        g = grade_pad_legality(parse_kicad_pcb(board), 0.2, pcb_file=board)
        assert 'J6' in g['oob_pad_copper_gating_refs'], g  # really off
        r = check([sys.executable, '-X', 'utf8', SEED, board,
                   os.path.join(d, 'out.kicad_pcb'), '--intent', intent,
                   '--repair'], refuse='a lock is no waiver', code=4)
    s = _summary(r)
    assert s['unrepairable_pinned_refs'] == ['J6'], s
    assert s['unrepairable_pinned_off_board_refs'] == ['J6'], s
    assert s['unrepairable_failed_refs'] == [], s
    assert s['unrepairable_refs'] == ['J6'] and s['unrepairable'] == 1, s
    assert '1 pinned: J6' in r.stdout, r.stdout[-600:]
    print('  PASS: J6 pinned, named, and off the board -> exit 4')


def test_a_locked_violator_on_the_board_is_pinned_and_not_a_failure():
    with tempfile.TemporaryDirectory() as d:
        board, intent = _stage(
            d, extra=(J7,),
            edge_claims=[{'ref': 'J7', 'edge': 'north',
                          'class': 'edge_receptacle'}])
        r = check([sys.executable, '-X', 'utf8', SEED, board,
                   os.path.join(d, 'out.kicad_pcb'), '--intent', intent,
                   '--repair'], accept=True)
    s = _summary(r)
    assert s['unrepairable_pinned_refs'], s     # the premise: pinned
    assert s['unrepairable_pinned_refs'] == ['J7'], s
    assert s['unrepairable_pinned_off_board_refs'] == [], s
    assert s['unrepairable_failed_refs'] == [], s
    assert 'not a gate failure' in r.stdout, r.stdout[-600:]
    print('  PASS: J7 pinned on the board, exit 0')


def test_a_free_violator_it_cannot_seat_still_exits_4():
    with tempfile.TemporaryDirectory() as d:
        board, intent = _stage(
            d, extra=(J6, R3),
            edge_claims=[{'ref': 'R3', 'class': 'edge_receptacle'}])
        r = check([sys.executable, '-X', 'utf8', SEED, board,
                   os.path.join(d, 'out.kicad_pcb'), '--intent', intent,
                   '--repair'], refuse='no edge is declared', code=4)
    s = _summary(r)
    assert s['unrepairable_failed_refs'] == ['R3'], s
    assert s['unrepairable_pinned_refs'] == ['J6'], s
    print('  PASS: R3 failed -> exit 4; J6 still listed as pinned')


def test_a_drill_off_the_outline_is_still_charged():
    from kicad_parser import parse_kicad_pcb
    from placement import floorplan, seeder
    with tempfile.TemporaryDirectory() as d:
        board, intent = _stage(d, extra=(H9,))
        res = seeder.repair_placement(parse_kicad_pcb(board), board,
                                      floorplan.load_intent(intent),
                                      clearance=0.2)
    assert 'H9' in res['violators'], res['violators']
    assert 'J5' not in res['violators'] + res['unrepairable'], res
    print('  PASS: the pad-less NPTH in the notch is a violator (%s)'
          % ('repaired' if 'H9' in res['repaired'] else 'charged'))


TESTS = [
    test_the_premise_box_in_the_notch_no_pad,
    test_a_box_corner_in_a_notch_is_not_a_violator,
    test_a_locked_off_board_violator_is_pinned_named_and_fails,
    test_a_locked_violator_on_the_board_is_pinned_and_not_a_failure,
    test_a_free_violator_it_cannot_seat_still_exits_4,
    test_a_drill_off_the_outline_is_still_charged,
]


if __name__ == '__main__':
    only = sys.argv[1:]
    ran = 0
    for t in TESTS:
        if only and not any(o in t.__name__ for o in only):
            continue
        print(f"--- {t.__name__}")
        t()
        ran += 1
    if only and not ran:
        print(f"NO TEST matches {only}")
        sys.exit(2)
    print('ALL PASS')
