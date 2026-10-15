#!/usr/bin/env python3
"""#858: place_route_loop rejects a round whose candidate lays NEW pad copper
on another part's pad, before routing it, and says so.

The loop's accept criterion scored only the ROUTE -- `better()` compares
failures then iterations, `--accept-cmd` a judge's SCORE -- and an unrouted
short is invisible to both: the issue's run 1 was ACCEPTED at failures
96 -> 87 with U31.6 [TELEM_5V] on Y1.3 [/CAN_XTAL2]. Now every candidate is
measured on the WRITTEN board (after any relocation and the quench) with
check_assembly's own BLOCKING channel, `legality.pad_intersection_pairs`, and
a pair the incumbent did not have rejects the round: no route, no judge, a
`pad_stacks_new` record in the round sidecar and `rounds_rejected_pad_stacks`
in the verdict.

Nothing is routed. The router, the quench and the judge are recorders, as in
test_554_loop_relocate.py. Pinned:
  * `new_pad_stacks` reports the new pair only -- a stack the board ships
    with (C2/C3, same net) is the incumbent's -- and names the short's nets;
  * a quench that drops R1 [NB] onto C1 [NA] is rejected before routing,
    even when the judge would have scored it better, and the output is the
    input board;
  * the control: the same loop with a clean move routes and accepts, and
    writes no `pad_stacks_new` key.

Run: python3 -X utf8 tests/test_858_loop_pad_stack_gate.py
"""
import json
import os
import shutil
import sys
import tempfile
from io import StringIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _d in ('py_placer', 'py_router', 'py_tools'):
    sys.path.insert(0, os.path.join(ROOT, _d))

import place_route_loop as prl                    # noqa: E402
from kicad_parser import parse_kicad_pcb          # noqa: E402


def _fp(ref, x, net, name):
    return (f'\t(footprint "test:P1"\n\t\t(layer "F.Cu")\n'
            f'\t\t(uuid "fp-{ref}")\n\t\t(at {x} 100)\n'
            f'\t\t(property "Reference" "{ref}"\n\t\t\t(at 0 0)\n\t\t)\n'
            f'\t\t(pad "1" smd rect\n\t\t\t(at -0.5 0)\n\t\t\t(size 0.8 0.8)\n'
            f'\t\t\t(layers "F.Cu")\n\t\t\t(net {net} "{name}")\n'
            f'\t\t\t(uuid "p-{ref}")\n\t\t)\n\t)\n')


def _board(td):
    """C1 [NA] and R1 [NB] apart; C2/C3 [NA] already stacked (same net), the
    kind of stack a board can ship with on purpose."""
    body = ('(kicad_pcb\n\t(version 20241229)\n\t(net 0 "")\n'
            '\t(net 1 "NA")\n\t(net 2 "NB")\n'
            '\t(gr_rect\n\t\t(start 100 80)\n\t\t(end 200 120)\n'
            '\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type solid)\n\t\t)\n'
            '\t\t(layer "Edge.Cuts")\n\t\t(uuid "edge1")\n\t)\n')
    body += (_fp('C1', 140, 1, 'NA') + _fp('R1', 160, 2, 'NB')
             + _fp('C2', 120, 1, 'NA') + _fp('C3', 120.3, 1, 'NA') + ')\n')
    path = os.path.join(td, 'in.kicad_pcb')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(body)
    return path


