"""#1172 (and #1155's generator half): stage 1 chooses an edge connector's
angle, and keeps its courtyard off the connectors it has already seated.

On glasgow_revC's pile, stage 1 seated the IDC box headers J2 (north) and J3
(south) at the pile's arbitrary rotation 0 -- each one's long axis ACROSS the
board -- and checked only pads against what it had seated, so their shells
interpenetrated (76.3 mm2 on the fa10 input). Two mechanisms, two fixes:

  1. `_stage1_claims`: an edge connector whose INPUT angle carries no
     information (`_uninformative_refs`: the board is a pile, or its pads are
     all off the outline) gets every quarter turn, least depth into the board
     first; a STRONG mating face (#1155) decides the angle outright, and
     turns a part facing away on any board. Everything else keeps its angle.
  2. `_placed_courtyard_hits` at rung admission: a rung that overlaps a
     placed courtyard (the floorplan budget's currency) is refused in a
     first pass; only when no rung clears both pads and courtyards does the
     old pad-only ladder run (`_stage1_one`), so it cannot lose a seat.

`_seat_edge` (the repair path) turns a strong face that points away to the
facing quarter turn and never seats it reversed. Each has a blind twin: the
module flags `_stage1_choose_edge_angle` / `_edge_courtyard_tier` off.
"""

import json
import os
import random
import sys
import tempfile
import unittest
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ('py_placer', 'py_router', 'py_tools', 'tests'):
    sys.path.insert(0, os.path.join(ROOT, _p))

from kicad_parser import parse_kicad_pcb                      # noqa: E402
from placement import floorplan, legality, seeder            # noqa: E402
from placement.body import board_bodies                      # noqa: E402
from run_utils import check                                  # noqa: E402

PY = sys.executable
KF = os.path.join(ROOT, 'kicad_files')
GLASGOW = os.path.join(KF, 'glasgow_revC.kicad_pcb')
SONDE = os.path.join(KF, 'sonde_u.kicad_pcb')
MK = {'MK1': (54, 116), 'MK2': (54, 75), 'MK3': (126, 75), 'MK4': (126, 116)}


