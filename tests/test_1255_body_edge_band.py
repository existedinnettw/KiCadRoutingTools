#!/usr/bin/env python3
"""#1255: every grader now reports a component body's distance to the edge.

Past the hard outline gate, nothing graded how close a BODY sat to the board
edge. The quench's soft halo is a cost no grader reads, and render's
`a_off_outline.courtyard` measures the courtyard against an inset outline.
check_assembly and board_score did not measure it at all. A 1.77 mm nudge put
splitflap R1's fab body 1.00 mm from the south edge, and every grader read the
board exactly as before.

`legality.body_edge_band` measures each pad-bearing part's body (drawn, else
courtyard, else pad box) against the real outline at margin 0. check_assembly
(`body_edge_band`) and render_placement (`a_body_edge_band`) report it. The
band is JLCPCB's 2.5 mm, labelled `derived_default`, and the census is
report-only, unless the intent declares `assembly.body_edge_mm`. Declared, it
gates every part in the band. The design brief declares it too, and that
compiles.

Pinned here:
  * the repro: report-only, `new_in_band 1` naming R1, exit 0;
  * a declared band (with the J-row's edges declared) gates R1 and passes the
    untouched board, in check_assembly, render --gate and board_score;
  * the calibration on the five human boards, refs and distances as the
    issue measured them;
  * the intent and brief slots: validation, compile, merge, drift, clause id,
    and check_floorplan naming check_assembly as the grader;
  * a declared band on a board with no outline fails closed.

Run: python3 -X utf8 tests/test_1255_body_edge_band.py
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
from placement import design_brief as db                       # noqa: E402
from placement import floorplan as fp                          # noqa: E402
from placement.legality import (BODY_EDGE_DEFAULT_MM,          # noqa: E402
                                body_edge_band)
from run_utils import check, evidence                          # noqa: E402

PY = sys.executable
CA = os.path.join(ROOT, 'py_tools', 'check_assembly.py')
CF = os.path.join(ROOT, 'py_tools', 'check_floorplan.py')
BS = os.path.join(ROOT, 'py_tools', 'board_score.py')
RP = os.path.join(ROOT, 'py_tools', 'render_placement.py')
POSE = os.path.join(ROOT, 'py_placer', 'place_pose.py')
SPLITFLAP = os.path.join(ROOT, 'kicad_files', 'splitflap_driver.kicad_pcb')

#: splitflap's six JST headers at the north edge, 0.41 mm off it.
J_ROW = ('J3', 'J5', 'J6', 'J7', 'J12', 'J13')


def _write(path, doc):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh)
    return evidence(path, os.path.basename(path))


def _declared_intent(td, band=1.5, name='declared.json'):
    """A band, and the J-row's edges, so R1 is the only part inside 1.5 mm."""
    return _write(os.path.join(td, name), {
        'schema': 1, 'kind': 'floorplan-intent', 'units': 'mm',
        'min_reader': 9, 'assembly': {'body_edge_mm': band},
        'edge_connectors': [{'ref': r, 'edge': 'north'} for r in J_ROW]})


def _assembly(board, *extra, **kw):
    td = tempfile.mkdtemp(prefix='t1255j_')
    try:
        jp = os.path.join(td, 'a.json')
        r = check([PY, '-X', 'utf8', CA, board, '--json', jp] + list(extra),
                  **kw)
        with open(evidence(jp, 'check_assembly JSON'),
                  encoding='utf-8') as fh:
            return r, json.load(fh)
    finally:
        shutil.rmtree(td, ignore_errors=True)


