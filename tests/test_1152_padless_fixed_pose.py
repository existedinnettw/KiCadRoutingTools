#!/usr/bin/env python3
"""#1152: a pad-less footprint's declared fixed pose is SEATED by its
courtyard, and a stage-0 refusal names the lock's real cause.

The quench enters a footprint with no pad at all (a logo or a mechanical
outline that draws a courtyard) LOCKED, as a static obstacle -- a policy, not
a pose anybody fixed. The seeder puts every locked part in `placed`, and stage
0 then refused the part's `fixed_poses[]` entry as "already placed ... locked
in the board file" on a file that locks nothing, at every pose but the one it
already had. The "no copper pad or hole" branch of `_fixed_pose_check`, which
exists to judge exactly such a part by its courtyard, was never reached.

Now `QuenchState.lock_reasons` records why each part entered locked; stage 0
checks a declared pose for a part whose only lock is the pad-less policy like
any free part's, and every other refusal names the recorded cause.

Pinned here, on a synthetic 20 x 20 mm board:
  * the pad-less G1, unlocked in the file, seats where it is declared, is
    locked by the seed and written there;
  * declared 1.5 mm past the outline it is refused by its COURTYARD at zero
    margin, never as "locked in the board file";
  * declared onto a file-locked part's courtyard it is refused for the overlap;
  * the same G1 locked in the file is refused as locked in the board file, and
    a pad-less or free part outside `seed_refs` as outside the seed scope;
  * the state records LOCK_PADLESS / LOCK_FILE as the causes.

Run: python3 -X utf8 tests/test_1152_padless_fixed_pose.py
"""
import contextlib
import io
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ('py_router', 'py_placer', 'py_tools'):
    sys.path.insert(0, os.path.join(ROOT, _d))

from kicad_parser import parse_kicad_pcb           # noqa: E402
from placement import floorplan as fp              # noqa: E402
from placement import quench as q                  # noqa: E402
from placement import seeder                       # noqa: E402

#: G1: a courtyard and NO pad (the issue's case). R1: an ordinary free part.
#: R9: locked in the file, so stage 0 has a placed courtyard to collide with.
BOARD = '''(kicad_pcb
 (version 20241229)
 (net 0 "") (net 1 "/A")
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal))
 (gr_rect (start 0 0) (end 20 20) (layer "Edge.Cuts") (uuid "e1"))
 (footprint "t:LOGO" (layer "F.Cu") %(g1_lock)s(uuid "fp-G1") (at 10 10)
  (property "Reference" "G1" (at 0 0 0))
  (fp_rect (start -2 -2) (end 2 2) (layer "F.CrtYd") (uuid "c1")))
 (footprint "t:R" (layer "F.Cu") (uuid "fp-R1") (at 5 5)
  (property "Reference" "R1" (at 0 0 0))
  (fp_rect (start -0.8 -0.5) (end 0.8 0.5) (layer "F.CrtYd") (uuid "c2"))
  (pad "1" smd rect (at -0.4 0) (size 0.4 0.5) (layers "F.Cu") (net 1 "/A") (uuid "r1")))
 (footprint "t:R" (layer "F.Cu") (locked yes) (uuid "fp-R9") (at 15 15)
  (property "Reference" "R9" (at 0 0 0))
  (fp_rect (start -0.8 -0.5) (end 0.8 0.5) (layer "F.CrtYd") (uuid "c3"))
  (pad "1" smd rect (at -0.4 0) (size 0.4 0.5) (layers "F.Cu") (net 1 "/A") (uuid "r9")))
)
'''


def _board(td, g1_locked=False):
    path = os.path.join(td, 'b%d.kicad_pcb' % int(g1_locked))
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(BOARD % {'g1_lock': '(locked yes) ' if g1_locked else ''})
    return path