@contextmanager
def flags(**kw):
    saved = {k: getattr(seeder, k) for k in kw}
    for k, v in kw.items():
        setattr(seeder, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(seeder, k, v)


def _pile(td, src, name, *extra_ops):
    """`make_unplaced` of a tracked board, then `place_pose` ops on it."""
    pile = os.path.join(td, name + '_pile.kicad_pcb')
    check([PY, '-X', 'utf8', '.claude/skills/pcb-free-agent/scripts/'
           'make_unplaced.py', src, pile], accept=True)
    if not extra_ops:
        return pile
    out = os.path.join(td, name + '.kicad_pcb')
    check([PY, '-X', 'utf8', 'py_placer/place_pose.py', pile, out, '--force']
          + list(extra_ops), accept=True)
    return out


def _intent(td, board, entries, name='i'):
    doc = {'schema': floorplan.SCHEMA_VERSION, 'kind': floorplan.KIND,
           'units': 'mm', 'edge_connectors': entries}
    p = os.path.join(td, name + '.json')
    with open(p, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh)
    return floorplan.load_intent(p)


def _seed(board, intent, refs):
    pcb = parse_kicad_pcb(board)
    res = seeder.seed_from_intent(pcb, board, intent, random.Random(1),
                                  seed_refs=set(refs))
    poses = {p['reference']: (p['new_x'], p['new_y'], p['new_rotation'])
             for p in res['placements']}
    return pcb, res, poses


def _overlap(board, poses, a, b):
    """The courtyard overlap of a and b at `poses`, in the budget's currency."""
    pcb = parse_kicad_pcb(board)
    cen = legality.CourtyardCensus(pcb, board)
    gp, _ = legality.courtyard_budget_universe(cen, [a, b], poses)
    return legality.pair_overlap_area_exact(gp[a], gp[b])


def _extent(board, ref, pose):
    pcb = parse_kicad_pcb(board)
    lb = legality.part_local_bounds(pcb, board)[ref]
    r = legality.rotate_local_bounds(*lb.local, pose[2])
    return r[2] - r[0], r[3] - r[1]


GLASGOW_CLAIMS = [{'ref': 'J2', 'edge': 'north',
                   'overhang_mm': {'min': 0, 'max': 0.55}},
                  {'ref': 'J3', 'edge': 'south',
                   'overhang_mm': {'min': 0, 'max': 0.55}}]


class GlasgowBoxHeaders(unittest.TestCase):
    """The issue's own input: glasgow's pile, MK1-4 placed and locked."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp()
        ops = []
        for ref, (x, y) in MK.items():
            ops += ['set', ref, str(x), str(y), '--rot', '0']
        ops += ['lock'] + list(MK)
        cls.board = _pile(cls.td, GLASGOW, 'g', *ops)
        cls.intent = _intent(cls.td, cls.board, GLASGOW_CLAIMS)

    def test_the_headers_lie_along_their_edges_and_clear_each_other(self):
        _pcb, res, poses = _seed(self.board, self.intent, ('J2', 'J3'))
        for ref in ('J2', 'J3'):
            w, h = _extent(self.board, ref, poses[ref])
            self.assertGreater(w, h, (ref, poses[ref]))
        self.assertLess(_overlap(self.board, poses, 'J2', 'J3'),
                        legality.EPS)
        self.assertEqual(sorted(res['edge_angle_chosen']), ['J2', 'J3'])
        self.assertTrue(any('stage 1 turned it' in n and '#1172' in n
                            for n in res['notes']))

    def test_blind_twin_reproduces_the_issue(self):
        with flags(_stage1_choose_edge_angle=False,
                   _edge_courtyard_tier=False):
            _pcb, res, poses = _seed(self.board, self.intent, ('J2', 'J3'))
        self.assertEqual({r: poses[r][2] % 360 for r in ('J2', 'J3')},
                         {'J2': 0.0, 'J3': 0.0})
        self.assertGreater(_overlap(self.board, poses, 'J2', 'J3'), 1.0)
        self.assertEqual(res['edge_angle_chosen'], {})

    def test_the_courtyard_tier_alone_separates_them(self):
        """Mechanism 2 by itself: both DECLARED at 0, so no angle is chosen,
        and J3 slides along its edge off J2's courtyard."""
        doc = {'schema': floorplan.SCHEMA_VERSION, 'kind': floorplan.KIND,
               'units': 'mm', 'edge_connectors': GLASGOW_CLAIMS,
               'blocks': [{'name': 'rotation:J2', 'refs': ['J2'],
                           'rotation': 0},
                          {'name': 'rotation:J3', 'refs': ['J3'],
                           'rotation': 0}]}
        p = os.path.join(self.td, 'decl.json')
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh)
        intent = floorplan.load_intent(p)
        _pcb, res, poses = _seed(self.board, intent, ('J2', 'J3'))
        self.assertEqual({r: poses[r][2] % 360 for r in ('J2', 'J3')},
                         {'J2': 0.0, 'J3': 0.0})
        self.assertLess(_overlap(self.board, poses, 'J2', 'J3'),
                        legality.EPS)
        with flags(_edge_courtyard_tier=False):
            _pcb, _res, off = _seed(self.board, intent, ('J2', 'J3'))
        self.assertGreater(_overlap(self.board, off, 'J2', 'J3'), 1.0)

    def test_it_cannot_lose_a_seat(self):
        """When EVERY rung overlaps a placed courtyard, no angle seats clean,
        so each header keeps the pad-clear seat of the first angle stage 1
        chose: the seat the old pad-only ladder finds there (the tier-off
        arm's), the tier's T2 -- not the input angle's attempt, which lies
        across the board (round-2 verifier: orangecrab's pile shorted J5 at
        its input angle where a chosen one seated pad-clear). The overlap and
        the choice are named."""
        orig = seeder._placed_courtyard_hits
        try:
            seeder._placed_courtyard_hits = (
                lambda state, ref, pose, others: ['MK1'])
            _pcb, res, poses = _seed(self.board, self.intent, ('J2', 'J3'))
        finally:
            seeder._placed_courtyard_hits = orig
        with flags(_edge_courtyard_tier=False):
            _pcb, off_res, off = _seed(self.board, self.intent, ('J2', 'J3'))
        self.assertEqual({r: poses[r] for r in ('J2', 'J3')},
                         {r: off[r] for r in ('J2', 'J3')})
        self.assertEqual(sorted(res['edge_angle_chosen']), ['J2', 'J3'])
        self.assertEqual(
            {r: v['to'] for r, v in res['edge_angle_chosen'].items()},
            {r: v['to'] for r, v in off_res['edge_angle_chosen'].items()})
        self.assertTrue(any('overlaps it' in n and 'MK1' in n
                            for n in res['notes']))
        self.assertTrue(any('so it keeps the first pad-clear seat, at' in n
                            for n in res['notes']), res['notes'])

    def test_a_clean_input_outranks_a_pad_clear_seat(self):
        """J3 alone (J2 keeps its pile pose, counted as placed): every angle
        but the input 0 is made to overlap a placed courtyard. An
        uninformative input's set holds every quarter turn, the input angle
        included, so the walk itself finds 0 clean and keeps it over the
        pad-clear seats ahead of it: J3 gets exactly the seat it had before
        #1172 chose anything, and no fall-back is needed."""
        orig = seeder._placed_courtyard_hits

        def hits(state, ref, pose, others):
            if ref == 'J3' and abs((pose[2] + 180.0) % 360.0 - 180.0) > 1e-6:
                return ['MK1']
            return orig(state, ref, pose, others)
        try:
            seeder._placed_courtyard_hits = hits
            _pcb, res, poses = _seed(self.board, self.intent, ('J3',))
        finally:
            seeder._placed_courtyard_hits = orig
        with flags(_stage1_choose_edge_angle=False):
            _pcb, _r, off = _seed(self.board, self.intent, ('J3',))
        self.assertEqual(poses.get('J3'), off.get('J3'))
        self.assertNotIn('J3', res['edge_angle_chosen'])
        self.assertTrue(any(
            'so stage 1 seated it at 0deg, the next angle it chose that seats '
            'clear' in n for n in res['notes']), res['notes'])
        self.assertFalse(any('before any angle was chosen' in n
                             or 'first pad-clear seat' in n
                             for n in res['notes']), res['notes'])

    def test_determinism(self):
        a = _seed(self.board, self.intent, ('J2', 'J3'))[2]
        b = _seed(self.board, self.intent, ('J2', 'J3'))[2]
        self.assertEqual({r: a[r] for r in ('J2', 'J3')},
                         {r: b[r] for r in ('J2', 'J3')})

    def test_a_courtyard_overlapping_seat_walks_to_a_clean_angle(self):
        """A seat only the pad-only fallback finds ('clean_t2') is walked
        like a crowded one: the next angle stage 1 chose that seats clean
        wins. Forced here by making J3's first angle overlap MK1."""
        base = _seed(self.board, self.intent, ('J2', 'J3'))[1]
        first = base['edge_angle_chosen']['J3']['to']
        orig = seeder._placed_courtyard_hits

        def hits(state, ref, pose, others):
            if ref == 'J3' and abs((pose[2] - first + 180.0) % 360.0
                                   - 180.0) < 1e-6:
                return ['MK1']
            return orig(state, ref, pose, others)
        try:
            seeder._placed_courtyard_hits = hits
            _pcb, res, poses = _seed(self.board, self.intent, ('J2', 'J3'))
        finally:
            seeder._placed_courtyard_hits = orig
        self.assertGreater(abs((poses['J3'][2] - first + 180.0) % 360.0
                               - 180.0), 1e-6, (first, poses['J3']))
        self.assertTrue(any('the next angle it chose that seats clear' in n
                            for n in res['notes']), res['notes'])


