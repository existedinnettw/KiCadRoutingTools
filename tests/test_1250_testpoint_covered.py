#!/usr/bin/env python3
"""#1250: a test point a move buried under another part's body gates.

The marker waiver (`legality._MARKER_NONPHYSICAL`) excused a test point's
courtyard pair and `_GATE_EXEMPT` its containment, on the premise that a test
point has nothing above board level. Nothing asked the other question, whether
something now sits over the test point. glasgow_revC's TP6 (`/CLKREF`), moved
39.7 mm between U19's pad rows, read `waived:marker_class CONTAINED 100%` and
`buildable` under check_assembly --baseline, board_score's assembly stayed 0,
and render's gate line did not move. A probe cannot reach that pad.

`grade_body_overlap`'s `testpoint_covered` now measures every test point's pad
copper against each other same-face drawn body with the #1106 measurement
(`bodyless_pad_shape`, `pads_under_body_frac` at `CONTAINMENT_FRAC`).
check_assembly gates a finding only when a member moved relative to
--baseline, because orangecrab ships TP26/TP27 under J4 by design.

Pinned here:
  * the TP6 repro: NOT BUILDABLE with --baseline (exit 4), report-only
    without it, accepted by an intent `overlap_waivers` pair;
  * board_score --baseline counts it (assembly 0 -> 1) and render --before
    --gate names `b_testpoint_covered(moved)`;
  * the calibration: over the tracked boards the only covered test points
    are orangecrab TP26/TP27 under J4, and orangecrab stays buildable;
  * the rules on a synthetic board: a silk-drawn cover never gates, a moved
    COVER gates as a moved test point does, and a fiducial is not graded.

Run: python3 -X utf8 tests/test_1250_testpoint_covered.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ('py_placer', 'py_router', 'py_tools', 'tests'):
    sys.path.insert(0, os.path.join(ROOT, _p))

from kicad_parser import parse_kicad_pcb                       # noqa: E402
from placement.legality import grade_body_overlap              # noqa: E402
from run_utils import check, corpus_boards, evidence           # noqa: E402

PY = sys.executable
CA = os.path.join(ROOT, 'py_tools', 'check_assembly.py')
BS = os.path.join(ROOT, 'py_tools', 'board_score.py')
RP = os.path.join(ROOT, 'py_tools', 'render_placement.py')
POSE = os.path.join(ROOT, 'py_placer', 'place_pose.py')
GLASGOW = os.path.join(ROOT, 'kicad_files', 'glasgow_revC.kicad_pcb')
ORANGECRAB = os.path.join(ROOT, 'kicad_files', 'orangecrab_ext_pll.kicad_pcb')


def _assembly(board, *extra, **kw):
    """check_assembly's run and JSON. The JSON goes to a scratch dir, never
    beside the board (a tracked board must not gain a file)."""
    td = tempfile.mkdtemp(prefix='t1250j_')
    try:
        jp = os.path.join(td, 'a.json')
        r = check([PY, '-X', 'utf8', CA, board, '--json', jp] + list(extra),
                  **kw)
        with open(evidence(jp, 'check_assembly JSON'),
                  encoding='utf-8') as fh:
            return r, json.load(fh)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _intent(td, pair):
    p = os.path.join(td, 'waive.json')
    with open(p, 'w', encoding='utf-8') as fh:
        json.dump({'schema': 1, 'kind': 'floorplan-intent', 'units': 'mm',
                   'overlap_waivers': [{'pair': list(pair),
                                        'reason': 'probed before fitting'}]},
                  fh)
    return p


class TheRepro(unittest.TestCase):
    """glasgow TP6 set to (112.7, 91.4), inside U19's TSSOP-16 body."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp(prefix='t1250_')
        cls.board = os.path.join(cls.td, 'tp6.kicad_pcb')
        check([PY, '-X', 'utf8', POSE, GLASGOW, cls.board,
               'set', 'TP6', '112.7', '91.4'], accept=True)
        evidence(cls.board, 'the TP6 board place_pose wrote')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.td, ignore_errors=True)

    def test_it_gates_against_the_baseline(self):
        r, doc = _assembly(self.board, '--baseline', GLASGOW,
                           refuse='TEST POINT COVERED', code=4)
        self.assertFalse(doc['buildable'], doc['verdict'])
        self.assertEqual(doc['testpoint_covered_gating'], 1)
        (t,) = doc['testpoint_covered_gating_pairs']
        self.assertEqual((t['testpoint'], t['cover'], t['frac'],
                          t['body_source'], t['waiver']),
                         ('TP6', 'U19', 1.0, 'fab', ''))
        self.assertIn('TP6 under U19', r.stdout)
        self.assertIn('GATES (moved: TP6)', r.stdout)
        # Every OTHER conjunct is as it was: the verdict rests on this one.
        self.assertEqual(doc['blocking'], 0)
        self.assertEqual(doc['containment_blocking'], 0)
        self.assertEqual(doc['courtyard_blocking_gating'], 0)

    def test_without_a_baseline_it_is_reported_not_gated(self):
        r, doc = _assembly(self.board, accept=True)
        self.assertTrue(doc['buildable'], doc['verdict'])
        self.assertEqual(doc['testpoint_covered'], 1)
        self.assertIsNone(doc['testpoint_covered_gating'])
        self.assertIn('REPORT-ONLY', r.stdout)

    def test_an_authored_waiver_accepts_it(self):
        r, doc = _assembly(self.board, '--baseline', GLASGOW, '--intent',
                           _intent(self.td, ('TP6', 'U19')), accept=True)
        self.assertTrue(doc['buildable'], doc['verdict'])
        (t,) = doc['testpoint_covered_pairs']
        self.assertEqual(t['waiver'], 'intent_declared')
        self.assertFalse(t['gates_if_moved'])
        self.assertEqual(doc['testpoint_covered_gating'], 0)
        self.assertNotIn('matched no waivable overlap', r.stderr)

    def test_board_score_counts_it(self):
        jp = os.path.join(self.td, 'score.json')
        check([PY, '-X', 'utf8', BS, self.board, '--baseline', GLASGOW,
               '--json', jp], refuse='BLOCKING=', code=4)
        with open(evidence(jp, 'board_score JSON'), encoding='utf-8') as fh:
            a = json.load(fh)['components']['assembly']
        self.assertEqual(a['count'], 1, a.get('count_basis'))
        self.assertFalse(a['buildable'])
        self.assertEqual(a['conjuncts_fired'], ['testpoint_covered_gating'])
        self.assertEqual(a['live_conjuncts_fired'],
                         ['testpoint_covered_gating'])

    def test_render_gates_it_with_before_and_lists_it_without(self):
        js = os.path.join(self.td, 'r.json')
        r = check([PY, '-X', 'utf8', RP, self.board, '-o',
                   os.path.join(self.td, 'r.png'), '--json-out', js,
                   '--gate', '--quiet', '--before', GLASGOW],
                  refuse='b_testpoint_covered(moved)=1', code=4)
        with open(evidence(js, 'render JSON'), encoding='utf-8') as fh:
            rows = json.load(fh)['checklist']['b_testpoint_covered']
        self.assertEqual(rows, [['TP6', 'U19', 1.0, 'fab', '', True]])
        # Without --before nothing says what moved, so the key is listed
        # and the gate does not charge it (glasgow fails --gate on its own
        # courtyard overhangs either way).
        r = check([PY, '-X', 'utf8', RP, self.board, '-o',
                   os.path.join(self.td, 'r2.png'), '--json-out', js,
                   '--gate', '--quiet'], refuse='GATE: FAIL', code=4)
        self.assertNotIn('b_testpoint_covered', r.stderr)