def _seed(path, poses, **kw):
    doc = {'schema': 1, 'kind': fp.KIND, 'units': 'mm',
           'fixed_poses': [dict(p, rot=0, basis='declared') for p in poses]}
    with contextlib.redirect_stdout(io.StringIO()):
        return seeder.seed_from_intent(
            parse_kicad_pcb(path), path, fp.intent_from_dict(doc, path),
            random.Random('0'), group_sources=(), clearance=0.2,
            board_edge_clearance=0.1, **kw)


def test_the_state_records_why_each_part_is_locked():
    with tempfile.TemporaryDirectory() as td:
        path = _board(td)
        with contextlib.redirect_stdout(io.StringIO()):
            st = q.QuenchState(parse_kicad_pcb(path), path, 0.2, 0.1, 30.0,
                               0.5, 0.15, 2.0, 2.0, 2.0, 0.1, 0.3)
    assert st.parts['G1'].locked, 'the pad-less part is still an obstacle'
    assert st.lock_reasons == {'G1': q.LOCK_PADLESS, 'R9': q.LOCK_FILE}, \
        st.lock_reasons
    print(f"  PASS: lock_reasons {st.lock_reasons}")


def test_a_padless_fixed_pose_is_seated_where_declared():
    with tempfile.TemporaryDirectory() as td:
        res = _seed(_board(td), [{'ref': 'G1', 'x': 13.0, 'y': 10.0}])
    assert not res['fixed_refused'], res['fixed_refused']
    rec = res['fixed_seated']['G1']
    assert rec['how'] == 'contained', rec
    assert 'G1' in res['lock_refs'], res['lock_refs']
    row = [p for p in res['placements'] if p['reference'] == 'G1']
    assert row and (row[0]['new_x'], row[0]['new_y']) == (13.0, 10.0), row
    # Declared where it already is: recorded as before #1152, unchecked and
    # unstamped (a mechanical.json part keeps its pose on a staged pile).
    with tempfile.TemporaryDirectory() as td:
        same = _seed(_board(td), [{'ref': 'G1', 'x': 10.0, 'y': 10.0}])
    assert same['fixed_seated']['G1']['how'] == 'already_there', same
    assert 'G1' not in same['lock_refs'], same['lock_refs']
    print(f"  PASS: G1 seated {rec['how']} at (13, 10) and stamped locked; "
          f"at its own pose, already_there as before")


def test_past_the_outline_it_is_refused_by_its_courtyard():
    with tempfile.TemporaryDirectory() as td:
        res = _seed(_board(td), [{'ref': 'G1', 'x': 19.5, 'y': 10.0}])
    why = (res['fixed_refused'].get('G1') or {}).get('reason', '')
    assert 'no copper pad or hole to anchor an overhang' in why, why
    assert 'locked in the board file' not in why, why
    assert 'G1' in res['unseated'], res['unseated']
    print(f"  PASS: {why}")


def test_onto_a_locked_part_it_is_refused_for_the_overlap():
    with tempfile.TemporaryDirectory() as td:
        res = _seed(_board(td), [{'ref': 'G1', 'x': 15.0, 'y': 14.0}])
    why = (res['fixed_refused'].get('G1') or {}).get('reason', '')
    assert 'courtyard overlaps R9' in why, why
    print(f"  PASS: {why}")


def test_a_real_lock_or_scope_is_still_named_truly():
    with tempfile.TemporaryDirectory() as td:
        locked = _seed(_board(td, g1_locked=True),
                       [{'ref': 'G1', 'x': 13.0, 'y': 10.0}])
        scoped = _seed(_board(td), [{'ref': 'G1', 'x': 13.0, 'y': 10.0},
                                    {'ref': 'R1', 'x': 4.0, 'y': 4.0}],
                       seed_refs={'R9'})
    why = locked['fixed_refused']['G1']['reason']
    assert why.endswith("-- locked in the board file, and not this stage's "
                        "to move"), why
    for ref in ('G1', 'R1'):
        w = scoped['fixed_refused'][ref]['reason']
        assert "-- outside the seed scope, and not this stage's" in w, (ref, w)
    print(f"  PASS: file lock -> {why!r}; out of scope -> "
          f"{scoped['fixed_refused']['G1']['reason']!r}")


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