class TheMatingFaceDecides(unittest.TestCase):
    """sonde_u's J2 is #1155's DSUB edge-mount: a strong face (7.4 mm)."""

    #: The band `emit_intent` writes for J2 off the designer's board: the
    #: shell overhangs the edge by ~14 mm when it faces it.
    ENTRY = {'ref': 'J2', 'edge': 'east', 'class': 'edge_receptacle',
             'overhang_mm': {'min': 0.0, 'max': 15.15}}

    def _facing(self, board, pose):
        pcb = parse_kicad_pcb(board)
        geom = board_bodies(pcb, board)['J2']
        return floorplan.edge_facing(self.ENTRY, pcb.footprints['J2'], geom,
                                     rotation=pose[2])

    def test_from_a_pile_it_is_seated_facing_its_edge(self):
        with tempfile.TemporaryDirectory() as td:
            board = _pile(td, SONDE, 's')
            intent = _intent(td, board, [dict(self.ENTRY)])
            _pcb, res, poses = _seed(board, intent, ('J2',))
            self.assertEqual(self._facing(board, poses['J2'])['facing'],
                             'east')
            self.assertFalse(self._facing(board, poses['J2'])['fails'])
            self.assertIn('J2', res['edge_angle_chosen'])
            with flags(_stage1_choose_edge_angle=False):
                _pcb, _r, off = _seed(board, intent, ('J2',))
            # blind: seated at the pile's angle, facing away -- an ERROR
            ef = self._facing(board, off['J2'])
            self.assertTrue(ef['fails'] and ef['strong'], ef)

    def test_a_placed_connector_facing_its_edge_is_not_turned(self):
        """The designer's own board: informative input, already facing."""
        with tempfile.TemporaryDirectory() as td:
            intent = _intent(td, SONDE, [dict(self.ENTRY)])
            j2 = parse_kicad_pcb(SONDE).footprints['J2']
            _pcb, res, poses = _seed(SONDE, intent, ('J2',))
        self.assertEqual(res['edge_angle_chosen'], {})
        self.assertAlmostEqual(poses['J2'][2] % 360, j2.rotation % 360)

    def _reversed(self, td):
        rev = os.path.join(td, 'rev.kicad_pcb')
        j2 = parse_kicad_pcb(SONDE).footprints['J2']
        check([PY, '-X', 'utf8', 'py_placer/place_pose.py', SONDE, rev,
               '--allow-routed', '--force', 'set', 'J2', str(j2.x),
               str(j2.y), '--rot', '90'], accept=True)
        return rev

    @staticmethod
    def _at(pose, deg):
        return abs((pose[2] - deg + 180.0) % 360.0 - 180.0) < 1e-6

    def test_a_pad_clear_seat_beats_an_input_that_seats_nothing(self):
        """The facing angle 270 is made to overlap a placed courtyard at
        every rung, so it seats only pad-clear (T2), and that seat is kept,
        facing its edge. The input 90 is never tried: it points the strong
        face away, and the attempt from it would leave J2 reversed for the
        later stages -- the east band puts it off the board (round-2
        verifier)."""
        orig = seeder._placed_courtyard_hits

        def hits(state, ref, pose, others):
            if ref == 'J2' and self._at(pose, 270.0):
                return ['J1']
            return orig(state, ref, pose, others)
        with tempfile.TemporaryDirectory() as td:
            rev = self._reversed(td)
            intent = _intent(td, rev, [dict(self.ENTRY)])
            try:
                seeder._placed_courtyard_hits = hits
                _pcb, res, poses = _seed(rev, intent, ('J2',))
            finally:
                seeder._placed_courtyard_hits = orig
            self.assertFalse(self._facing(rev, poses['J2'])['fails'])
        self.assertAlmostEqual(poses['J2'][2] % 360, 270.0)
        self.assertIn('J2', res['edge_angle_chosen'])
        self.assertTrue(any(
            'so it keeps the first pad-clear seat, at 270deg' in n
            for n in res['notes']), res['notes'])

    def test_a_crowded_chosen_angle_falls_back_and_says_it_faces_away(self):
        """Prefer-then-fall-back (phase-5 verifier, BLOCKING on the fa10
        glasgow pile): the facing angle 270 is made to CROWD a placed part's
        pads at every rung, so stage 1 keeps nothing at it and J2 gets the
        attempt it had without choosing -- from its input 90, which leaves it
        to the later stages. That angle points the strong face away, and the
        note says so (round-2 verifier: coldfire's UARTCAN203)."""
        from placement import legality
        orig = legality.LegalityContext.pair_shortfall

        def shortfall(ctx, a, b, pose_a=None, pose_b=None):
            sf = orig(ctx, a, b, pose_a, pose_b)
            if a == 'J2' and pose_a is not None and self._at(pose_a, 270.0):
                return sf._replace(pad=max(sf.pad, 1.0))
            return sf
        with tempfile.TemporaryDirectory() as td:
            rev = self._reversed(td)
            intent = _intent(td, rev, [dict(self.ENTRY)])
            try:
                legality.LegalityContext.pair_shortfall = shortfall
                _pcb, res, poses = _seed(rev, intent, ('J2',))
            finally:
                legality.LegalityContext.pair_shortfall = orig
            with flags(_stage1_choose_edge_angle=False):
                _pcb, _r, off = _seed(rev, intent, ('J2',))
        # An unmoved part has no placement record: it kept its input pose.
        self.assertEqual(poses.get('J2'), off.get('J2'))
        self.assertNotIn('J2', res['edge_angle_chosen'])
        self.assertTrue(any(
            'the attempt it had before any angle was chosen, from its input '
            '90deg, which leaves it to the later stages -- an angle at which '
            'its mating face points away from the east edge, which the '
            'facing grade reports' in n for n in res['notes']), res['notes'])

    def test_a_declared_set_is_narrowed_to_its_facing_members(self):
        """[90, 270] declared on the reversed board: 90 points J2's face
        west, so stage 1 tries 270 only."""
        with tempfile.TemporaryDirectory() as td:
            rev = self._reversed(td)
            doc = {'schema': floorplan.SCHEMA_VERSION,
                   'kind': floorplan.KIND, 'units': 'mm',
                   'edge_connectors': [dict(self.ENTRY)],
                   'blocks': [{'name': 'rotation:J2', 'refs': ['J2'],
                               'rotation_candidates': [90, 270]}]}
            p = os.path.join(td, 'set.json')
            with open(p, 'w', encoding='utf-8') as fh:
                json.dump(doc, fh)
            _pcb, res, poses = _seed(rev, floorplan.load_intent(p), ('J2',))
        self.assertAlmostEqual(poses['J2'][2] % 360, 270.0)
        self.assertTrue(any('only [270.0] point its mating face' in n
                            for n in res['notes']), res['notes'])
        # "first" means first in the facing-first order, and says so
        self.assertTrue(any('those facing it ([270.0]) tried first' in n
                            for n in res['notes']), res['notes'])

    def test_a_crowded_facing_member_never_beats_a_clean_declared_one(self):
        """[0, 270] declared on the reversed board, 270 made to CROWD a
        placed part's pads at every rung: the set is reordered, not
        narrowed, so 0 -- the declared member the author's order seats
        clean, facing north -- is still tried and kept, exactly the seat of
        the arm without #1172. A pad short is worse than a facing error, and
        the kept angle's facing is named."""
        with tempfile.TemporaryDirectory() as td:
            rev = self._reversed(td)
            doc = {'schema': floorplan.SCHEMA_VERSION,
                   'kind': floorplan.KIND, 'units': 'mm',
                   'edge_connectors': [dict(self.ENTRY)],
                   'blocks': [{'name': 'rotation:J2', 'refs': ['J2'],
                               'rotation_candidates': [0, 270]}]}
            p = os.path.join(td, 'crowd.json')
            with open(p, 'w', encoding='utf-8') as fh:
                json.dump(doc, fh)
            orig = legality.LegalityContext.pair_shortfall

            def shortfall(ctx, a, b, pose_a=None, pose_b=None):
                sf = orig(ctx, a, b, pose_a, pose_b)
                if a == 'J2' and pose_a is not None and self._at(pose_a,
                                                                 270.0):
                    return sf._replace(pad=max(sf.pad, 1.0))
                return sf
            try:
                legality.LegalityContext.pair_shortfall = shortfall
                _pcb, res, poses = _seed(rev, floorplan.load_intent(p),
                                         ('J2',))
                with flags(_stage1_choose_edge_angle=False,
                           _edge_courtyard_tier=False):
                    _pcb, _r, off = _seed(rev, floorplan.load_intent(p),
                                          ('J2',))
            finally:
                legality.LegalityContext.pair_shortfall = orig
        self.assertAlmostEqual(poses['J2'][2] % 360, 0.0)
        self.assertEqual(poses['J2'], off['J2'])
        self.assertFalse(any('conflict is left for the gate' in n
                             for n in res['notes']), res['notes'])
        self.assertTrue(any(
            'no member of its declared rotation_candidates that faces the '
            'east edge ([270.0]) seats it as well, so it keeps the declared '
            '0deg' in n for n in res['notes']), res['notes'])

    def test_a_declared_set_with_no_facing_member_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            rev = self._reversed(td)
            doc = {'schema': floorplan.SCHEMA_VERSION,
                   'kind': floorplan.KIND, 'units': 'mm',
                   'edge_connectors': [dict(self.ENTRY)],
                   'blocks': [{'name': 'rotation:J2', 'refs': ['J2'],
                               'rotation_candidates': [0, 90]}]}
            p = os.path.join(td, 'set.json')
            with open(p, 'w', encoding='utf-8') as fh:
                json.dump(doc, fh)
            _pcb, res, _poses = _seed(rev, floorplan.load_intent(p), ('J2',))
        self.assertTrue(any('does its mating face point at the east edge'
                            in n for n in res['notes']), res['notes'])

    def test_a_diagonal_member_does_not_face_the_edge(self):
        """[45, 90] on the reversed board: 45 names no edge (the grade
        abstains there) and 90 points the face west, so NO member faces east
        -- the set is not narrowed to [45] (round-2 verifier)."""
        with tempfile.TemporaryDirectory() as td:
            rev = self._reversed(td)
            doc = {'schema': floorplan.SCHEMA_VERSION,
                   'kind': floorplan.KIND, 'units': 'mm',
                   'edge_connectors': [dict(self.ENTRY)],
                   'blocks': [{'name': 'rotation:J2', 'refs': ['J2'],
                               'rotation_candidates': [45, 90]}]}
            p = os.path.join(td, 'diag.json')
            with open(p, 'w', encoding='utf-8') as fh:
                json.dump(doc, fh)
            _pcb, res, _poses = _seed(rev, floorplan.load_intent(p), ('J2',))
        self.assertFalse(any('only [45.0]' in n for n in res['notes']),
                         res['notes'])
        self.assertTrue(any('at no angle of its declared rotation_candidates '
                            '[45.0, 90.0] does its mating face point at the '
                            'east edge' in n for n in res['notes']),
                        res['notes'])

    def test_a_declared_reversed_rotation_is_kept_and_named(self):
        with tempfile.TemporaryDirectory() as td:
            rev = self._reversed(td)
            doc = {'schema': floorplan.SCHEMA_VERSION,
                   'kind': floorplan.KIND, 'units': 'mm',
                   'edge_connectors': [dict(self.ENTRY)],
                   'blocks': [{'name': 'rotation:J2', 'refs': ['J2'],
                               'rotation': 90}]}
            p = os.path.join(td, 'one.json')
            with open(p, 'w', encoding='utf-8') as fh:
                json.dump(doc, fh)
            _pcb, res, poses = _seed(rev, floorplan.load_intent(p), ('J2',))
        self.assertAlmostEqual((poses.get('J2') or (0, 0, 90.0))[2] % 360,
                               90.0)
        self.assertTrue(any('its DECLARED rotation 90deg points its mating '
                            'face west' in n for n in res['notes']),
                        res['notes'])

    def test_a_facing_angle_too_wide_for_its_edge_is_named(self):
        """sonde_u J1, a 54.1 mm DSUB-25 on a 43.18 mm west edge: its only
        facing angle, 270, cannot seat, and the refusal says why AT 270."""
        import pose_score
        with tempfile.TemporaryDirectory() as td:
            b = os.path.join(td, 'j1.kicad_pcb')
            j1 = parse_kicad_pcb(SONDE).footprints['J1']
            check([PY, '-X', 'utf8', 'py_placer/place_pose.py', SONDE, b,
                   '--allow-routed', '--force', 'set', 'J1', str(j1.x),
                   str(j1.y), '--rot', '0'], accept=True)
            st = pose_score.make_state(parse_kicad_pcb(b), b)
            notes = []
            ok = seeder._seat_edge(
                st, 'J1', {'ref': 'J1', 'edge': 'west',
                           'class': 'edge_receptacle',
                           'overhang_mm': {'min': 0.0, 'max': 27.048}},
                set(), notes)
        self.assertFalse(ok)
        self.assertTrue(any('at rotation 270deg it is wider' in n
                            for n in notes), notes)

    def test_an_exempt_class_never_drives_the_repair_ladder(self):
        import pose_score
        st = pose_score.make_state(parse_kicad_pcb(SONDE), SONDE)
        self.assertIsNone(seeder._strong_facing_rots(
            st, 'J2', dict(self.ENTRY, **{'class': 'connector_affinity'})))

    def test_repair_turns_a_reversed_connector_and_never_seats_it_reversed(
            self):
        import pose_score
        with tempfile.TemporaryDirectory() as td:
            rev = os.path.join(td, 'rev.kicad_pcb')
            j2 = parse_kicad_pcb(SONDE).footprints['J2']
            check([PY, '-X', 'utf8', 'py_placer/place_pose.py', SONDE, rev,
                   '--allow-routed', '--force', 'set', 'J2', str(j2.x),
                   str(j2.y), '--rot', '90'], accept=True)
            self.assertTrue(self._facing(rev, (0, 0, 90.0))['fails'])
            pcb = parse_kicad_pcb(rev)
            st = pose_score.make_state(pcb, rev)
            notes = []
            ok = seeder._seat_edge(st, 'J2', dict(self.ENTRY), set(), notes)
            self.assertTrue(ok, notes)
            self.assertAlmostEqual(st.parts['J2'].rot % 360, 270.0)
            self.assertTrue(any('#1155' in n for n in notes), notes)
            # blind twin: without the facing ladder it is seated reversed
            st2 = pose_score.make_state(parse_kicad_pcb(rev), rev)
            orig = seeder._strong_facing_rots
            try:
                seeder._strong_facing_rots = lambda *a, **k: None
                seeder._seat_edge(st2, 'J2', dict(self.ENTRY), set(), [])
            finally:
                seeder._strong_facing_rots = orig
            self.assertAlmostEqual(st2.parts['J2'].rot % 360, 90.0)


