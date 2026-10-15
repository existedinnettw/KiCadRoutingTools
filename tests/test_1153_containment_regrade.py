#!/usr/bin/env python3
"""#1153: `place_seed --repair` judges a zero-move containment violator by the
SAME census that charged it.

The repair charges a part for sitting inside another part's body from
`grade_body_overlap`'s containment pairs (check_assembly's grader). Its
zero-move honesty re-grade asked a different question -- the search's own
`QuenchState._body_contained_at`, fab rects only -- so on pristine esp_prog
R1, charged for sitting inside U2's SILK-drawn body (U2 draws no .Fab;
contained_frac 0.892), was reported `repaired` with 0 moves while the pair
stayed on the board. Now one selection (`seeder._repair_containment_pairs`)
serves both halves: the charge on the input poses, the re-grade on the board
at the repair's final poses -- for every ref charged for containment, moved
or not, on the pairs it was charged for.

Pinned here:
  * the anti-vacuity: esp_prog's census holds R1/U2 at 0.892 on a silk body,
    and the search's own measure does not see it;
  * R1 is charged, does not move, and is UNRESOLVED (not repaired), the note
    naming U2;
  * the re-grade reads the FINAL poses: when another mover takes U2 off R1
    during the repair, the same zero-move R1 is repaired;
  * a MOVE that leaves R1 inside U2's body (0.1 mm) is unresolved, one that
    takes it out (2 mm) is repaired, and a pair partner seated in R1's place
    without clearing the pair is unresolved too. The seat search is replaced
    by a stand-in for these, since its fab-rect currency never moves R1.

Run: python3 -X utf8 tests/test_1153_containment_regrade.py
"""
import contextlib
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ('py_router', 'py_placer', 'py_tools'):
    sys.path.insert(0, os.path.join(ROOT, _d))
sys.path.insert(0, os.path.join(ROOT, 'tests'))

from kicad_parser import parse_kicad_pcb           # noqa: E402
from placement import legality as leg              # noqa: E402
from placement import seeder                       # noqa: E402
from run_utils import evidence                     # noqa: E402

ESP = os.path.join(ROOT, 'kicad_files', 'esp_prog.kicad_pcb')


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


def _repair():
    return _quiet(lambda: seeder.repair_placement(
        parse_kicad_pcb(ESP), ESP, None, clearance=0.2))


def test_the_pair_is_real_and_the_search_cannot_see_it():
    evidence(ESP)
    pcb = _quiet(parse_kicad_pcb, ESP)
    body = _quiet(leg.grade_body_overlap, pcb, 0.2, pcb_file=ESP)
    pairs = [(q.a, q.b, q.contained_frac) for q in body['containment_pairs']
             if {q.a, q.b} == {'R1', 'U2'}]
    assert pairs and pairs[0][2] >= leg.CONTAINMENT_FRAC, pairs
    assert body['body_sources'].get('U2') == 'silk', body['body_sources']
    import pose_score
    st = _quiet(pose_score.make_state, pcb, ESP, clearance=0.2)
    assert not st._body_contained_at('R1', None, None, None), \
        'the search sees the pair now; this fixture no longer separates them'
    print(f"  PASS: census {pairs[0]}, U2 silk-drawn, search blind to it")


def test_a_zero_move_containment_violator_is_unresolved():
    res = _repair()
    assert 'R1' in res['violators'], res['violators']
    assert 'R1' not in [m['reference'] for m in res['moves']], res['moves']
    assert 'R1' not in res['repaired'], res['repaired']
    assert 'R1' in res['unresolved'], res['unresolved']
    note = [n for n in res['notes'] if n.startswith('R1: UNRESOLVED')]
    assert note and 'containment pair with U2' in note[0], res['notes']
    print(f"  PASS: {note[0]}")


def _repair_with(seat):
    """The repair with `_try_place` answered by `seat(state, ref, real, *a,
    **k)` -- a stand-in for a seat search whose move the test chooses."""
    real = seeder._try_place
    seeder._try_place = lambda state, ref, *a, **k: seat(state, ref, real,
                                                         *a, **k)
    try:
        return _repair()
    finally:
        seeder._try_place = real


def _shift(state, ref, dx):
    p = state.parts[ref]
    state.apply_move(ref, p.x + dx, p.y, p.rot)
    return state.clearance


def _moved(res):
    return [m['reference'] for m in res['moves']]


def test_the_regrade_reads_the_final_poses():
    """A pair another mover cleared during the repair is cleared: the re-grade
    grades the state's poses, not the input file's."""
    def other_mover_clears_it(state, ref, real, *a, **k):
        if ref == 'R1':
            _shift(state, 'U2', 25.0)
            return state.clearance          # R1 accepted where it stands
        return real(state, ref, *a, **k)

    res = _repair_with(other_mover_clears_it)
    assert 'R1' not in _moved(res), res['moves']
    assert 'R1' in res['repaired'], (res['repaired'], res['unresolved'],
                                     res['notes'])
    print("  PASS: U2 moved off it during the repair, so zero-move R1 is "
          "repaired")


def test_a_moved_violator_still_inside_is_unresolved():
    """A move the search accepts can leave the part in the body the census
    charged it for: R1 0.1 mm over is still 89% inside U2's silk body."""
    res = _repair_with(lambda st, ref, real, *a, **k: (
        _shift(st, 'R1', 0.1) if ref == 'R1' else real(st, ref, *a, **k)))
    assert 'R1' in _moved(res), res['moves']
    assert 'R1' not in res['repaired'], res['repaired']
    assert 'R1' in res['unresolved'], (res['unresolved'], res['notes'])
    note = [n for n in res['notes'] if n.startswith('R1: UNRESOLVED')]
    assert note and 'moved without clearing it' in note[0], res['notes']
    print(f"  PASS: {note[0]}")


def test_a_moved_violator_that_leaves_the_body_is_repaired():
    """The positive control for a move: 2 mm over, R1 is in no pair."""
    res = _repair_with(lambda st, ref, real, *a, **k: (
        _shift(st, 'R1', 2.0) if ref == 'R1' else real(st, ref, *a, **k)))
    assert 'R1' in _moved(res), res['moves']
    assert 'R1' in res['repaired'], (res['repaired'], res['unresolved'],
                                     res['notes'])
    print("  PASS: R1 moved 2 mm, out of U2's body, and is repaired")


def test_a_partner_seated_for_the_pair_is_regraded_on_it():
    """R1 finds no seat; its pair partner U2 is seated instead, 0.1 mm over,
    which clears nothing. U2 moved FOR R1's containment charge, so it is
    judged on that pair."""
    res = _repair_with(lambda st, ref, real, *a, **k: (
        None if ref == 'R1' else _shift(st, 'U2', 0.1) if ref == 'U2'
        else real(st, ref, *a, **k)))
    assert _moved(res) == ['U2'], res['moves']
    assert 'U2' not in res['repaired'], res['repaired']
    assert 'U2' in res['unresolved'], (res['unresolved'], res['notes'])
    print("  PASS: the seated partner U2 still holds R1 and is unresolved")


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
