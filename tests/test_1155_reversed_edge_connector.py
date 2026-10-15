"""#1155: a reversed edge-mount connector fails the edge rule, and every
grader that reads it says so.

sonde_xilinx's J2 (`DSUB-9_Male_EdgeMount`, declared east) seated backwards --
B side, rot 90, its 18 mm shell over the board and its mating face pointing
inward -- passed check_floorplan's `edge_connector`, board_context's "Mating
face", check_assembly and board_score: every conjunct read the courtyard's
NEAREST edge, which is east either way. The facing conjunct
(`floorplan.edge_facing`) reads which way the body leaves the pad row.

The fixture is the tracked `sonde_u` board, which carries the same footprint
seated correctly (rot -90), copied and posed reversed with place_pose.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ('py_placer', 'py_router', 'py_tools', 'tests'):
    sys.path.insert(0, os.path.join(ROOT, _p))

from kicad_parser import parse_kicad_pcb                      # noqa: E402
from placement import floorplan                              # noqa: E402
from run_utils import check                                  # noqa: E402

PY = sys.executable
SONDE = os.path.join(ROOT, 'kicad_files', 'sonde_u.kicad_pcb')
JACKS = os.path.join(ROOT, 'tests', 'fixtures', '1155_stock_jacks.kicad_pcb')
FA10 = os.path.expanduser('~/Documents/prive/git/krt-fa10/wk/fa10/'
                          '02_sonde_xilinx/repro')
WEST = "J2's body leaves its pad row toward the west"


def _write(td, name, doc):
    p = os.path.join(td, name)
    with open(p, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh)
    return p


def _intent(entry):
    return {'schema': floorplan.SCHEMA_VERSION, 'kind': floorplan.KIND,
            'units': 'mm', 'edge_connectors': [entry]}


class TheReversedDsub(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.td, True)
        j2 = parse_kicad_pcb(SONDE).footprints['J2']
        cls.correct_rot = j2.rotation
        cls.reversed = os.path.join(cls.td, 'reversed.kicad_pcb')
        check([PY, '-X', 'utf8', 'py_placer/place_pose.py', SONDE,
               cls.reversed, '--allow-routed', '--force', 'set', 'J2',
               str(j2.x), str(j2.y), '--rot', '90'], accept=True)

    def _grade(self, board, entry, **kw):
        return check([PY, '-X', 'utf8', 'py_tools/check_floorplan.py', board,
                      '--intent', _write(self.td, 'i.json', _intent(entry))],
                     **kw)

    def test_reversed_fails_declared_or_inferred(self):
        for entry in ({'ref': 'J2', 'edge': 'east'},
                      {'ref': 'J2', 'edge': 'east',
                       'class': 'edge_receptacle'}):
            r = self._grade(self.reversed, entry, refuse=WEST, code=4)
            self.assertIn('[ERROR] edge_connector', r.stdout)
            self.assertIn('facing west [courtyard+fab, strong] FAIL',
                          r.stdout)

    def test_the_correct_pose_passes(self):
        self.assertAlmostEqual(self.correct_rot % 360, 270.0)
        r = self._grade(SONDE, {'ref': 'J2', 'edge': 'east'}, accept=True)
        self.assertIn('facing east [courtyard+fab, strong] pass', r.stdout)

    def test_an_exempt_class_is_not_graded(self):
        r = self._grade(self.reversed, {'ref': 'J2', 'edge': 'east',
                                        'class': 'connector_affinity'},
                        accept=True)
        self.assertNotIn(WEST, r.stdout)

    def test_board_score_counts_it_blocking(self):
        ipath = _write(self.td, 'bs.json', _intent({'ref': 'J2',
                                                     'edge': 'east'}))
        scores = {}
        for name, board in (('correct', SONDE), ('reversed', self.reversed)):
            p = subprocess.run(
                [PY, '-X', 'utf8', 'py_tools/board_score.py', board,
                 '--intent', ipath],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', cwd=ROOT, timeout=900)
            line = next(ln for ln in p.stdout.splitlines()
                        if ln.startswith('SCORE_JSON='))
            scores[name] = json.loads(line.split('=', 1)[1])
        self.assertEqual(scores['reversed']['blocking_by']['floorplan'],
                         scores['correct']['blocking_by']['floorplan'] + 1)

    def test_board_context_names_the_reversed_face(self):
        p = subprocess.run(
            [PY, '-X', 'utf8', 'py_tools/board_context.py', self.reversed,
             '--md'], capture_output=True, text=True, encoding='utf-8',
            errors='replace', cwd=ROOT, timeout=900)
        block = p.stdout[p.stdout.index('### J2'):][:1500]
        self.assertIn('Mating face **W**', block)
        self.assertIn('nearest the **E** edge', block)
        self.assertIn('a reversed pose', block)

    def test_the_emitter_never_blesses_the_reversed_pose(self):
        """J2's back overhangs the east edge, so the emitter observes it --
        and withholds the edge as SUSPECT (run-5 suspect-and-derive, run-8
        A3's machine-readable flag): reversed on the east edge, or a
        west-edge part displaced, the board cannot say which."""
        pcb = parse_kicad_pcb(self.reversed)
        for dc in (False, True):
            doc = floorplan.emit_intent(pcb, self.reversed, declare_classes=dc)
            j2 = [c for c in doc['edge_connectors'] if c['ref'] == 'J2']
            self.assertEqual(len(j2), 1, doc['edge_connectors'])
            self.assertNotIn('edge', j2[0])
            self.assertIs(j2[0].get('suspect'), True)
            self.assertIn('reversed pose', j2[0]['suspect_reason'])
            self.assertIn('SUSPECT', j2[0]['note'])
            r = floorplan.grade(
                floorplan.intent_from_dict(doc, source_path='e'), pcb,
                self.reversed)
            self.assertEqual([v.message for v in r.errors
                              if v.rule == 'edge_connector'], [])

    def test_a_withheld_edge_keeps_the_note_it_had(self):
        entry = {'ref': 'J2', 'edge': 'east', 'note': 'band max from the body'}
        floorplan._withhold_reversed_edge(
            entry, parse_kicad_pcb(self.reversed), self.reversed, {})
        self.assertTrue(entry['note'].startswith('band max from the body; '))
        self.assertIn('SUSPECT', entry['note'])

    def test_the_sheet_measures_to_the_facing_edge(self):
        """`dist_mm` / `overhang_mm` go with `edge` (the facing one);
        `nearest_*` with the nearest."""
        from board_context import mating_faces
        m = mating_faces(parse_kicad_pcb(self.reversed), self.reversed,
                         0.2)['J2']
        self.assertEqual((m['edge'], m['edge_basis'], m['nearest_edge']),
                         ('W', 'facing', 'E'))
        self.assertLess(m['nearest_dist_mm'], 1.0)
        self.assertGreater(m['dist_mm'], 30.0)
        self.assertEqual(m['overhang_mm'], 0.0)
        self.assertGreater(m['nearest_overhang_mm'], 0.0)

    def test_an_entry_with_no_edge_is_not_graded_or_passed(self):
        doc = _intent({'ref': 'J2', 'class': 'edge_receptacle'})
        r = floorplan.grade(floorplan.intent_from_dict(doc, source_path='e'),
                            parse_kicad_pcb(self.reversed), self.reversed)
        ev = [e for e in r.edge_connector_evidence if e['ref'] == 'J2']
        self.assertEqual(ev[0]['facing']['disposition'], 'no_edge')
        tail = floorplan._facing_tail(ev[0]['facing'])
        self.assertIn('not graded', tail)
        self.assertNotIn('pass', tail)


class TheDiagonalPose(unittest.TestCase):
    """J2 at 45 degrees: a strong face, but the pose names no edge."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.td, True)
        j2 = parse_kicad_pcb(SONDE).footprints['J2']
        cls.board = os.path.join(cls.td, 'diag.kicad_pcb')
        check([PY, '-X', 'utf8', 'py_placer/place_pose.py', SONDE,
               cls.board, '--allow-routed', '--force', 'set', 'J2',
               str(j2.x - 20.0), str(j2.y), '--rot', '45'], accept=True)

    def test_the_grade_says_why_it_abstains(self):
        from placement.body import board_bodies
        pcb = parse_kicad_pcb(self.board)
        ef = floorplan.edge_facing({'ref': 'J2', 'edge': 'east'},
                                   pcb.footprints['J2'],
                                   board_bodies(pcb, self.board)['J2'])
        self.assertIsNone(ef['facing'])
        self.assertFalse(ef['strong'])
        self.assertIn('quarter turn', ef['reason'])

    def test_the_sheet_says_why_it_reads_the_nearest_edge(self):
        from board_context import mating_faces, mating_line
        m = mating_faces(parse_kicad_pcb(self.board), self.board, 0.2)['J2']
        self.assertEqual(m['edge_basis'], 'nearest')
        self.assertFalse(m['facing_strong'])
        self.assertIn('quarter turn', mating_line(m))


class AWeakFaceIsAWarning(unittest.TestCase):
    """A stock jack whose weak face is WRONG (Neutrik NMJ6HCD3, opening
    east, face read -x at 1.36 mm): seated correctly, it is a WARN, never
    an ERROR -- the tiering the stock-library sweep forced."""

    def test_the_wrong_weak_face_warns(self):
        doc = _intent({'ref': 'J3', 'edge': 'east',
                       'class': 'edge_receptacle'})
        r = floorplan.grade(floorplan.intent_from_dict(doc, source_path='e'),
                            parse_kicad_pcb(JACKS), JACKS)
        facing = [v for v in r.violations if v.rule == 'edge_connector'
                  and 'leaves its pad row' in v.message]
        self.assertEqual(len(facing), 1, [v.message for v in r.violations])
        self.assertEqual(facing[0].severity, floorplan.WARN)
        self.assertFalse(facing[0].measured['facing_strong'])

    def test_a_weak_face_keeps_its_emitted_edge(self):
        """Seated at the east edge it opens out of, J3's wrong weak face
        does not withhold its edge: only a strong face is suspect."""
        with tempfile.TemporaryDirectory() as td:
            board = os.path.join(td, 'j3east.kicad_pcb')
            check([PY, '-X', 'utf8', 'py_placer/place_pose.py', JACKS, board,
                   '--force', 'set', 'J3', '181.75', '11'], accept=True)
            pcb = parse_kicad_pcb(board)
            doc = floorplan.emit_intent(pcb, board)
            j3 = [c for c in doc['edge_connectors'] if c['ref'] == 'J3']
            self.assertEqual([(c.get('edge'), c.get('suspect')) for c in j3],
                             [('east', None)])
            r = floorplan.grade(
                floorplan.intent_from_dict(doc, source_path='e'), pcb, board)
        self.assertEqual(r.errors, [])
        self.assertEqual(len([v for v in r.violations if v.ref == 'J3'
                              and 'leaves its pad row' in v.message]), 1)


class TheSheetSentence(unittest.TestCase):
    """board_context's sentence: only an OPPOSITE strong face is reversed."""

    ROW = {'edge': 'W', 'edge_basis': 'facing', 'dist_mm': 30.0,
           'overhang_mm': 0.0, 'interior': False, 'nearest_edge': 'E',
           'nearest_dist_mm': 0.0, 'nearest_overhang_mm': 0.26,
           'facing_basis': 'courtyard+fab', 'facing_strong': True}

    def _line(self, **kw):
        from board_context import mating_line
        return mating_line(dict(self.ROW, **kw))

    def test_opposite_and_strong_is_reversed(self):
        self.assertIn('a reversed pose', self._line())

    def test_opposite_and_weak_is_a_question(self):
        line = self._line(facing_strong=False)
        self.assertNotIn('reversed', line)
        self.assertIn('weak face', line)

    def test_perpendicular_opens_along_its_edge(self):
        """A corner part: opening east, sitting nearest north."""
        line = self._line(edge='E', nearest_edge='N')
        self.assertNotIn('reversed', line)
        self.assertIn('along the N edge rather than out of it', line)
        self.assertNotIn('weak face', line)

    def test_a_weak_perpendicular_face_says_it_is_weak(self):
        """The stock jacks' wrong weak faces read perpendicular to their
        edge; the sentence must not state that as a fact."""
        line = self._line(edge='E', nearest_edge='N', facing_strong=False)
        self.assertIn('courtyard+fab, a weak face)', line)

    def test_interior_survives_the_facing_branch(self):
        line = self._line(edge='E', nearest_edge='N', interior=True)
        self.assertIn('INTERIOR for a connector', line)

    def test_no_face_reads_the_nearest_edge(self):
        line = self._line(edge='E', edge_basis='nearest', dist_mm=0.0,
                          overhang_mm=0.26)
        self.assertIn('Mating face **E** (nearest edge; the body names no '
                      'face)', line)


class TheIssueRepro(unittest.TestCase):

    def test_the_fa10_seed(self):
        board = os.path.join(FA10, 's0_seed.kicad_pcb')
        intent = os.path.join(FA10, 'intent.json')
        if not (os.path.isfile(board) and os.path.isfile(intent)):
            self.skipTest('the fa10 sonde_xilinx repro is not on this machine '
                          '(it is not tracked)')
        check([PY, '-X', 'utf8', 'py_tools/check_floorplan.py', board,
               '--intent', intent], refuse=WEST, code=4)


if __name__ == '__main__':
    unittest.main()