class TheCourtyardTierReading(unittest.TestCase):
    """`_placed_courtyard_hits` itself, on glasgow's pile."""

    @classmethod
    def setUpClass(cls):
        import pose_score
        cls.td = tempfile.mkdtemp()
        cls.board = _pile(cls.td, GLASGOW, 'g')
        cls.st = pose_score.make_state(parse_kicad_pcb(cls.board), cls.board)
        j2 = cls.st.parts['J2']
        cls.at_j2 = (j2.x, j2.y, j2.rot)

    def test_a_placed_courtyard_is_a_hit(self):
        self.assertEqual(seeder._placed_courtyard_hits(
            self.st, 'J3', self.at_j2, {'J2'}), ['J2'])

    def test_only_the_parts_it_is_handed_count(self):
        """Stage 1 hands it `placed`, never the pile."""
        self.assertEqual(seeder._placed_courtyard_hits(
            self.st, 'J3', self.at_j2, set()), [])

    def test_disjoint_courtyards_are_not_a_hit(self):
        x1, y1 = self.st.board[2], self.st.board[3]
        self.assertEqual(seeder._placed_courtyard_hits(
            self.st, 'J3', (x1 + 200.0, y1 + 200.0, 0.0), {'J2'}), [])

    def test_a_project_ignoring_courtyards_reads_none(self):
        try:
            self.st.courtyards_ignored = True
            self.assertEqual(seeder._placed_courtyard_hits(
                self.st, 'J3', self.at_j2, {'J2'}), [])
        finally:
            self.st.courtyards_ignored = False


