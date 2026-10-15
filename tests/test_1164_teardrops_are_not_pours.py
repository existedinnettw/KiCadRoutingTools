#!/usr/bin/env python3
"""A teardrop zone is copper, not a POUR: no net is a plane net for one (#1164).

KiCad generates a teardrop area where a track meets a pad or via, as a zone
carrying `(attr (teardrop ...))`. #1164 gave `Zone` a `teardrop` flag on both
parse paths. Every consumer that decides "this net is a plane / poured net"
from `pcb.zones` read a teardrop as a pour, so on a teardropped board:

  * `route.py --rip-existing-nets` never offered a teardropped signal net for
    rip-up ("plane nets belong to route_planes"), and --force-reroute, the
    pre-existing rip candidacy, tap relocation and the plane finalize read it
    as a plane too;
  * a GND teardrop on F.Cu read as "GND already pours on F.Cu", so
    route_planes kept the pour it was asked to create (`check_existing_zones`
    over `plane_io.extract_zones`);
  * board_brief listed the teardropped net among the board's pours.

`kicad_parser.pour_zones` is the one answer those consumers now ask. The
teardrop stays copper for the obstacle map, DRC and connectivity, which still
read `pcb.zones`.

Every check runs the SAME injected zone twice: once as a teardrop and once
with the `(attr (teardrop ...))` removed (an ordinary zone, the control), so
the attr is what moves each answer.

    python3 tests/test_1164_teardrops_are_not_pours.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ('py_router', 'py_placer', 'py_tools', 'tests'):
    sys.path.insert(0, os.path.join(ROOT, _d))

import run_utils                                              # noqa: E402
from kicad_parser import parse_kicad_pcb, pour_zones          # noqa: E402

BOARD = run_utils.evidence(os.path.join(ROOT, 'kicad_files',
                                        'splitflap_driver.kicad_pcb'))
ROUTE = os.path.join(ROOT, 'py_router', 'route.py')
PY = [sys.executable, '-X', 'utf8']
NET = '/OUT_A_PHASE_A'          # J3.1 <-> U2.11, a 2-pad signal net
failures = []


def check(label, ok, detail=''):
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f" ({detail})" if detail else ''))
    if not ok:
        failures.append(label)


def _zone(net, x, y, teardrop, uid):
    """A small triangle at (x, y) on F.Cu; KiCad 10's teardrop spelling."""
    pts = '(xy %.4f %.4f) (xy %.4f %.4f) (xy %.4f %.4f)' % (
        x, y, x + 1.0, y + 0.3, x + 1.0, y - 0.3)
    attr = ('\t\t(attr\n\t\t\t(teardrop\n\t\t\t\t(type padvia)\n\t\t\t)\n\t\t)\n'
            if teardrop else '')
    return ('\t(zone\n\t\t(net "%s")\n\t\t(layer "F.Cu")\n\t\t(uuid "%s")\n'
            '\t\t(name "$teardrop_padvia$")\n\t\t(hatch full 0.1)\n'
            '\t\t(priority 30000)\n%s'
            '\t\t(connect_pads yes\n\t\t\t(clearance 0)\n\t\t)\n'
            '\t\t(min_thickness 0.0254)\n\t\t(filled_areas_thickness no)\n'
            '\t\t(fill yes\n\t\t\t(thermal_gap 0.5)\n'
            '\t\t\t(thermal_bridge_width 0.5)\n\t\t)\n'
            '\t\t(polygon\n\t\t\t(pts\n\t\t\t\t%s\n\t\t\t)\n\t\t)\n'
            '\t\t(filled_polygon\n\t\t\t(layer "F.Cu")\n\t\t\t(pts\n'
            '\t\t\t\t%s\n\t\t\t)\n\t\t)\n\t)\n' % (net, uid, attr, pts, pts))


def _inject(src, dst, zones):
    """`src` with `zones` inserted before the board's closing paren, plus the
    sibling .kicad_pro copied (CLAUDE.md #441: never a bare board copy)."""
    with open(src, encoding='utf-8', newline='') as fh:
        text = fh.read()
    cut = text.rstrip().rfind(')')
    with open(dst, 'w', encoding='utf-8', newline='') as fh:
        fh.write(text[:cut] + ''.join(zones) + text[cut:])
    pro = os.path.splitext(src)[0] + '.kicad_pro'
    if os.path.isfile(pro):
        with open(pro, encoding='utf-8') as a, \
                open(os.path.splitext(dst)[0] + '.kicad_pro', 'w',
                     encoding='utf-8') as b:
            b.write(a.read())


def _route(board, out, extra, td):
    js = os.path.join(td, os.path.basename(out) + '.json')
    r = run_utils.check(PY + [ROUTE, board, out, '--json-out', js] + extra,
                        accept=True)
    return r.stdout, (json.load(open(js, encoding='utf-8'))
                      if os.path.isfile(js) else {})