class TheRepro(unittest.TestCase):
    """splitflap R1 set to (81.915, 54.47): its body 1.00 mm from the edge."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp(prefix='t1255_')
        cls.board = os.path.join(cls.td, 'r1.kicad_pcb')
        check([PY, '-X', 'utf8', POSE, SPLITFLAP, cls.board,
               'set', 'R1', '81.915', '54.47'], accept=True)
        evidence(cls.board, 'the R1 board place_pose wrote')
        cls.intent = _declared_intent(cls.td)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.td, ignore_errors=True)

    def test_undeclared_it_is_reported_and_never_gates(self):
        r, doc = _assembly(self.board, '--baseline', SPLITFLAP, accept=True)
        band = doc['body_edge_band']
        self.assertEqual((band['threshold_mm'], band['threshold_basis'],
                          band['gates']),
                         (BODY_EDGE_DEFAULT_MM, 'derived_default', False))
        self.assertIn('JLCPCB', band['citation'])
        self.assertIn('2.5mm', band['citation'])
        r1 = [x for x in band['in_band'] if x['ref'] == 'R1']
        self.assertEqual(len(r1), 1, band['in_band'])
        self.assertEqual((round(r1[0]['mm'], 2), r1[0]['source']),
                         (1.0, 'fab'))
        self.assertEqual((band['new_in_band'], band['new_in_band_refs']),
                         (1, ['R1']))
        self.assertEqual(doc['body_edge_band_gating'], 0)
        self.assertTrue(doc['buildable'], doc['verdict'])
        self.assertIn('new_in_band 1', r.stdout)
        self.assertIn('REPORT-ONLY', r.stdout)

    def test_the_untouched_board_has_r1_outside_the_band(self):
        _r, doc = _assembly(SPLITFLAP, '--baseline', SPLITFLAP, accept=True)
        band = doc['body_edge_band']
        self.assertNotIn('R1', [x['ref'] for x in band['in_band']])
        self.assertEqual(band['new_in_band'], 0)

    def test_a_declared_band_gates(self):
        r, doc = _assembly(self.board, '--baseline', SPLITFLAP,
                           '--intent', self.intent,
                           refuse='BODY-TO-EDGE BAND', code=4)
        band = doc['body_edge_band']
        self.assertEqual((band['threshold_mm'], band['threshold_basis'],
                          band['gates'], band['citation']),
                         (1.5, 'declared', True, None))
        self.assertEqual([x['ref'] for x in band['in_band']], ['R1'])
        self.assertEqual(doc['body_edge_band_gating'], 1)
        self.assertFalse(doc['buildable'])
        # The declared edges are listed apart, never counted.
        self.assertEqual(sorted(x['ref'] for x in band['excluded']
                                if x['why'] == 'declared_edge'),
                         sorted(J_ROW))
        # ...and the untouched board passes the same declaration.
        _r, doc = _assembly(SPLITFLAP, '--intent', self.intent, accept=True)
        self.assertEqual(doc['body_edge_band_gating'], 0)

    def test_render_reports_it_and_gates_only_when_declared(self):
        js = os.path.join(self.td, 'r.json')
        r = check([PY, '-X', 'utf8', RP, self.board, '-o',
                   os.path.join(self.td, 'r.png'), '--json-out', js,
                   '--gate', '--quiet', '--before', SPLITFLAP],
                  refuse='GATE: FAIL', code=4)     # its 14 courtyard overhangs
        self.assertNotIn('a_body_edge_band', r.stderr)
        with open(evidence(js, 'render JSON'), encoding='utf-8') as fh:
            cl = json.load(fh)['checklist']['a_body_edge_band']
        self.assertEqual((cl['gates'], cl['threshold_basis'],
                          cl['new_in_band'], cl['new_in_band_refs']),
                         (False, 'derived_default', 1, ['R1']))
        self.assertIn(['R1', 0.998, 'fab'], cl['in_band'])
        check([PY, '-X', 'utf8', RP, self.board, '-o',
               os.path.join(self.td, 'r2.png'), '--json-out', js, '--gate',
               '--quiet', '--intent', self.intent],
              refuse='a_body_edge_band=1', code=4)

    def test_board_score_counts_a_declared_band(self):
        jp = os.path.join(self.td, 'score.json')
        check([PY, '-X', 'utf8', BS, self.board, '--baseline', SPLITFLAP,
               '--intent', self.intent, '--json', jp],
              refuse='BLOCKING=', code=4)
        with open(evidence(jp, 'board_score JSON'), encoding='utf-8') as fh:
            a = json.load(fh)['components']['assembly']
        self.assertEqual(a['count'], 1, a.get('count_basis'))
        self.assertEqual(a['conjuncts_fired'], ['body_edge_band_gating'])
        self.assertEqual(a['conjuncts']['body_edge_band_gating'], 1)


#: The issue's measurement, re-measured on main: refs and distances (mm, to
#: 2 decimals) of the parts in the 2.5 mm band, out of the eligible parts.
CALIBRATION = {
    'splitflap_driver': (58, {
        'J3': 0.41, 'J5': 0.41, 'J6': 0.41, 'J7': 0.41, 'J12': 0.41,
        'J13': 0.41, 'J16': 2.05, 'J17': 2.05, 'J18': 2.05, 'C3': 2.20}),
    'esp_prog': (15, {
        'CON1': 0.25, 'CON2': 0.69, 'Y1': 0.90, 'Q2': 0.95, 'C2': 1.10,
        'C4': 1.20, 'U1': 1.25}),
    'glasgow_revC': (232, {'J4': 0.04, 'J2': 1.00, 'J3': 1.00, 'R4': 2.02}),
    'watchy': (70, {
        'U3': 0.25, 'D3': 0.35, 'M1': 0.45, 'R8': 0.73, 'R9': 0.73,
        'C11': 0.78, 'R11': 0.84, 'R4': 0.86, 'J1': 1.97, 'C20': 2.41}),
    'ulx3s': (210, {
        'J1': 0.00, 'J2': 0.00, 'U9': 0.00, 'BAT1': 0.10, 'SD1': 0.32,
        'GPDI1': 0.39, 'J4': 1.27, 'U11': 1.84, 'D8': 2.28}),
}


class TheHumanBoards(unittest.TestCase):

    def test_every_one_breaks_the_default_as_the_issue_measured(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for stem, (eligible, want) in CALIBRATION.items():
                path = os.path.join(ROOT, 'kicad_files', stem + '.kicad_pcb')
                band = body_edge_band(parse_kicad_pcb(path), path)
                got = {x['ref']: round(x['mm'], 2) for x in band['in_band']}
                self.assertEqual(got, want, stem)
                self.assertEqual(band['eligible'], eligible, stem)
                self.assertFalse(band['gates'], stem)

    def test_a_declared_band_on_a_board_with_no_outline_fails_closed(self):
        td = tempfile.mkdtemp(prefix='t1255n_')
        try:
            path = os.path.join(td, 'b.kicad_pcb')
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('(kicad_pcb (version 20240108) (generator pcbnew)\n'
                         '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal)'
                         ' (44 "Edge.Cuts" user))\n  (net 0 "")\n'
                         '  (footprint "R:R" (layer "F.Cu") (at 10 10 0)\n'
                         '    (property "Reference" "R1" (at 0 0) (layer'
                         ' "F.SilkS"))\n'
                         '    (pad "1" smd rect (at 0 0) (size 1 1)'
                         ' (layers "F.Cu")))\n)\n')
            intent = _declared_intent(td, band=2.0)
            _r, doc = _assembly(path, '--intent', intent,
                                refuse='BODY-TO-EDGE BAND NOT MEASURED',
                                code=4)
            self.assertEqual(doc['body_edge_band_gating'], 1)
            # Undeclared, the same board is only told it was not measured.
            _r, doc = _assembly(path, accept=True)
            self.assertEqual(doc['body_edge_band_gating'], 0)
        finally:
            shutil.rmtree(td, ignore_errors=True)


BASE = {'schema': 1, 'kind': 'floorplan-intent', 'units': 'mm'}
BRIEF = {'schema': 1, 'kind': 'design-brief', 'units': 'mm'}


class TheDeclaration(unittest.TestCase):

    def test_the_intent_loader(self):
        it = fp.intent_from_dict(dict(BASE, assembly={'body_edge_mm': 2.5}))
        self.assertEqual((it.body_edge_mm(), it.assembly_sides()),
                         (2.5, 'both'))
        self.assertIsNone(fp.intent_from_dict(BASE).body_edge_mm())
        for bad, why in (({'body_edge_mm': 0}, 'distance > 0'),
                         ({'body_edge_mm': -1}, 'distance > 0'),
                         ({'body_edge_mm': True}, 'expected a number'),
                         ({'why': 'nothing'}, 'constrains nothing')):
            with self.assertRaises(fp.IntentError) as cm:
                fp.intent_from_dict(dict(BASE, assembly=bad))
            self.assertIn(why, str(cm.exception), bad)
        self.assertGreaterEqual(fp.READER_VERSION, 9)

    def test_the_brief_compiles_merges_and_drifts(self):
        brief = db.brief_from_dict(dict(BRIEF, assembly={
            'body_edge_mm': 2.5, 'why': 'JLC, no rails'}))
        frag, report = db.compile_brief(brief, refs_known=False)
        self.assertEqual(frag['assembly']['body_edge_mm'], 2.5)
        self.assertGreaterEqual(frag['min_reader'], 9)
        self.assertIn('assembly.body_edge_mm', report['declared'])
        self.assertEqual(db.parse_clause_id('assembly.body_edge_mm')['kind'],
                         'assembly')
        emitted = dict(BASE, assembly={'sides': 'F', 'why': 'observed'})
        merged = db.merge_into_intent(emitted, frag, report)
        it = fp.intent_from_dict(merged)
        self.assertEqual((it.body_edge_mm(), it.assembly_sides()),
                         (2.5, 'F'))
        self.assertEqual(merged['context']['basis']['assembly.body_edge_mm'],
                         'declared')
        self.assertEqual(db.drift_pairs(merged, frag), [])
        self.assertEqual([cid for cid, _ in db.drift_pairs(emitted, frag)],
                         ['assembly.body_edge_mm'])
        # A band the intent carries from the brief, which the brief has
        # since dropped, drifts too: check_assembly would keep gating on it.
        frag_0, _rep = db.compile_brief(db.brief_from_dict(BRIEF),
                                        refs_known=False)
        self.assertEqual([cid for cid, _ in db.drift_pairs(merged, frag_0)],
                         ['assembly.body_edge_mm'])
        self.assertEqual(db.drift_pairs(
            dict(BASE, assembly={'body_edge_mm': 2.5}), frag_0), [])
        # "unknown" is an answer: reported, compiled to nothing.
        frag_u, rep_u = db.compile_brief(db.brief_from_dict(dict(
            BRIEF, assembly={'body_edge_mm': 'unknown'})), refs_known=False)
        self.assertNotIn('assembly', frag_u)
        self.assertIn('assembly.body_edge_mm', rep_u['unknown'])
        for bad, why in (({'why': 'x'}, 'constrains nothing'),
                         ({'body_edge_mm': 0}, 'not a distance'),
                         ({'body_edge': 2}, 'unknown key')):
            with self.assertRaises(db.BriefError) as cm:
                db.brief_from_dict(dict(BRIEF, assembly=bad))
            self.assertIn(why, str(cm.exception), bad)

    def test_check_floorplan_names_the_grader(self):
        td = tempfile.mkdtemp(prefix='t1255f_')
        try:
            brief = _write(os.path.join(td, 'brief.json'), dict(
                BRIEF, assembly={'body_edge_mm': 1.5}))
            r = check([PY, '-X', 'utf8', CF, SPLITFLAP, '--intent',
                       _declared_intent(td), '--brief', brief], accept=True)
            self.assertIn('graded by another instrument, not by this one: '
                          'assembly.body_edge_mm (check_assembly --intent, '
                          'body_edge_band)', r.stdout)
            self.assertNotIn('NOT physically checked -- no rule measures '
                             'them, whatever `complete` says: '
                             'assembly.body_edge_mm', r.stdout)
            # An intent without the band drifts from the brief.
            noband = _write(os.path.join(td, 'noband.json'), BASE)
            r = check([PY, '-X', 'utf8', CF, SPLITFLAP, '--intent', noband,
                       '--brief', brief], accept=True)
            self.assertIn('assembly.body_edge_mm: brief says 1.5, the intent '
                          'does not declare it', r.stdout)
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