class StageOneReadsTheInput(unittest.TestCase):
    """`_stage1_claims` and `_uninformative_refs`, called directly."""

    SONDE_J2 = {'ref': 'J2', 'edge': 'east', 'class': 'edge_receptacle',
                'overhang_mm': {'min': 0.0, 'max': 15.15}}

    def _claims(self, board, entries, uninformative):
        import pose_score
        pcb = parse_kicad_pcb(board)
        st = pose_score.make_state(pcb, board)
        by_edge = {}
        for c in entries:
            by_edge.setdefault(c['edge'], []).append(dict(c))
        claims, why = seeder._stage1_claims(st, by_edge, {}, st.board, [],
                                            lambda: set(uninformative))
        return claims, why

    def test_an_informative_input_keeps_its_angle(self):
        claims, _ = self._claims(GLASGOW, GLASGOW_CLAIMS, ())
        self.assertEqual(claims, {})

    def test_an_uninformative_one_lies_along_its_edge_first(self):
        claims, _ = self._claims(GLASGOW, GLASGOW_CLAIMS, ('J2', 'J3'))
        self.assertEqual(sorted(claims), ['J2', 'J3'])
        for ref, cl in claims.items():
            self.assertIsInstance(cl, seeder._ChosenRot)
            w, h = _extent(GLASGOW, ref, (0, 0, cl.candidates[0]))
            self.assertGreater(w, h, (ref, cl))

    def test_a_strong_face_offers_only_its_facing_angle(self):
        """Uninformative but already facing: the reversed angles are not
        candidates at all, so no fallback can seat it reversed."""
        claims, _ = self._claims(SONDE, [self.SONDE_J2], ('J2',))
        self.assertEqual(tuple(claims['J2'].candidates), (270.0,))

    def test_a_reversed_strong_face_is_turned_on_a_placed_board(self):
        with tempfile.TemporaryDirectory() as td:
            rev = os.path.join(td, 'rev.kicad_pcb')
            j2 = parse_kicad_pcb(SONDE).footprints['J2']
            check([PY, '-X', 'utf8', 'py_placer/place_pose.py', SONDE, rev,
                   '--allow-routed', '--force', 'set', 'J2', str(j2.x),
                   str(j2.y), '--rot', '90'], accept=True)
            claims, why = self._claims(rev, [self.SONDE_J2], ())
        self.assertEqual(tuple(claims['J2'].candidates), (270.0,))
        self.assertIn('pointed west', why['J2'])

    def test_a_weak_face_orders_the_turns_but_never_narrows_them(self):
        """The Neutrik NMJ6HCD3's weak face (1.36 mm) is WRONG: it may put
        its facing turn first, never remove the others -- a strong face's
        only-facing rule would leave a jack nothing but its reversed angle."""
        jacks = os.path.join(ROOT, 'tests', 'fixtures',
                             '1155_stock_jacks.kicad_pcb')
        claims, _ = self._claims(jacks, [{'ref': 'J3', 'edge': 'east',
                                          'class': 'edge_receptacle'}],
                                 ('J3',))
        self.assertEqual(sorted(claims['J3'].candidates),
                         [0.0, 90.0, 180.0, 270.0])

    def test_a_pile_is_uninformative_and_a_placed_board_is_not(self):
        import pose_score
        from types import SimpleNamespace
        st = pose_score.make_state(parse_kicad_pcb(GLASGOW), GLASGOW)
        refs = ['J2', 'J3']
        self.assertEqual(seeder._uninformative_refs(
            st, SimpleNamespace(pile=True, stacked_suspect_refs=()), refs),
            set(refs))
        self.assertEqual(seeder._uninformative_refs(
            st, SimpleNamespace(pile=False, stacked_suspect_refs=()), refs),
            set())

    def test_a_stacked_suspect_is_uninformative(self):
        import pose_score
        from types import SimpleNamespace
        st = pose_score.make_state(parse_kicad_pcb(GLASGOW), GLASGOW)
        self.assertEqual(seeder._uninformative_refs(
            st, SimpleNamespace(pile=False, stacked_suspect_refs=('J2',)),
            ['J2', 'J3']), {'J2'})

    def test_a_part_with_every_pad_off_the_outline_is_uninformative(self):
        """All pads, not any: a designer's connector grazing the edge keeps
        its angle; one parked off the board does not."""
        import pose_score
        from types import SimpleNamespace
        st = pose_score.make_state(parse_kicad_pcb(GLASGOW), GLASGOW)
        j3 = st.parts['J3']
        st.apply_move('J3', j3.x + 500.0, j3.y, j3.rot)
        self.assertEqual(seeder._uninformative_refs(
            st, SimpleNamespace(pile=False, stacked_suspect_refs=()),
            ['J2', 'J3']), {'J3'})