class TheDesignersBoards(unittest.TestCase):

    def test_orangecrab_ships_two_and_stays_buildable(self):
        r, doc = _assembly(ORANGECRAB, '--baseline', ORANGECRAB, accept=True)
        self.assertTrue(doc['buildable'], doc['verdict'])
        self.assertEqual(
            sorted((t['testpoint'], t['cover'], t['frac'])
                   for t in doc['testpoint_covered_pairs']),
            [('TP26', 'J4', 1.0), ('TP27', 'J4', 1.0)])
        self.assertEqual(doc['testpoint_covered_gating'], 0)
        self.assertIn("baseline's own (no member moved)", r.stdout)

    def test_the_tracked_boards_carry_only_orangecrabs_two(self):
        boards = corpus_boards()
        if not boards:
            self.skipTest('git cannot list the tracked boards')
        found = {}
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for b in boards:
                g = grade_body_overlap(parse_kicad_pcb(b), 0.2, pcb_file=b)
                for t in g['testpoint_covered']:
                    found[(os.path.basename(b), t.testpoint)] = t.cover
        self.assertEqual(found, {
            ('orangecrab_ext_pll.kicad_pcb', 'TP26'): 'J4',
            ('orangecrab_ext_pll.kicad_pcb', 'TP27'): 'J4'})


