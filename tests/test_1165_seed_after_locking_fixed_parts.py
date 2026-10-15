#!/usr/bin/env python3
"""#1165: place_seed seeds a pile whose fixed parts were placed and locked first.

The pcb-free-agent skill says to place and lock the connectors and other fixed
parts, then seed the rest. Doing that made place_seed exit 3 ("this board
already looks PLACED") and rank_rotations stop with `refused: board_placed`,
with nearly every part still in the pile: the gate read `unplaced` /
`partially_unplaced`, whose spread (s2) and off-outline (s3) statistics count
the locked parts. Seating them widened the spread, and on a small board they
also diluted the off-outline share below 0.9, so even #1109's `pile` read
False (ecc83: 4 locked holes of 15 parts, 0.733).

The fixture is that ecc83 shape, built from a TRACKED board through the CLIs
the skill uses: `make_unplaced.py` piles lvds_converter_dualclk (13 parts),
then `place_pose.py` puts its two connectors back at their designed poses and
locks them -- 11/13 = 0.846 of the parts off the outline, under the threshold.

What is pinned:
  * `assess_placement` measures the off-outline share over the UNLOCKED parts
    too (`movable_outside_fraction`), and `pile` / `off_outline` read it;
    `unplaced` and `partially_unplaced` do not move, so every gate_or_exit
    caller refuses and warns exactly as before;
  * `seed_scope` is SEED_ALL there, and SEED_ALL outranks a stacked scope;
  * place_seed seeds the pile (exit 0), the locked connectors stand, and
    rank_rotations ranks instead of refusing;
  * the negative control: the PLACED board with the same two parts locked is
    still refused for the stated reason, exit 3.

Run: python3 -X utf8 tests/test_1165_seed_after_locking_fixed_parts.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ('py_placer', 'py_router', 'py_tools', 'tests'):
    sys.path.insert(0, os.path.join(ROOT, _p))

from kicad_parser import parse_kicad_pcb                       # noqa: E402
from placement.placement_state import (                        # noqa: E402
    OUTSIDE_FRACTION, SEED_ALL, SEED_NONE, SEED_STACKED, PlacementState,
    assess_placement)
from run_utils import check, evidence                          # noqa: E402

PY = sys.executable
SRC = os.path.join(ROOT, 'kicad_files', 'lvds_converter_dualclk.kicad_pcb')
#: The connectors' designed poses on the tracked board.
FIXED = {'J1': (83.75, 57.96, 0.0), 'J2': (125.75, 76.51, 180.0)}
MAKE_UNPLACED = os.path.join(ROOT, '.claude', 'skills', 'pcb-free-agent',
                             'scripts', 'make_unplaced.py')


def _cli(*parts):
    return [PY, '-X', 'utf8'] + [str(p) for p in parts]


def _place_and_lock(src, dst):
    """place_pose: both connectors at their designed poses, then locked."""
    argv = [os.path.join(ROOT, 'py_placer', 'place_pose.py'), src, dst]
    for ref, (x, y, rot) in sorted(FIXED.items()):
        argv += ['set', ref, x, y, '--rot', rot]
    for ref in sorted(FIXED):
        argv += ['lock', ref]
    check(_cli(*argv), accept=True)
    return evidence(dst, 'the locked-connector board')


class LockedFixedPartsThenSeed(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp(prefix='t1165_')
        evidence(SRC, 'the tracked source board')
        cls.pile = os.path.join(cls.td, 'pile.kicad_pcb')
        check(_cli(MAKE_UNPLACED, SRC, cls.pile), accept=True)
        evidence(cls.pile, 'the pile')
        cls.intent = os.path.join(cls.td, 'intent.json')
        check(_cli(os.path.join(ROOT, 'py_tools', 'check_floorplan.py'),
                   cls.pile, '--emit-intent', cls.intent, '--allow-unplaced'),
              accept=True)
        evidence(cls.intent, 'the emitted intent')
        cls.locked = _place_and_lock(
            cls.pile, os.path.join(cls.td, 'locked.kicad_pcb'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.td, ignore_errors=True)

    def _state(self, path):
        return assess_placement(parse_kicad_pcb(path), path)

    def test_the_input_is_the_ecc83_shape(self):
        """The whole-board share is under the threshold (so the old gates all
        read 'placed'); the unlocked parts are all off the outline."""
        st = self._state(self.locked)
        self.assertLess(st.outside_fraction, OUTSIDE_FRACTION, st.signals)
        self.assertFalse(st.signals['s3_outside'], st.signals)
        self.assertEqual(st.signals['locked'], 2, st.signals)
        self.assertEqual(st.movable_outside_fraction, 1.0, st.signals)
        # The verdicts gate_or_exit reads are untouched by #1165.
        self.assertFalse(st.unplaced, st.reasons)
        self.assertFalse(st.partially_unplaced, st.reasons)
        # The ones that changed.
        self.assertTrue(st.signals['s3_movable_outside'], st.signals)
        self.assertTrue(st.off_outline)
        self.assertTrue(st.pile)
        self.assertEqual(st.seed_scope, SEED_ALL)
        self.assertTrue(any('unlocked footprints lie outside' in r
                            for r in st.reasons), st.reasons)

    def test_place_seed_seeds_it_and_the_locks_stand(self):
        out = os.path.join(self.td, 'seeded.kicad_pcb')
        r = check(_cli(os.path.join(ROOT, 'py_placer', 'place_seed.py'),
                       self.locked, out, '--intent', self.intent),
                  accept=True)
        self.assertIn('seeding every unlocked part; the locked ones stand',
                      r.stdout)
        self.assertNotIn('already looks PLACED', r.stdout + r.stderr)
        pcb = parse_kicad_pcb(evidence(out, 'the seed'))
        for ref, (x, y, rot) in FIXED.items():
            fp = pcb.footprints[ref]
            self.assertTrue(fp.locked, ref)
            self.assertEqual((round(fp.x, 3), round(fp.y, 3),
                              round((fp.rotation or 0.0) % 360, 3)),
                             (x, y, rot), ref)
        # The unlocked parts left the pile: the seed is no longer one.
        st = self._state(out)
        self.assertLess(st.movable_outside_fraction, OUTSIDE_FRACTION,
                        st.signals)
        self.assertFalse(st.pile, st.reasons)

    def test_rank_rotations_ranks_instead_of_refusing(self):
        od = os.path.join(self.td, 'rot')
        check(_cli(os.path.join(ROOT, 'py_placer', 'rank_rotations.py'),
                   self.locked, '--intent', self.intent, '--ref', 'IC4',
                   '--seeds', '0', '--out-dir', od), accept=True)
        with open(evidence(os.path.join(od, 'rotations.json'),
                           'the ranking'), encoding='utf-8') as fh:
            doc = json.load(fh)
        self.assertIsNone(doc.get('refused'), doc.get('refused'))

    def test_a_placed_board_with_locks_is_still_refused(self):
        """Negative control: the tracked (placed) board with the same two
        connectors locked. A lock is not a pile."""
        placed = _place_and_lock(SRC, os.path.join(self.td, 'placed.kicad_pcb'))
        st = self._state(placed)
        self.assertFalse(st.pile, st.signals)
        self.assertEqual(st.seed_scope, SEED_NONE)
        check(_cli(os.path.join(ROOT, 'py_placer', 'place_seed.py'), placed,
                   os.path.join(self.td, 'no.kicad_pcb'),
                   '--intent', self.intent),
              refuse='already looks PLACED', code=3)


class TheScopePredicate(unittest.TestCase):
    """`seed_scope`'s precedence, which place_seed, rank_rotations and
    check_floorplan's seeder forecast all read."""

    def test_each_arm(self):
        self.assertEqual(PlacementState(unplaced=True).seed_scope, SEED_ALL)
        self.assertEqual(PlacementState(
            signals={'s3_outside': True}).seed_scope, SEED_ALL)
        self.assertEqual(PlacementState(
            signals={'s3_movable_outside': True}).seed_scope, SEED_ALL)
        self.assertEqual(PlacementState(
            partially_unplaced=True,
            stacked_suspect_refs=['R1', 'R2']).seed_scope, SEED_STACKED)
        self.assertEqual(PlacementState().seed_scope, SEED_NONE)

    def test_off_outline_outranks_a_stack(self):
        """A pile whose fixed parts were placed first can carry a deliberate
        stack (CM5's two module halves share an origin). Scoping the seed to
        that stack seeded two locked parts and left the pile in place."""
        st = PlacementState(partially_unplaced=True, n_footprints=84,
                            stacked_suspect_refs=['Module301', 'Module302'],
                            signals={'s3_outside': True})
        self.assertEqual(st.seed_scope, SEED_ALL)
        self.assertTrue(st.pile)

    def test_pile_reads_the_movable_arm(self):
        self.assertTrue(PlacementState(
            signals={'s3_movable_outside': True}).pile)
        self.assertFalse(PlacementState(
            signals={'s3_movable_outside': False}).pile)


if __name__ == '__main__':
    unittest.main()