def main():
    from plane_io import extract_zones, check_existing_zones
    from tap_relocation import plane_zone_net_ids
    import board_brief

    with tempfile.TemporaryDirectory() as td:
        # 0. Commit copper on NET, then put a "teardrop" on its U2.11 pad.
        r1 = os.path.join(td, 'r1.kicad_pcb')
        _out, _js = _route(BOARD, r1, ['--nets', NET], td)
        pcb1 = parse_kicad_pcb(run_utils.evidence(r1))
        nid = next(i for i, n in pcb1.nets.items() if n.name == NET)
        check('setup: NET routed copper',
              sum(1 for s in pcb1.segments if s.net_id == nid) > 0)
        boards = {}
        for tag, td_flag in (('teardrop', True), ('zone', False)):
            p = os.path.join(td, f'{tag}.kicad_pcb')
            _inject(r1, p, [_zone(NET, 196.22, 34.35, td_flag,
                                  'eeeeeeee-0000-4000-8000-000000000001'),
                            _zone('GND', 162.56, 38.59, td_flag,
                                  'eeeeeeee-0000-4000-8000-000000000002')])
            boards[tag] = p
        pcbs = {k: parse_kicad_pcb(v) for k, v in boards.items()}
        gid = next(i for i, n in pcbs['teardrop'].nets.items() if n.name == 'GND')

        # 1. the parse flags them, and pour_zones drops only the teardrops
        td_z = [z for z in pcbs['teardrop'].zones if z.teardrop]
        check('the parser flags both injected teardrops', len(td_z) == 2,
              len(td_z))
        check('control: the same zones without the attr are not teardrops',
              not any(z.teardrop for z in pcbs['zone'].zones))
        check('pour_zones: no teardrop is a pour',
              pour_zones(pcbs['teardrop'].zones) == [])
        check('pour_zones: an ordinary zone is', len(pour_zones(pcbs['zone'].zones)) == 2)
        check('teardrops are still COPPER in pcb.zones (obstacles, DRC)',
              len(pcbs['teardrop'].zones) == 2)

        # 2. route.py --rip-existing-nets: a teardropped signal net is
        #    rip-eligible; the same net with an ordinary zone is a plane net.
        line = f'pre-existing net(s) eligible for rip-up: {NET}'
        out_td, _ = _route(boards['teardrop'], os.path.join(td, 'r_td.kicad_pcb'),
                           ['--nets', '/OUT_A_PHASE_B',
                            '--rip-existing-nets', NET], td)
        check('rip-existing: the teardropped net is eligible', line in out_td,
              [ln for ln in out_td.splitlines() if 'eligible' in ln][:2])
        out_z, _ = _route(boards['zone'], os.path.join(td, 'r_z.kicad_pcb'),
                          ['--nets', '/OUT_A_PHASE_B',
                           '--rip-existing-nets', NET], td)
        check('control: with a POUR on it the net stays route_planes\'s',
              line not in out_z,
              [ln for ln in out_z.splitlines() if 'eligible' in ln][:2])

        # 3. route_planes' existing-pour model: a GND teardrop on F.Cu is not
        #    "GND already pours on F.Cu"
        #    (`zone_to_replace` is the incumbent same-net pour: route_planes
        #    replaces it, or under skip_existing_zones keeps it INSTEAD of
        #    the plane asked for -- so a teardrop found here was deleted, or
        #    stood in for the plane.)
        zi = extract_zones(boards['teardrop'])
        check('plane_io.extract_zones lists no teardrop', zi == [], zi)
        create, cont, replace, _f = check_existing_zones(zi, 'F.Cu', 'GND', gid)
        check('check_existing_zones: GND on F.Cu is a plain create, no '
              'incumbent', create and cont and replace is None,
              (create, cont, replace))
        zi_c = extract_zones(boards['zone'])
        _cr, _c, replace_c, _f = check_existing_zones(zi_c, 'F.Cu', 'GND', gid)
        check('control: an ordinary GND zone on F.Cu IS the incumbent pour',
              replace_c is not None and replace_c.net_name == 'GND'
              and len(zi_c) == 2, (replace_c, len(zi_c)))

        # 4. tap relocation's "repairable from the pour" nets
        check('plane_zone_net_ids: no teardropped net',
              plane_zone_net_ids(pcbs['teardrop']) == set())
        check('control: both zoned nets with ordinary zones',
              plane_zone_net_ids(pcbs['zone']) == {nid, gid})

        # 5. board_brief: the teardropped net is not listed as poured
        pc = board_brief.pour_census(pcbs['teardrop'], boards['teardrop'])
        check('board_brief: no pour, two teardrop areas counted',
              pc['pours'] == [] and pc['teardrop_areas'] == 2,
              (pc['pours'], pc.get('teardrop_areas')))
        pc_c = board_brief.pour_census(pcbs['zone'], boards['zone'])
        check('control: ordinary zones are listed as pours',
              sorted(e['net'] for e in pc_c['pours']) == sorted(['GND', NET])
              and pc_c['teardrop_areas'] == 0,
              [e['net'] for e in pc_c['pours']])

    if failures:
        print(f"\nFAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: a teardrop is copper, never a pour")
    return 0


if __name__ == '__main__':
    sys.exit(main())