def _fp(ref, name, x, y, pads, layer_body=None, body=None):
    """A footprint at (x, y) on F.Cu. `pads`: [(num, lx, ly, w, h)].
    `layer_body` 'F.Fab' or 'F.SilkS' draws `body` (x0, y0, x1, y1)."""
    s = (f'  (footprint "{name}" (layer "F.Cu") (at {x} {y} 0)\n'
         f'    (property "Reference" "{ref}" (at 0 0) (layer "F.SilkS"))\n')
    if layer_body:
        x0, y0, x1, y1 = body
        s += (f'    (fp_rect (start {x0} {y0}) (end {x1} {y1}) (stroke (width'
              f' 0.1) (type default)) (layer "{layer_body}"))\n')
    for num, lx, ly, w, h in pads:
        s += (f'    (pad "{num}" smd rect (at {lx} {ly}) (size {w} {h})'
              f' (layers "F.Cu" "F.Paste" "F.Mask"))\n')
    return s + '  )\n'


def _board(td, name, parts):
    path = os.path.join(td, name + '.kicad_pcb')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('(kicad_pcb (version 20240108) (generator pcbnew)\n'
                 '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal)'
                 ' (44 "Edge.Cuts" user))\n'
                 '  (net 0 "")\n'
                 '  (gr_rect (start 0 0) (end 40 40) (stroke (width 0.1)'
                 ' (type default)) (layer "Edge.Cuts"))\n'
                 + ''.join(parts) + ')\n')
    return path


IC_PADS = [('1', -3, 0, 1.0, 0.6), ('2', 3, 0, 1.0, 0.6)]
IC_BODY = (-2, -3, 2, 3)
TP_PAD = [('1', 0, 0, 1.0, 1.0)]
TP_NAME = 'TestPoint:TestPoint_Pad_D1.0mm'


class TheRules(unittest.TestCase):
    """U1 at (20, 20) with a 4 x 6 body; TP1 at (20, 21.5), inside it."""

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix='t1250s_')

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _pair(self, layer, ic_at=(20, 20), tp_at=(20, 21.5), tp_ref='TP1',
              tp_name=TP_NAME, name='b'):
        return _board(self.td, name, [
            _fp('U1', 'Package:IC', ic_at[0], ic_at[1], IC_PADS, layer,
                IC_BODY),
            _fp(tp_ref, tp_name, tp_at[0], tp_at[1], TP_PAD)])

    def _findings(self, path):
        return grade_body_overlap(parse_kicad_pcb(path), 0.2,
                                  pcb_file=path)['testpoint_covered']

    def test_a_fab_body_is_gate_eligible_a_silk_one_never(self):
        (fab,) = self._findings(self._pair('F.Fab', name='fab'))
        self.assertEqual((fab.testpoint, fab.cover, fab.body_source,
                          fab.gates_if_moved), ('TP1', 'U1', 'fab', True))
        (silk,) = self._findings(self._pair('F.SilkS', name='silk'))
        self.assertEqual((silk.body_source, silk.gates_if_moved),
                         ('silk', False))
        base = self._pair('F.SilkS', tp_at=(5, 5), name='silk_base')
        _r, doc = _assembly(os.path.join(self.td, 'silk.kicad_pcb'),
                            '--baseline', base, accept=True)
        self.assertEqual(doc['testpoint_covered'], 1)
        self.assertEqual(doc['testpoint_covered_gating'], 0)

    def test_a_moved_cover_gates_like_a_moved_test_point(self):
        moved = self._pair('F.Fab', name='moved')
        base = self._pair('F.Fab', ic_at=(30, 30), name='ic_base')
        r, doc = _assembly(moved, '--baseline', base,
                           refuse='GATES (moved: U1)', code=4)
        self.assertEqual(doc['testpoint_covered_gating'], 1)
        # ...and an untouched pair is the baseline's own.
        _r, doc = _assembly(moved, '--baseline', moved, accept=True)
        self.assertEqual(doc['testpoint_covered_gating'], 0)

    def test_a_fiducial_is_not_graded(self):
        path = self._pair('F.Fab', tp_ref='FID1',
                          tp_name='Fiducial:Fiducial_1mm_Mask2mm',
                          name='fid')
        g = grade_body_overlap(parse_kicad_pcb(path), 0.2, pcb_file=path)
        self.assertEqual(g['testpoint_covered'], [])
        # ...at the SAME geometry: the body-less fiducial's pad is under U1
        # (#1106's channel says so, waived marker_class).
        self.assertEqual([(p.a, p.b, p.waiver) for p in g['pairs']
                          if p.kind == 'pads_under_body'],
                         [('FID1', 'U1', 'marker_class')])


if __name__ == '__main__':
    unittest.main()