class AWeakFaceDecidesNothing(unittest.TestCase):
    """The repair ladder turns only a STRONG face: the Neutrik NMJ6HCD3's
    weak face (1.36 mm) is wrong, and turning by it would reverse a jack
    seated correctly."""

    def test_only_a_strong_face_drives_the_repair_ladder(self):
        import pose_score
        jacks = os.path.join(ROOT, 'tests', 'fixtures',
                             '1155_stock_jacks.kicad_pcb')
        st = pose_score.make_state(parse_kicad_pcb(jacks), jacks)
        weak = {'ref': 'J3', 'edge': 'east', 'class': 'edge_receptacle'}
        self.assertIsNone(seeder._strong_facing_rots(st, 'J3', weak))
        sonde = pose_score.make_state(parse_kicad_pcb(SONDE), SONDE)
        self.assertEqual(seeder._strong_facing_rots(
            sonde, 'J2', dict(StageOneReadsTheInput.SONDE_J2)), [270.0])


class PackedStarts(unittest.TestCase):
    """kit-dev-coldfire's north edge: five claims (three 31 mm D-subs since
    #1176) whose equal starts overlap; they start packed by width -- when
    the opt-in switch is on."""

    def test_it_is_opt_in(self):
        """Off by default, an opt-in. It ignores the parts already on the
        band and the declared zones (#1244)."""
        self.assertIs(seeder._stage1_packed_starts, False)

    def test_the_north_claims_start_packed_in_seat_order(self):
        import pose_score
        board = os.path.join(KF, 'kit-dev-coldfire-xilinx_5213.kicad_pcb')
        pcb = parse_kicad_pcb(board)
        doc = floorplan.emit_intent(pcb, board)
        it = floorplan.intent_from_dict(doc, source_path='e')
        specs = sorted((c for c in it.edge_claims()
                        if c.get('edge') == 'north'), key=lambda c: c['ref'])
        self.assertEqual(len(specs), 5)
        st = pose_score.make_state(pcb, board)
        starts = seeder._packed_starts(st, specs, 'north', st.board,
                                       lambda ref: None)
        self.assertEqual(sorted(starts), [c['ref'] for c in specs])
        fr = [starts[c['ref']] for c in specs]
        self.assertEqual(fr, sorted(fr))
        self.assertTrue(all(0.0 < f < 1.0 for f in fr), fr)
        # equal starts that do not overlap are kept: two of them, far apart
        far = [c for c in specs if c['ref'] in ('J201', 'UARTCAN203')]
        self.assertEqual(seeder._packed_starts(st, far, 'north', st.board,
                                               lambda ref: None), {})
        # a declared position anywhere on the edge keeps the equal starts
        placed = [dict(specs[0], center_on_edge=0.0)] + specs[1:]
        self.assertEqual(seeder._packed_starts(st, placed, 'north',
                                               st.board, lambda ref: None),
                         {})


def _rect_part(ref, x, y, cy, pads, *, locked=False, name='t:R'):
    """A footprint with courtyard `cy` (x0, y0, x1, y1, local) and `pads`
    [(x, y, sx, sy, net)], at (x, y, 0)."""
    lk = '    (locked yes)\n' if locked else ''
    body = ''.join(f'    (pad "{i + 1}" smd rect (at {px} {py}) (size {sx} '
                   f'{sy}) (layers "F.Cu") (net {n} "N{n}"))\n'
                   for i, (px, py, sx, sy, n) in enumerate(pads))
    return (f'  (footprint "{name}" (layer "F.Cu") (at {x} {y})\n' + lk +
            f'    (property "Reference" "{ref}" (at 0 0) (layer "F.SilkS"))\n'
            f'    (fp_rect (start {cy[0]} {cy[1]}) (end {cy[2]} {cy[3]})'
            f' (stroke (width 0.05) (type default)) (layer "F.CrtYd"))\n'
            + body + '  )\n')


def _board(td, parts, w=40.0, h=40.0, name='b'):
    path = os.path.join(td, name + '.kicad_pcb')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('(kicad_pcb (version 20240108) (generator pcbnew)\n'
                 '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal)'
                 ' (44 "Edge.Cuts" user))\n'
                 '  (net 0 "") (net 1 "N1") (net 2 "N2") (net 3 "N3")\n'
                 f'  (gr_rect (start 0 0) (end {w} {h}) (stroke (width 0.1)'
                 ' (type default)) (layer "Edge.Cuts"))\n'
                 + ''.join(parts) + ')\n')
    return path


