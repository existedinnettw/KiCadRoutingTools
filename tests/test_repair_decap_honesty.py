#!/usr/bin/env python3
"""#1066: `place_seed --repair` reports a violator repaired only when the grade
error it was CHARGED for is gone.

The repair charges every intent grade error that names a ref (decap_distance,
decap_pin_distance, zones, ...), but `_try_place` searches for the nearest
LEGAL pose and has no intent target, so it accepts the part where it stands.
The run-7 honesty re-grade re-checked pads, holes and containment only, so
each intent-only violator was reported repaired with nothing moved -- glasgow
run 34: "33 violator(s), 33 repaired (0 moved)", floorplan errors 43 -> 43.

What each case pins (esp_prog, `decaps.max_distance_mm = 2.0`, which charges
C3 alone at the human pose: 2.125mm from U1):

* ZERO MOVE: C3 at its human pose is not moved and is UNRESOLVED, naming
  decap_distance.
* MOVED, STILL FAILING: C3 dropped onto Y1's pads is charged for the pad
  conflict AND the decap distance. The repair moves it off R1 -- and it is
  still too far from U1, so it is UNRESOLVED too ("it moved"). Re-grading
  only the zero-move refs (the obvious half-fix) passes the first case and
  fails this one.
* A REAL FIX is still repaired: R2 dropped onto R1 charges one pad conflict,
  the repair moves one of them off it, and that part is reported repaired.
* A charged claim the POSE grader cannot reproduce (it comes from `grade`
  outside the rules loop, so no move can clear it) stays UNRESOLVED.
* A re-grade that RAISES is not a pass: the ref is unresolved and says why.
* A move that CREATES an error is not a repair (the phase-1 verifier's
  counterexample): splitflap with 12 parts displaced, C8 and C12 are moved
  off pad conflicts and out of their decap limits -- unresolved, "created".
* Leaving the decap search radius is not a fix: C3 pushed past 5mm from U1
  becomes `decap_ungraded` (warn), and stays unresolved.
* A created `decap_pin_distance` (it names the IC) is charged to the CAP
  whose move stranded the pin, through its measured `cap` -- and, when the
  finding names no moved ref at all, by COUNTERFACTUAL: watchy seed 2's C5
  strands U4 pin 20 under a claim U4 already carried, naming only U4 and C3.
* A finding that only GREW is not "no change": watchy seed 1's C12 move grows
  U3's VBUS pin error 7.78 -> 11.10mm, and C12 is not repaired.
* CLI: `place_seed --repair` writes `violators`, `repaired_refs`,
  `unresolved_refs`, `unresolved_by_rule` (its exit 4 is the final grade's,
  as before), and -- the issue's own regression property -- the refs it
  reports repaired share no member with the refs a FRESH `floorplan.grade` of
  the written board still charges a decap error to.

#1066 (b), the opt-in decap rung (`repair_decaps` / `--repair-decaps`):
splitflap seed 3 -- C2 is seated at its IC and fixed, C8's only fixing pose is
disproportionate and refused; a fixing pose that adds a finding elsewhere is
reverted; the flag needs `--repair` and its record reaches JSON_SUMMARY.

    python3 tests/test_repair_decap_honesty.py
"""
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _d in ('py_router', 'py_placer', 'py_tools'):
    sys.path.insert(0, os.path.join(ROOT, _d))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS_DIR)

from kicad_parser import parse_kicad_pcb           # noqa: E402
from placement import floorplan as fp              # noqa: E402
from placement import seeder                       # noqa: E402
from placement.writer import write_placed_output   # noqa: E402
from run_utils import check, evidence              # noqa: E402

RUN_ALL_TIMEOUT = 1200

ESP = os.path.join(ROOT, 'kicad_files', 'esp_prog.kicad_pcb')
PLACE_SEED = os.path.join(ROOT, 'py_placer', 'place_seed.py')
SOURCES = ('kicad', 'sheet')
CLEARANCE = 0.2
DECAP_RULES = ('decap_distance', 'decap_pin_distance')