def _run(r1_x, td, judge=False):
    """main() with the router, the quench and (optionally) the judge
    recorded. The quench moves R1 to `r1_x`; every route 'improves'."""
    routes, judged = [], []

    def fake_quench(pcb_data, **kw):
        return [{'reference': 'R1', 'new_x': r1_x, 'new_y': 100.0,
                 'new_rotation': 0.0}]

    def fake_run_route(pcb_file, routed_file, route_args, log_file, **kw):
        routes.append(os.path.basename(pcb_file))
        n = 2 if len(routes) == 1 else 1
        return {'failures': n, 'failed_nets': ['NB'], 'blockers': [],
                'iterations': 1000, 'vias': 0, 'blocker_report': None}

    def fake_accept_score(cmd, placed, routed, json_file):
        judged.append(os.path.basename(placed))
        return -float(len(judged)), 'recorded judge: always better'

    board = _board(td)
    work = os.path.join(td, 'work')
    out = os.path.join(td, 'out.kicad_pcb')
    saved = (prl.quench, prl.run_route, prl.accept_score, sys.argv,
             sys.stdout)
    prl.quench, prl.run_route = fake_quench, fake_run_route
    prl.accept_score = fake_accept_score
    sys.argv = (['place_route_loop.py', board, out, '--route-args',
                 '--nets "*"', '--rounds', '1', '--work-dir', work,
                 '--no-movie']
                + (['--accept-cmd', 'recorded'] if judge else []))
    buf = StringIO()
    sys.stdout = buf
    try:
        prl.main()
    finally:
        (prl.quench, prl.run_route, prl.accept_score, sys.argv,
         sys.stdout) = saved
    with open(os.path.join(work, 'loop_round1.json'), encoding='utf-8') as f:
        side = json.load(f)
    summary = {}
    for line in buf.getvalue().splitlines():
        if line.startswith('JSON_SUMMARY: '):
            summary = json.loads(line[len('JSON_SUMMARY: '):])
    r1 = parse_kicad_pcb(out).footprints['R1']
    return dict(routes=routes, judged=judged, side=side, summary=summary,
                out_r1=(r1.x, r1.y), stdout=buf.getvalue())


def test_only_a_new_stack_is_reported_and_its_short_named():
    with tempfile.TemporaryDirectory() as td:
        board = _board(td)
        inc = prl.pad_stack_pairs(parse_kicad_pcb(board), 0.2)
        assert set(inc) == {('C2', 'C3')}, sorted(inc)
        moved = os.path.join(td, 'moved.kicad_pcb')
        from placement.writer import write_placed_output
        write_placed_output(board, moved, [{'reference': 'R1', 'new_x': 140.0,
                                            'new_y': 100.0,
                                            'new_rotation': 0.0}])
        cand = prl.pad_stack_pairs(parse_kicad_pcb(moved), 0.2)
    new = prl.new_pad_stacks(inc, cand)
    assert [(s['a'], s['b']) for s in new] == [('C1', 'R1')], new
    # The channel's own label: ref.pad:net-id, so net 1 (NA) on net 2 (NB).
    assert new[0]['shorts'] == ['C1.1:1 <-> R1.1:2'], new
    assert prl.new_pad_stacks(inc, inc) == [], 'the incumbent made nothing'
    print(f"  PASS: new {new}; the shipped C2/C3 stack is not new")


def test_a_round_that_shorts_is_rejected_before_routing():
    with tempfile.TemporaryDirectory() as td:
        got = _run(140.0, td, judge=True)
    side, s = got['side'], got['summary']
    assert got['routes'] == ['loop_round0.kicad_pcb'], got['routes']
    assert got['judged'] == ['loop_round0.kicad_pcb'], (
        'the judge was asked about a candidate the gate refuses', got['judged'])
    assert side['accepted'] is False and side['routed'] is None, side
    rows = side.get('pad_stacks_new') or []
    assert [(r['a'], r['b']) for r in rows] == [('C1', 'R1')], rows
    assert rows[0]['shorts'], rows
    assert s['rounds_rejected_pad_stacks'] == 1, s
    assert s['rounds_accepted'] == 0, s
    assert got['out_r1'] == (160.0, 100.0), got['out_r1']
    assert 'REJECTED before routing' in got['stdout'], got['stdout'][-600:]
    print(f"  PASS: rejected unrouted and unjudged; sidecar {rows}")


def test_a_clean_round_routes_and_is_accepted():
    with tempfile.TemporaryDirectory() as td:
        got = _run(161.0, td)
    side, s = got['side'], got['summary']
    assert got['routes'] == ['loop_round0.kicad_pcb',
                             'loop_round1.kicad_pcb'], got['routes']
    assert side['accepted'] is True, side
    assert 'pad_stacks_new' not in side, sorted(side)
    assert s['rounds_rejected_pad_stacks'] == 0, s
    assert got['out_r1'] == (161.0, 100.0), got['out_r1']
    print("  PASS: the clean move routes, is accepted, and records nothing")


def main():
    names = sys.argv[1:]
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)
             and (not names or any(x in n for x in names))]
    failed = 0
    for n, f in tests:
        print(n)
        try:
            f()
        except Exception as exc:                            # noqa: BLE001
            failed += 1
            print(f"  FAIL: {exc.__class__.__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