class TheFallBack(unittest.TestCase):
    """The round-3 verifier's inputs: what stage 1 keeps when its own angles
    do not seat clean, and what the note says it did."""

    def test_an_angle_is_deeper_than_the_board_only_past_its_band(self):
        """J1 is 4 x 12 on an 11 mm board, parked off it (its input angle
        means nothing); two locked flanks leave a 5 mm gap on the north
        band. Its claim lets it overhang 5 mm, so the input 0 (12 deep) is
        NOT deeper than the board, stays in the set, and is the angle that
        seats clean in the gap -- where dropping it kept a quarter turn over
        R2's courtyard (24 mm2)."""
        with tempfile.TemporaryDirectory() as td:
            b = _board(td, [
                _rect_part('J1', 60, 60, (-2, -6, 2, 6), [(0, 0, .6, .6, 2)]),
                _rect_part('R1', 8.75, 1.8, (-8.75, -1.2, 8.75, 1.2),
                           [(-7.75, 0.2, .6, .6, 1)], locked=True),
                _rect_part('R2', 31.25, 1.8, (-8.75, -1.2, 8.75, 1.2),
                           [(7.75, 0.2, .6, .6, 3)], locked=True)], h=11.0)
            intent = _intent(td, b, [{'ref': 'J1', 'edge': 'north',
                                      'overhang_mm': {'min': 0, 'max': 5}}])
            _pcb, res, poses = _seed(b, intent, ('J1',))
        self.assertAlmostEqual(poses['J1'][2] % 360, 0.0)
        self.assertTrue(any('so stage 1 seated it at 0deg' in n
                            for n in res['notes']), res['notes'])

    def test_a_crowded_input_attempt_is_named_as_crowded(self):
        """Every angle crowds a locked R1 whose 38 mm pad runs along the
        north band, so the input attempt runs and crowds too -- kept, as it
        was before #1172, and the note says CROWDING, with no facing clause
        (the box has no face)."""
        with tempfile.TemporaryDirectory() as td:
            b = _board(td, [
                _rect_part('J1', 60, 60, (-2, -2, 2, 2), [(0, 0, .6, .6, 2)]),
                _rect_part('R1', 20, 2.2, (-19.5, -0.3, 19.5, 0.3),
                           [(0, 0, 38, 0.4, 1)], locked=True)], h=20.0)
            intent = _intent(td, b, [{'ref': 'J1', 'edge': 'north',
                                      'overhang_mm': {'min': 0, 'max': 1}}])
            _pcb, res, _poses = _seed(b, intent, ('J1',))
        fell = [n for n in res['notes'] if 'before any angle was chosen' in n]
        self.assertEqual(len(fell), 1, res['notes'])
        self.assertIn('from its input 0deg, which seats it crowding what is '
                      'placed (#1172)', fell[0])
        self.assertNotIn('points away', fell[0])

    def test_a_clean_reversed_input_is_named_as_facing_away(self):
        """A strong face (the courtyard reaches 7 mm one way from the pad
        row, 2 mm the other) seated on the board at 0, facing SOUTH, claimed
        on the north edge: stage 1 chooses 180. 180 is made to crowd a
        placed part's pads at every rung, so the input attempt runs -- and
        seats clean, reversed. The note says both."""
        with tempfile.TemporaryDirectory() as td:
            b = _board(td, [
                _rect_part('J1', 20, 20, (-3, -2, 3, 7),
                           [(-1, 0, .6, .6, 2), (1, 0, .6, .6, 1)],
                           name='t:JACK'),
                _rect_part('R1', 30, 30, (-1, -0.6, 1, 0.6),
                           [(-0.5, 0, .5, .6, 2), (0.5, 0, .5, .6, 1)])])
            # A band whose centre (0.5 mm) keeps the reversed part's pads on
            # the board, so the input attempt can seat.
            entry = {'ref': 'J1', 'edge': 'north', 'class': 'edge_receptacle',
                     'overhang_mm': {'min': 0, 'max': 1}}
            pcb = parse_kicad_pcb(b)
            ef = floorplan.edge_facing(entry, pcb.footprints['J1'],
                                       board_bodies(pcb, b)['J1'],
                                       rotation=0.0)
            self.assertTrue(ef['fails'] and ef['strong'], ef)   # the premise
            intent = _intent(td, b, [entry])
            orig = legality.LegalityContext.pair_shortfall

            def shortfall(ctx, a, bb, pose_a=None, pose_b=None):
                sf = orig(ctx, a, bb, pose_a, pose_b)
                if (a == 'J1' and pose_a is not None
                        and abs((pose_a[2] - 180.0 + 180.0) % 360.0 - 180.0)
                        < 1e-6):
                    return sf._replace(pad=max(sf.pad, 1.0))
                return sf
            try:
                legality.LegalityContext.pair_shortfall = shortfall
                _pcb, res, _poses = _seed(b, intent, ('J1',))
            finally:
                legality.LegalityContext.pair_shortfall = orig
        fell = [n for n in res['notes'] if 'before any angle was chosen' in n]
        self.assertEqual(len(fell), 1, res['notes'])
        self.assertIn('from its input 0deg, which seats it clear -- an angle '
                      'at which its mating face points away from the north '
                      'edge, which the facing grade reports', fell[0])
        self.assertNotIn('J1', res['edge_angle_chosen'])

    def test_the_first_pad_clear_seat_can_be_a_later_angle(self):
        """glasgow's J3 from the pile: every rung overlaps a placed
        courtyard, and its first chosen angle (90) also crowds a placed
        part's pads, so the first PAD-CLEAR seat is at 270, the second angle
        it chose -- and that is the one kept, not a redo of the first."""
        with tempfile.TemporaryDirectory() as td:
            ops = []
            for ref, (x, y) in MK.items():
                ops += ['set', ref, str(x), str(y), '--rot', '0']
            ops += ['lock'] + list(MK)
            board = _pile(td, GLASGOW, 'g', *ops)
            intent = _intent(td, board, GLASGOW_CLAIMS)
            hits0 = seeder._placed_courtyard_hits
            sf0 = legality.LegalityContext.pair_shortfall

            def shortfall(ctx, a, bb, pose_a=None, pose_b=None):
                sf = sf0(ctx, a, bb, pose_a, pose_b)
                if (a == 'J3' and pose_a is not None
                        and abs((pose_a[2] - 90.0 + 180.0) % 360.0 - 180.0)
                        < 1e-6):
                    return sf._replace(pad=max(sf.pad, 1.0))
                return sf
            try:
                seeder._placed_courtyard_hits = (
                    lambda state, ref, pose, others:
                    ['MK1'] if ref == 'J3' else hits0(state, ref, pose,
                                                      others))
                legality.LegalityContext.pair_shortfall = shortfall
                _pcb, res, poses = _seed(board, intent, ('J3',))
            finally:
                seeder._placed_courtyard_hits = hits0
                legality.LegalityContext.pair_shortfall = sf0
        self.assertEqual(res['edge_angle_chosen']['J3']['candidates'][:2],
                         [90.0, 270.0])
        self.assertAlmostEqual(poses['J3'][2] % 360, 270.0)
        self.assertTrue(any('so it keeps the first pad-clear seat, at 270deg'
                            in n for n in res['notes']), res['notes'])


    def test_a_chosen_angle_too_wide_for_the_edge_falls_back(self):
        """A strong-faced jack 20 mm along its pad row on a 15 mm wide board,
        input 90 -- facing east, across its north claim. Its facing angle,
        180, is wider than the edge, so stage 1 refuses it before the turn;
        the part still gets the attempt from its input angle, which seats it
        on its edge exactly as the arm without #1172 does, instead of being
        left to the later stages and parked in the interior. The note says
        the chosen angle does not fit, and that the kept one faces away."""
        with tempfile.TemporaryDirectory() as td:
            b = _board(td, [
                _rect_part('J1', 7.5, 20, (-10, -2, 10, 7),
                           [(-1, 0, .6, .6, 2), (1, 0, .6, .6, 1)],
                           name='t:JACK')], w=15.0, h=40.0)
            b90 = os.path.join(td, 'b90.kicad_pcb')
            check([PY, '-X', 'utf8', 'py_placer/place_pose.py', b, b90,
                   '--force', 'set', 'J1', '7.5', '20', '--rot', '90'],
                  accept=True)
            intent = _intent(td, b90, [{'ref': 'J1', 'edge': 'north',
                                        'class': 'edge_receptacle',
                                        'overhang_mm': {'min': 0, 'max': 1}}])
            _pcb, res, poses = _seed(b90, intent, ('J1',))
            with flags(_stage1_choose_edge_angle=False,
                       _edge_courtyard_tier=False):
                _pcb, _r, off = _seed(b90, intent, ('J1',))
        self.assertEqual(poses['J1'], off['J1'])
        self.assertNotIn('J1', res['unseated'])
        fell = [n for n in res['notes'] if 'before any angle was chosen' in n]
        self.assertEqual(len(fell), 1, res['notes'])
        self.assertIn('no angle stage 1 chose ([180.0]) fits the north edge',
                      fell[0])
        self.assertIn('which seats it clear -- an angle at which its mating '
                      'face points away from the north edge', fell[0])

    def _floor_board(self, td, blocks=()):
        """J1, a faceless 4 x 4 box, claimed on the north edge of the
        standard 40 mm board; R1 (locked) has a courtyard over the middle of
        the band and its pad far below it, so the centre rung is pad-clear
        but overlaps R1's courtyard and every rung clear of it is off the
        centre."""
        b = _board(td, [
            _rect_part('J1', 20, 30, (-2, -2, 2, 2),
                       [(-1, 0, .6, .6, 2), (1, 0, .6, .6, 1)]),
            _rect_part('R1', 20, 3, (-0.5, -3, 0.5, 1),
                       [(0, 0.5, .4, .4, 3)], locked=True)])
        doc = {'schema': floorplan.SCHEMA_VERSION, 'kind': floorplan.KIND,
               'units': 'mm',
               'edge_connectors': [{'ref': 'J1', 'edge': 'north',
                                    'overhang_mm': {'min': 0, 'max': 1}}],
               'blocks': list(blocks)}
        p = os.path.join(td, 'floor.json')
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh)
        return b, floorplan.load_intent(p)

    @staticmethod
    def _short_where(pred):
        """`seeder._floor_at`, reading pad copper 0.3 mm inside the floor at
        every J1 pose `pred(x, rot)` names (a measured shortfall's shape)."""
        fa = seeder._floor_at

        def floor_at(state, ref, x, y, rot):
            f = fa(state, ref, x, y, rot)
            if ref == 'J1' and f is not None and pred(round(x, 3), rot):
                return f._replace(short=((0.3, 0, 'east', 0.25, '2'),))
            return f
        return fa, floor_at

    def test_a_floor_clear_seat_outranks_a_courtyard_clear_one(self):
        """Pad copper at the board edge outranks a courtyard overlap: every
        rung off the centre reads copper inside the board-edge floor, so the
        only seats clear of R1's courtyard are short of it. The tier keeps
        the centre's floor-clear seat over R1's courtyard -- the seat of the
        arm without the tier -- and says why."""
        with tempfile.TemporaryDirectory() as td:
            b, intent = self._floor_board(td)
            fa, short = self._short_where(lambda x, rot: abs(x - 20.0) > 1e-6)
            try:
                seeder._floor_at = short
                _pcb, res, poses = _seed(b, intent, ('J1',))
                with flags(_edge_courtyard_tier=False):
                    _pcb, _r, off = _seed(b, intent, ('J1',))
            finally:
                seeder._floor_at = fa
        self.assertEqual(poses['J1'], off['J1'])
        self.assertAlmostEqual(poses['J1'][0], 20.0)
        self.assertNotIn('J1', res['edge_floor_fallback'])
        self.assertTrue(any('leaves pad copper inside the board-edge floor, '
                            'so it keeps a pad-clear seat whose copper '
                            'clears the floor and that overlaps it' in n
                            for n in res['notes']), res['notes'])

    def test_a_walked_member_short_of_the_floor_does_not_win(self):
        """The same across a declared set [0, 90]: at 0 the floor-clear seat
        overlaps R1's courtyard; at 90 every seat is short of the floor, the
        one clear of R1 included. The #1125 walk does not take 90's
        courtyard-clear seat over 0's floor-clear one."""
        with tempfile.TemporaryDirectory() as td:
            b, intent = self._floor_board(
                td, [{'name': 'rotation:J1', 'refs': ['J1'],
                      'rotation_candidates': [0, 90]}])
            fa, short = self._short_where(
                lambda x, rot: (abs((rot - 90.0 + 180.0) % 360.0 - 180.0)
                                < 1e-6 or abs(x - 20.0) > 1e-6))
            try:
                seeder._floor_at = short
                _pcb, res, poses = _seed(b, intent, ('J1',))
                with flags(_edge_courtyard_tier=False):
                    _pcb, _r, off = _seed(b, intent, ('J1',))
            finally:
                seeder._floor_at = fa
        self.assertEqual(poses['J1'], off['J1'])
        self.assertAlmostEqual(poses['J1'][2] % 360, 0.0)
        self.assertNotIn('J1', res['edge_floor_fallback'])


if __name__ == '__main__':
    unittest.main()