def _intent(td):
    doc = {'schema': 1, 'kind': fp.KIND, 'units': 'mm',
           'decaps': {'max_distance_mm': 2.0}}
    path = os.path.join(td, 'intent.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh)
    return fp.load_intent(path), path


def _board(td, name, moves=()):
    """esp_prog, with `moves` [(ref, x, y)] applied through the shipped
    writer, and its project siblings -- or the tracked board itself."""
    if not moves:
        return ESP
    pcb = parse_kicad_pcb(ESP)
    out = os.path.join(td, name)
    write_placed_output(ESP, out, [
        {'reference': r, 'new_x': x, 'new_y': y,
         'new_rotation': pcb.footprints[r].rotation or 0.0}
        for r, x, y in moves])
    return evidence(out, 'the perturbed board')


def _repair(board, intent):
    return seeder.repair_placement(
        parse_kicad_pcb(board), board, intent, group_sources=SOURCES,
        clearance=CLEARANCE)


def _decap_refs_after(board, res, intent, td, name):
    """Refs a FRESH grade of the repaired, written board charges a decap
    error to -- the issue's own instrument, not the repair's."""
    out = os.path.join(td, name)
    write_placed_output(board, out, res['moves'])
    g = fp.grade(intent, parse_kicad_pcb(out), out, group_sources=SOURCES,
                 clearance=CLEARANCE)
    return {v.ref for v in g.errors if v.rule in DECAP_RULES}


def test_a_zero_move_decap_violator_is_unresolved():
    with tempfile.TemporaryDirectory() as td:
        intent, _p = _intent(td)
        res = _repair(ESP, intent)
        assert 'C3' in res['violators'], res['violators']
        assert not res['moves'], res['moves']
        assert 'C3' not in res['repaired'], res['repaired']
        assert 'C3' in res['unresolved'], res['unresolved']
        assert res['unresolved_claims']['C3'] == ['decap_distance'], \
            res['unresolved_claims']
        note = [n for n in res['notes'] if n.startswith('C3: UNRESOLVED')]
        assert note and 'decap_distance' in note[0] \
            and 'did not move' in note[0], res['notes']
        still = _decap_refs_after(ESP, res, intent, td, 'z.kicad_pcb')
        assert not set(res['repaired']) & still, (res['repaired'], still)
    print(f"  PASS: C3 not moved and unresolved -- {note[0]}")


def test_a_moved_violator_still_failing_is_unresolved_and_a_real_fix_is_not():
    pcb = parse_kicad_pcb(ESP)
    r3, y1 = pcb.footprints['R3'], pcb.footprints['Y1']
    with tempfile.TemporaryDirectory() as td:
        intent, _p = _intent(td)
        # C3 onto the crystal Y1: 3.555mm from U1, inside the decap radius
        # (so graded, not `decap_ungraded`), and no move off Y1 that a pad
        # conflict licenses brings it within 2mm. R2 onto R3 -- not onto R1,
        # which sits inside U2's silk-drawn body: since #1153 a part that
        # moves off a pad conflict there and stays in that body is not
        # repaired, so the real fix below could not be shown on it.
        board = _board(td, 'stacked.kicad_pcb',
                       [('C3', y1.x, y1.y), ('R2', r3.x, r3.y + 0.3)])
        res = _repair(board, intent)
        moved = {m['reference'] for m in res['moves']}
        # The pad conflict moved C3 -- the arm is about a MOVED violator.
        assert 'C3' in moved, (moved, res['notes'])
        assert 'C3' in res['unresolved'], (res['unresolved'], res['notes'])
        assert 'C3' not in res['repaired'], res['repaired']
        note = [n for n in res['notes'] if n.startswith('C3: UNRESOLVED')]
        assert note and 'it moved' in note[0], res['notes']
        # R2/R3 is a pad conflict and nothing else: one of the pair moved
        # off the other and that part IS repaired.
        fixed = {'R2', 'R3'} & set(res['repaired'])
        assert fixed & moved, (res['repaired'], moved)
        still = _decap_refs_after(board, res, intent, td, 'm.kicad_pcb')
        assert 'C3' in still, still
        assert not set(res['repaired']) & still, (res['repaired'], still)
    print(f"  PASS: C3 moved {sorted(moved)} and is unresolved; "
          f"{sorted(fixed)} repaired")


def test_a_claim_the_pose_grader_cannot_reproduce_stays_unresolved():
    """A charged error from `grade` outside its rules loop (intent
    validation, block resolution, `exclusive_unsatisfiable`) is invariant
    under every move. Injected here as one extra error on U2, which carries
    no other claim and does not move."""
    real = fp.grade

    def grade_plus(*a, **kw):
        g = real(*a, **kw)
        g.violations.append(fp.Violation(
            'synthetic_invariant', fp.ERROR, 'a claim no pose can change',
            ref='U2', expected={'k': 1}))
        return g
    with tempfile.TemporaryDirectory() as td:
        intent, _p = _intent(td)
        fp.grade = grade_plus
        try:
            res = _repair(ESP, intent)
        finally:
            fp.grade = real
        assert 'U2' in res['violators'], res['violators']
        assert 'U2' not in {m['reference'] for m in res['moves']}
        assert 'U2' in res['unresolved'], (res['unresolved'], res['notes'])
        assert res['unresolved_claims']['U2'] == ['synthetic_invariant']
    print("  PASS: a pose-invariant charged claim is never read as cleared")


def test_a_regrade_that_raises_is_not_a_pass():
    real = fp.PoseGrader.violations
    calls = {'n': 0}

    def flaky(self, *a, **kw):
        calls['n'] += 1
        if calls['n'] > 1:
            raise fp.UntrustworthyOutline(['injected after the census'])
        return real(self, *a, **kw)
    with tempfile.TemporaryDirectory() as td:
        intent, _p = _intent(td)
        fp.PoseGrader.violations = flaky
        try:
            res = _repair(ESP, intent)
        finally:
            fp.PoseGrader.violations = real
        assert calls['n'] >= 2, calls
        assert 'C3' in res['unresolved'], (res['unresolved'], res['notes'])
        note = [n for n in res['notes'] if n.startswith('C3: UNRESOLVED')]
        assert note and 'could not be re-run' in note[0], res['notes']
    print(f"  PASS: {note[0]}")


def _perturbed(board, td, n=12, mm=4.0, seed='1'):
    """`n` random unlocked parts of `board` displaced up to `mm` on each
    axis, deterministically (the phase-1 verifier's generator)."""
    import random
    from placement.parser import extract_locked_refs
    from placement.portfolio import copy_siblings
    pcb = parse_kicad_pcb(board)
    rng = random.Random(seed)
    locked = extract_locked_refs(board)
    refs = sorted(r for r in pcb.footprints if r not in locked
                  and '~' not in r and not r.startswith('#'))
    moves = []
    for r in rng.sample(refs, min(n, len(refs))):
        f = pcb.footprints[r]
        moves.append({'reference': r,
                      'new_x': f.x + rng.uniform(-1, 1) * mm,
                      'new_y': f.y + rng.uniform(-1, 1) * mm,
                      'new_rotation': f.rotation or 0.0})
    src = os.path.join(td, 'perturbed.kicad_pcb')
    write_placed_output(board, src, moves)
    copy_siblings(board, src)
    return src


def test_a_move_that_creates_a_decap_error_is_not_repaired():
    """The phase-1 verifier's counterexample: charged claims alone are not
    enough, because a cap charged for a PAD conflict and moved off it can
    land outside its decap limit -- the charge clears, the board gains an
    error, and the cap read repaired. splitflap, 12 parts displaced up to
    4mm (seed 1): C8 and C12 were reported repaired with a decap_distance
    error only the repair's move had created."""
    board = os.path.join(ROOT, 'kicad_files', 'splitflap_driver.kicad_pcb')
    with tempfile.TemporaryDirectory() as td:
        doc = fp.emit_intent(parse_kicad_pcb(board), board)
        doc['decaps'] = dict(doc.get('decaps') or {}, max_distance_mm=2.5,
                             max_pin_distance_mm=2.5)
        ipath = os.path.join(td, 'intent.json')
        with open(ipath, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh)
        intent = fp.load_intent(ipath)
        src = _perturbed(board, td)
        before = {v.ref for v in fp.grade(
            intent, parse_kicad_pcb(src), src, group_sources=SOURCES,
            clearance=CLEARANCE).errors if v.rule in DECAP_RULES}
        res = _repair(src, intent)
        still = _decap_refs_after(src, res, intent, td, 'c.kicad_pcb')
        created = sorted(still - before)
        assert created, ("no move created a decap error on this fixture, "
                         "so the arm is vacuous", before, still)
        for r in created:
            note = [n for n in res['notes']
                    if n.startswith(f'{r}: UNRESOLVED')]
            assert note and 'created decap_distance' in note[0], (
                r, res['notes'])
            assert r in res['unresolved'], (r, res['unresolved'])
        assert not set(res['repaired']) & still, (res['repaired'], still)
    print(f"  PASS: {created} gained a decap error from their own move and "
          f"are unresolved; repaired {sorted(res['repaired'])} carry none")


def test_a_cap_pushed_past_the_decap_radius_is_not_cleared():
    """`decap_distance` stops being graded past the search radius (it becomes
    `decap_ungraded`, warn): leaving the radius must not read as the charge
    cleared. esp_prog, C3 6mm out from its pose (4.58mm from U1, on top of
    its neighbours), which the repair pushes 0.5mm further -- past 5mm."""
    import math
    pcb = parse_kicad_pcb(ESP)
    c = pcb.footprints['C3']
    a = 2 * math.pi * 13 / 16
    with tempfile.TemporaryDirectory() as td:
        intent, _p = _intent(td)
        board = _board(td, 'far.kicad_pcb',
                       [('C3', c.x + 6.0 * math.cos(a),
                         c.y + 6.0 * math.sin(a))])
        res = _repair(board, intent)
        out = os.path.join(td, 'far_out.kicad_pcb')
        write_placed_output(board, out, res['moves'])
        g = fp.grade(intent, parse_kicad_pcb(out), out,
                     group_sources=SOURCES, clearance=CLEARANCE)
        assert [v.rule for v in g.violations if v.ref == 'C3'] == [
            'decap_ungraded'], [(v.rule, v.measured) for v in g.violations
                                if v.ref == 'C3']
        assert 'C3' in res['unresolved'], (res['repaired'], res['notes'])
        note = [n for n in res['notes'] if n.startswith('C3: UNRESOLVED')]
        assert note and 'decap_ungraded' in note[0], res['notes']
    print(f"  PASS: {note[0]}")


def test_a_created_pin_error_is_charged_to_the_cap_that_moved():
    """`decap_pin_distance` names the IC, not the cap whose move stranded its
    pin, so a created one must be attributed through its measured `cap`.
    Injected: the re-grade adds U1's pin error naming C3, which the repair
    moved off Y1; U1 did not move."""
    pcb = parse_kicad_pcb(ESP)
    y1 = pcb.footprints['Y1']
    real = fp.PoseGrader.violations
    calls = {'n': 0}

    def plus_pin(self, *a, **kw):
        out = real(self, *a, **kw)
        calls['n'] += 1
        # Every call after the census -- counterfactual ones included, so
        # restoring C3 alone does not clear it: this arm pins the NAMES
        # channel (the finding's measured `cap`), not the counterfactual.
        if calls['n'] > 1:
            out = list(out) + [fp.Violation(
                'decap_pin_distance', fp.ERROR, 'injected', ref='U1',
                measured={'pad': '20', 'net': '/VCC', 'cap': 'C3',
                          'gap_mm': 9.0},
                expected={'max_pin_distance_mm': 2.0})]
        return out
    with tempfile.TemporaryDirectory() as td:
        intent, _p = _intent(td)
        board = _board(td, 'pin.kicad_pcb', [('C3', y1.x, y1.y)])
        fp.PoseGrader.violations = plus_pin
        try:
            res = _repair(board, intent)
        finally:
            fp.PoseGrader.violations = real
        moved = {m['reference'] for m in res['moves']}
        assert 'C3' in moved and 'U1' not in moved, moved
        assert 'decap_pin_distance' in res['unresolved_claims'].get('C3', ()), \
            (res['unresolved_claims'], res['notes'])
    print("  PASS: U1's created pin error is charged to the moved cap C3")


def test_a_second_finding_under_one_claim_is_new():
    """`finding_key` separates findings a claim merges: an IC already past
    its limit on pin 46 gains a stranded pin 20, SMALLER than pin 46's gap,
    so neither the claim nor its size says anything changed. Unit-level,
    because on a real board the 'worse' channel can hide the gap."""
    def pin(pad, gap):
        return fp.Violation('decap_pin_distance', fp.ERROR, 'x', ref='U4',
                            measured={'pad': pad, 'net': '/+3V3',
                                      'cap': 'C3', 'gap_mm': gap},
                            expected={'max_pin_distance_mm': 2.5})
    before = seeder.findings_of([pin('46', 5.0)])
    got = seeder.new_or_worse(before, [pin('46', 5.0), pin('20', 3.0)])
    assert [(v.measured['pad'], how) for v, how in got] == [('20', 'new')],         got
    got = seeder.new_or_worse(before, [pin('46', 5.5)])
    assert [(v.measured['pad'], how) for v, how in got] == [('46', 'worse')]
    assert seeder.new_or_worse(before, [pin('46', 5.0005)]) == []
    print("  PASS: pin 20 under U4's existing claim is NEW; pin 46 growing "
          "0.5mm is WORSE; 0.5um is noise")


def _watchy_repair(seed, td):
    board = os.path.join(ROOT, 'kicad_files', 'watchy.kicad_pcb')
    doc = fp.emit_intent(parse_kicad_pcb(board), board)
    doc['decaps'] = dict(doc.get('decaps') or {}, max_distance_mm=2.5,
                         max_pin_distance_mm=2.5)
    ipath = os.path.join(td, f'intent{seed}.json')
    with open(ipath, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh)
    intent = fp.load_intent(ipath)
    sub = os.path.join(td, f's{seed}')
    os.makedirs(sub)
    src = _perturbed(board, sub, seed=str(seed))
    return intent, src, _repair(src, intent)


def test_a_stranded_pin_is_charged_to_the_cap_that_left_it():
    """The round-2 verifier's counterexample, which neither the claim key nor
    the names in the finding can see. watchy, 12 parts displaced (seed 2):
    the repair moves C5 1.95mm, which strands U4's pin 20 -- a NEW finding,
    but under a claim U4 already carried (pin 46), and naming U4 and the
    now-nearest cap C3, neither of which moved. Only restoring C5 alone
    clears it, so C5 is charged and is not repaired."""
    with tempfile.TemporaryDirectory() as td:
        intent, src, res = _watchy_repair(2, td)
        assert 'C5' in {m['reference'] for m in res['moves']}, res['moves']
        assert 'C5' not in res['repaired'], res['repaired']
        note = [n for n in res['notes'] if n.startswith('C5: UNRESOLVED')]
        assert note and 'created decap_pin_distance' in note[0], note
        after = os.path.join(td, 'w2.kicad_pcb')
        write_placed_output(src, after, res['moves'])
        pins = [v for v in fp.grade(intent, parse_kicad_pcb(after), after,
                                    group_sources=SOURCES,
                                    clearance=CLEARANCE).errors
                if v.rule == 'decap_pin_distance' and v.ref == 'U4'
                and str((v.measured or {}).get('pad')) == '20']
        assert pins and pins[0].measured.get('cap') != 'C5', pins
    print(f"  PASS: {note[0]}")


def test_a_move_that_makes_a_finding_worse_is_not_repaired():
    """`grade_delta` counts claims, so an error that only GREW is 'no
    change'. watchy seed 1: the repair re-seats C12 4.24mm and U3's VBUS pin
    error grows 7.78 -> 11.10mm -- a regression, not a repair."""
    with tempfile.TemporaryDirectory() as td:
        _intent_, _src, res = _watchy_repair(1, td)
        assert 'C12' in {m['reference'] for m in res['moves']}
        assert 'C12' not in res['repaired'], res['repaired']
        note = [n for n in res['notes'] if n.startswith('C12: UNRESOLVED')]
        assert note and '(made worse)' in note[0], note
    print(f"  PASS: {note[0]}")


def _splitflap3(td):
    """splitflap, decaps 2.5/2.5, 12 parts displaced up to 4mm (seed 3):
    the repair's charged decap violators are C2 and C8."""
    board = os.path.join(ROOT, 'kicad_files', 'splitflap_driver.kicad_pcb')
    doc = fp.emit_intent(parse_kicad_pcb(board), board)
    doc['decaps'] = dict(doc.get('decaps') or {}, max_distance_mm=2.5,
                         max_pin_distance_mm=2.5)
    ipath = os.path.join(td, 'sf3.json')
    with open(ipath, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh)
    return fp.load_intent(ipath), _perturbed(board, td, seed='3')


def test_the_decap_rung_seats_a_cap_and_refuses_a_disproportionate_move():
    """#1066 (b), `repair_decaps=True`: C2 is seated toward its IC and its
    decap error is gone from a fresh grade of the written board; C8's only
    fixing pose is farther than the proportion budget allows, so it is left
    where it stands and says so. Without the flag neither moves (control)."""
    with tempfile.TemporaryDirectory() as td:
        intent, src = _splitflap3(td)
        off = _repair(src, intent)
        on = seeder.repair_placement(
            parse_kicad_pcb(src), src, intent, group_sources=SOURCES,
            clearance=CLEARANCE, repair_decaps=True)
        assert off['decap_rung'] == {}, off['decap_rung']
        still_off = _decap_refs_after(src, off, intent, td, 'off.kicad_pcb')
        still_on = _decap_refs_after(src, on, intent, td, 'on.kicad_pcb')
        assert {'C2', 'C8'} <= still_off, still_off
        rung = {c: [x['result'] for x in r['tried']]
                for c, r in on['decap_rung'].items()}
        assert rung.get('C2') == ['seated'], rung
        assert rung.get('C8') == ['disproportionate'], rung
        assert 'C2' not in still_on and 'C8' in still_on, still_on
        assert 'C2' in on['repaired'] and 'C8' in on['unresolved'], on
        assert not set(on['repaired']) & still_on, (on['repaired'], still_on)
        note = [n for n in on['notes'] if n.startswith('C8: decap rung')]
        assert note and 'disproportionate' in note[0], on['notes']
    print(f"  PASS: C2 seated and fixed; {note[0]}")


def test_the_decap_rung_reverts_a_seat_that_adds_a_finding():
    """A fixing pose that makes ANY other finding new or worse is reverted.
    Injected: while C2 sits anywhere but its input pose, the grade carries
    one more error on U2 -- the rung must put C2 back and name it."""
    real = fp.PoseGrader.violations
    with tempfile.TemporaryDirectory() as td:
        intent, src = _splitflap3(td)
        home = parse_kicad_pcb(src).footprints['C2']

        def plus_if_c2_moved(self, *a, **kw):
            out = real(self, *a, **kw)
            p = (kw.get('poses') or {}).get('C2')
            here = p or (self.state.parts['C2'].x, self.state.parts['C2'].y)
            if abs(here[0] - home.x) + abs(here[1] - home.y) > 1e-6:
                out = list(out) + [fp.Violation(
                    'synthetic_new', fp.ERROR, 'injected', ref='U2')]
            return out
        fp.PoseGrader.violations = plus_if_c2_moved
        try:
            on = seeder.repair_placement(
                parse_kicad_pcb(src), src, intent, group_sources=SOURCES,
                clearance=CLEARANCE, repair_decaps=True)
        finally:
            fp.PoseGrader.violations = real
        row = on['decap_rung']['C2']['tried'][0]
        assert row['result'] == 'reverted', row
        assert any('synthetic_new' in a for a in row['added']), row
        assert 'C2' not in {m['reference'] for m in on['moves']}, on['moves']
    print(f"  PASS: C2 reverted -- would add {row['added']}")


def test_the_decap_rung_skips_a_locked_cap_and_reverts_a_growing_overlap():
    """Two of the rung's own refusals. A cap the caller locks is never
    moved (its row says `locked`). And a fixing pose that grows the
    placement's courtyard overlap is reverted even when the grade adds no
    error -- injected: `legality_at` reads 1mm2 more while C2 is away from
    its input pose."""
    real = fp.PoseGrader.legality_at
    with tempfile.TemporaryDirectory() as td:
        intent, src = _splitflap3(td)
        locked = seeder.repair_placement(
            parse_kicad_pcb(src), src, intent, group_sources=SOURCES,
            clearance=CLEARANCE, repair_decaps=True, lock_globs=['C2'])
        assert [x['result'] for x in
                locked['decap_rung']['C2']['tried']] == ['locked'], locked
        home = parse_kicad_pcb(src).footprints['C2']

        def grows(self, *a, **kw):
            got = dict(real(self, *a, **kw))
            p = self.state.parts['C2']
            if abs(p.x - home.x) + abs(p.y - home.y) > 1e-6:
                got['overlap_area'] = float(got.get('overlap_area') or 0) + 1
            return got
        fp.PoseGrader.legality_at = grows
        try:
            on = seeder.repair_placement(
                parse_kicad_pcb(src), src, intent, group_sources=SOURCES,
                clearance=CLEARANCE, repair_decaps=True)
        finally:
            fp.PoseGrader.legality_at = real
        row = on['decap_rung']['C2']['tried'][0]
        assert row['result'] == 'reverted', row
        assert 'legality.overlap_area' in row['added'], row
    print("  PASS: a locked C2 is skipped; a C2 seat that grows the overlap "
          "is reverted")


def test_the_decap_rung_does_not_call_leaving_the_radius_a_fix():
    """Past the decap search radius `decap_distance` (error) becomes
    `decap_ungraded` (warn); the rung must read that as still open, not as
    cleared (round-2 verifier: tigard C18). Injected: while C2 is away from
    its input pose, the grade reports it ungraded instead of too far."""
    real = fp.PoseGrader.violations
    with tempfile.TemporaryDirectory() as td:
        intent, src = _splitflap3(td)
        home = parse_kicad_pcb(src).footprints['C2']

        def ungraded_when_moved(self, *a, **kw):
            out = real(self, *a, **kw)
            p = (kw.get('poses') or {}).get('C2')
            here = p or (self.state.parts['C2'].x, self.state.parts['C2'].y)
            if abs(here[0] - home.x) + abs(here[1] - home.y) <= 1e-6:
                return out
            out = [v for v in out
                   if not (v.rule == 'decap_distance' and v.ref == 'C2')]
            return out + [fp.Violation('decap_ungraded', fp.WARN,
                                       'injected', ref='C2')]
        fp.PoseGrader.violations = ungraded_when_moved
        try:
            on = seeder.repair_placement(
                parse_kicad_pcb(src), src, intent, group_sources=SOURCES,
                clearance=CLEARANCE, repair_decaps=True)
        finally:
            fp.PoseGrader.violations = real
        row = on['decap_rung']['C2']['tried'][0]
        assert row['result'] == 'reverted' and row['still'], row
    print("  PASS: C2 walked out of the radius is reverted, not 'seated'")


def test_two_caps_on_two_pins_of_one_ic_do_not_block_each_other():
    """Every uncovered pin of one IC shares ONE claim, so the rung must ask
    about the FINDING (its pin), or a cap that clears pin 1 reads as failing
    while pin 6 is still charged and is reverted (phase-3 verifier: 11 such
    on glasgow). watchy, piled and seeded (seed 0), decaps 2.5/2.5: U4 has
    several pins charged, and a cap is seated for one of them while another
    is still open."""
    import random
    from placement.parser import extract_locked_refs
    from placement.portfolio import copy_siblings
    board = os.path.join(ROOT, 'kicad_files', 'watchy.kicad_pcb')
    with tempfile.TemporaryDirectory() as td:
        pcb = parse_kicad_pcb(board)
        doc = fp.emit_intent(pcb, board)
        doc['decaps'] = dict(doc.get('decaps') or {}, max_distance_mm=2.5,
                             max_pin_distance_mm=2.5)
        ipath = os.path.join(td, 'w.json')
        with open(ipath, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh)
        intent = fp.load_intent(ipath)
        locked = extract_locked_refs(board)
        bb = pcb.board_info.board_bounds
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        pile = os.path.join(td, 'pile.kicad_pcb')
        write_placed_output(board, pile, [
            {'reference': r, 'new_x': cx, 'new_y': cy,
             'new_rotation': f.rotation or 0.0}
            for r, f in pcb.footprints.items() if r not in locked])
        copy_siblings(board, pile)
        res = seeder.seed_from_intent(
            parse_kicad_pcb(pile), pile, intent, random.Random('0'),
            group_sources=SOURCES, clearance=CLEARANCE,
            board_edge_clearance=0.55, grid_step=0.1)
        seeded = os.path.join(td, 's0.kicad_pcb')
        write_placed_output(pile, seeded, res['placements'])
        copy_siblings(board, seeded)
        before = fp.grade(intent, parse_kicad_pcb(seeded), seeded,
                          group_sources=SOURCES, clearance=CLEARANCE)
        on = seeder.repair_placement(
            parse_kicad_pcb(seeded), seeded, intent, group_sources=SOURCES,
            clearance=CLEARANCE, repair_decaps=True)
        pins = {}
        for v in before.errors:
            if v.rule == 'decap_pin_distance':
                pins.setdefault(v.ref, set()).add(str(v.measured.get('pad')))
        multi = {ic for ic, p in pins.items() if len(p) >= 2}
        seated = [(c, x['ic'], x['pad']) for c, r in on['decap_rung'].items()
                  for x in r['tried'] if x['rule'] == 'decap_pin_distance'
                  and x['ic'] in multi and x['result'] == 'seated']
        assert seated, (multi, on['decap_rung'])
        still = _decap_refs_after(seeded, on, intent, td, 'w_on.kicad_pcb')
        assert not set(on['repaired']) & still, (on['repaired'], still)
    print(f"  PASS: {len(seated)} pin seat(s) on multi-pin ICs kept, e.g. "
          f"{seated[0]}")


def test_cli_repair_decaps_needs_repair_and_reports_the_rung():
    from placement.portfolio import copy_siblings
    with tempfile.TemporaryDirectory() as td:
        intent, src0 = _splitflap3(td)
        board = os.path.join(td, 'sf3_in.kicad_pcb')
        write_placed_output(src0, board, [])
        copy_siblings(src0, board)
        ipath = os.path.join(td, 'sf3.json')
        out = os.path.join(td, 'sf3_out.kicad_pcb')
        check([sys.executable, '-X', 'utf8', PLACE_SEED, board, out,
               '--intent', ipath, '--repair-decaps'],
              refuse='--repair-decaps only applies to --repair', code=2,
              allow=('error: argument',), timeout=RUN_ALL_TIMEOUT)
        r = check([sys.executable, '-X', 'utf8', PLACE_SEED, board, out,
                   '--intent', ipath, '--repair', '--repair-decaps',
                   '--clearance', str(CLEARANCE)],
                  refuse='GRADE ERROR', code=4, timeout=RUN_ALL_TIMEOUT)
        s = _summary(r)
        assert [x['result'] for x in s['decap_rung']['C2']['tried']] == [
            'seated'], s['decap_rung']
        assert 'C2' in s['repaired_refs'], s
    print("  PASS: --repair-decaps alone is refused; with --repair the "
          "summary carries the rung")


def _summary(r):
    m = re.search(r'^JSON_SUMMARY: (.*)$', r.stdout, re.M)
    assert m, r.stdout[-1500:]
    return json.loads(m.group(1))


def test_cli_writes_the_refs_and_the_repaired_set_is_honest():
    from placement.portfolio import copy_siblings
    with tempfile.TemporaryDirectory() as td:
        intent, ipath = _intent(td)
        src = os.path.join(td, 'in.kicad_pcb')
        write_placed_output(ESP, src, [])
        copy_siblings(ESP, src)
        out = os.path.join(td, 'out.kicad_pcb')
        # Exit 4 here is the FINAL GRADE's (the board still carries C3's
        # error, which place_seed has always refused on), asserted by its
        # reason -- not `unresolved`, which sets no exit code of its own.
        r = check([sys.executable, '-X', 'utf8', PLACE_SEED, src, out,
                   '--intent', ipath, '--repair', '--clearance',
                   str(CLEARANCE)], refuse='GRADE ERROR [decap_distance] C3',
                  code=4, timeout=RUN_ALL_TIMEOUT)
        s = _summary(r)
        for k in ('violators', 'repaired_refs', 'unresolved',
                  'unresolved_refs', 'unresolved_by_rule'):
            assert k in s, (k, sorted(s))
        assert 'C3' in s['unresolved_refs'], s
        assert s['unresolved_by_rule'].get('decap_distance') == 1, s
        assert s['repaired'] == len(s['repaired_refs']), s
        assert s['unresolved'] == len(s['unresolved_refs']), s
        g = fp.grade(intent, parse_kicad_pcb(evidence(out, 'the output')),
                     out, group_sources=SOURCES, clearance=CLEARANCE)
        still = {v.ref for v in g.errors if v.rule in DECAP_RULES}
        assert 'C3' in still, still
        assert not set(s['repaired_refs']) & still, (s['repaired_refs'],
                                                      still)
        line = [ln for ln in r.stdout.splitlines()
                if ln.startswith('Repair:')]
        # #1153: the other violator is R1, charged for sitting inside U2's
        # silk-drawn body and never moved. It read "1 repaired (0 moved)"
        # while the pair stayed on the board; it is unresolved too.
        assert 'R1' in s['unresolved_refs'], s
        assert line and '2 unresolved' in line[0], line
    print(f"  PASS: {line[0]}")


TESTS = [
    test_a_zero_move_decap_violator_is_unresolved,
    test_a_moved_violator_still_failing_is_unresolved_and_a_real_fix_is_not,
    test_a_claim_the_pose_grader_cannot_reproduce_stays_unresolved,
    test_a_regrade_that_raises_is_not_a_pass,
    test_a_move_that_creates_a_decap_error_is_not_repaired,
    test_a_cap_pushed_past_the_decap_radius_is_not_cleared,
    test_a_created_pin_error_is_charged_to_the_cap_that_moved,
    test_a_second_finding_under_one_claim_is_new,
    test_a_stranded_pin_is_charged_to_the_cap_that_left_it,
    test_a_move_that_makes_a_finding_worse_is_not_repaired,
    test_cli_writes_the_refs_and_the_repaired_set_is_honest,
    test_the_decap_rung_seats_a_cap_and_refuses_a_disproportionate_move,
    test_the_decap_rung_reverts_a_seat_that_adds_a_finding,
    test_the_decap_rung_skips_a_locked_cap_and_reverts_a_growing_overlap,
    test_the_decap_rung_does_not_call_leaving_the_radius_a_fix,
    test_two_caps_on_two_pins_of_one_ic_do_not_block_each_other,
    test_cli_repair_decaps_needs_repair_and_reports_the_rung,
]


if __name__ == '__main__':
    only = sys.argv[1:]
    for t in TESTS:
        if only and not any(o in t.__name__ for o in only):
            continue
        print(f"--- {t.__name__}")
        t()
    print('ALL PASS')
