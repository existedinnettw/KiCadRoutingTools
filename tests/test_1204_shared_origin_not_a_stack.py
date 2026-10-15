#!/usr/bin/env python3
"""#1204: a shared origin is a stack only when something physical is shared.

check_assembly's COINCIDENT ORIGINS channel grouped origins rounded to 1 um
and gated any group holding two suspect non-markers, with no geometry test
and no waiver. A footprint origin is a library's datum, so parts share one by
design: CM5 IO's Module301 (GPIO) and Module302 (HSS) halves sit at one
origin with 34 mm between their pads, and on the tracked
rp2350_fpga_eensy_prePlane a Tag-Connect (J2, B.Cu, NPTH alignment holes)
sits over a SOT-666 (U3, F.Cu). Both designers' boards read NOT BUILDABLE,
an `overlap_waivers` entry for the pair waived its courtyard pair and not the
stack, and board_score folded the same conjunct into `assembly`.

Now a pair at one origin stacks only when, on a face both occupy, their
copper pad boxes overlap (a plated barrel is on both faces), an NPTH hole of
one lies on the other's copper, or the body channel found pad copper or a
drawn body shared there; and a pair the intent waives never stacks. The
seeder's #1151 disposition asks the same question on the same grade.

Pinned here:
  * the designer's rp2350 grades buildable; J2/U3 are disclosed `apart`;
  * module halves (the CM5 shape, synthetic) are apart -- until both draw the
    module outline on Fab, which the body channel reports as shared;
  * run 19's shape (C1 inside C3's pads, no pad intersecting -- the case the
    channel was written for) still stacks; `test_assembly_coincident_stack`
    pins it through the CLI;
  * an `overlap_waivers` pair is accepted, check_assembly and board_score
    agree, and a waiver used only here is not reported unused;
  * the seeder leaves an unseated half at its input pose.

Run: python3 -X utf8 tests/test_1204_shared_origin_not_a_stack.py
"""
import contextlib
import io
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
from placement import seeder                                   # noqa: E402
from placement.legality import grade_body_overlap              # noqa: E402
from placement.placement_state import (                        # noqa: E402
    body_contact_pairs, coincident_stack_groups, coincident_touch)
from run_utils import check, evidence                          # noqa: E402
import test_1151_unseated_disposition as t1151                 # noqa: E402
import test_assembly_coincident_stack as t19                   # noqa: E402

PY = sys.executable
CA = os.path.join(ROOT, 'py_tools', 'check_assembly.py')
BS = os.path.join(ROOT, 'py_tools', 'board_score.py')
RP = os.path.join(ROOT, 'kicad_files', 'rp2350_fpga_eensy_prePlane.kicad_pcb')


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


def _half(ref, px, fab=False):
    """One half of a module footprint, on B.Cu at (20, 20): two SMD pads at
    x = `px`, the module's two NPTH mounting holes (one AT the origin), and
    the module's courtyard -- the CM5 IO library's shape, scaled down."""
    s = (f'  (footprint "t:{ref}" (layer "B.Cu") (at 20 20 0)\n'
         f'    (property "Reference" "{ref}" (at 0 0) (layer "B.SilkS"))\n'
         '    (fp_rect (start -8 -2) (end 8 12) (stroke (width 0.05)'
         ' (type default)) (layer "B.CrtYd"))\n')
    if fab:
        s += ('    (fp_rect (start -7.5 -1.5) (end 7.5 11.5) (stroke (width'
              ' 0.1) (type default)) (layer "B.Fab"))\n')
    for num, y, net in (('1', 3, 1), ('2', 5, 2)):
        s += (f'    (pad "{num}" smd rect (at {px} {y}) (size 1 1)'
              f' (layers "B.Cu") (net {net} "N{net}"))\n')
    for y in (0, 10):
        s += (f'    (pad "" np_thru_hole circle (at 0 {y}) (size 2.7 2.7)'
              f' (drill 2.7) (layers "*.Cu" "*.Mask"))\n')
    return s + '  )\n'


def _assembly(board, *extra, **kw):
    """check_assembly's JSON and the run. The JSON goes to a scratch dir,
    never beside the board (a tracked board must not gain a file)."""
    td = tempfile.mkdtemp(prefix='t1204j_')
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
                                        'reason': 'shared origin'}]}, fh)
    return p


class TheDesignersBoard(unittest.TestCase):

    def test_rp2350_is_buildable_and_says_why(self):
        r, doc = _assembly(RP, accept=True)
        self.assertTrue(doc['buildable'], doc['verdict'])
        self.assertEqual(doc['coincident_origins'], 0)
        self.assertEqual(doc['coincident_origins_exempt'],
                         [{'point': [148.5, 96.0], 'refs': ['J2', 'U3'],
                           'why': 'apart'}])
        self.assertIn('SHARED ORIGINS, not stacks (1)', r.stdout)


class ModuleHalves(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix='t1204_')

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_halves_with_their_pads_apart_are_no_stack(self):
        path = t1151._two_part_board(self.td, 'apart',
                                     [_half('M1', 6), _half('M2', -6)])
        r, doc = _assembly(path, accept=True)
        self.assertEqual(doc['coincident_origins'], 0, r.stdout)
        self.assertEqual([e['why'] for e in doc['coincident_origins_exempt']],
                         ['apart'])
        # The same two parts with their pads together ARE a stack.
        path = t1151._two_part_board(self.td, 'together',
                                     [_half('M1', 6), _half('M2', 6)])
        _r, doc = _assembly(path, refuse='COINCIDENT ORIGINS', code=4)
        self.assertEqual(doc['coincident_origin_groups'],
                         [{'point': [20.0, 20.0], 'refs': ['M1', 'M2']}])

    def test_a_drawn_body_both_halves_share_is_a_stack(self):
        """Each half drawing the whole module on Fab: the body channel
        reports the overlap, and the origin stacks -- a waiver accepts it."""
        path = t1151._two_part_board(
            self.td, 'fab', [_half('M1', 6, fab=True),
                             _half('M2', -6, fab=True)])
        pcb = _quiet(parse_kicad_pcb, path)
        g = _quiet(grade_body_overlap, pcb, 0.2, pcb_file=path)
        self.assertEqual(body_contact_pairs(g),
                         {frozenset(('M1', 'M2')): {'B'}})
        self.assertEqual(coincident_stack_groups(pcb, path),
                         [], 'copper alone says apart')
        self.assertEqual(
            coincident_stack_groups(pcb, path, contacts=body_contact_pairs(g)),
            [{'point': [20.0, 20.0], 'refs': ['M1', 'M2']}])
        _r, doc = _assembly(path, refuse='COINCIDENT ORIGINS', code=4)
        self.assertEqual(doc['coincident_origins'], 1)
        _r, doc = _assembly(path, '--intent', _intent(self.td, ('M1', 'M2')),
                            accept=True)
        self.assertEqual(doc['coincident_origins'], 0)
        self.assertEqual(doc['coincident_origins_exempt'][0]['why'], 'waived')

    def test_an_npth_hole_on_copper_touches(self):
        """A hole of one part on the other's copper box is physical; the two
        halves' identical mounting holes are not."""
        hole = ('  (footprint "t:K" (layer "F.Cu") (at 20 23 0)\n'
                '    (property "Reference" "K1" (at 0 0) (layer "F.SilkS"))\n'
                '    (pad "" np_thru_hole circle (at 0 0) (size 1 1)'
                ' (drill 1) (layers "*.Cu" "*.Mask")))\n')
        path = t1151._two_part_board(self.td, 'hole',
                                     [_half('M1', 0), _half('M2', -6), hole])
        pcb = _quiet(parse_kicad_pcb, path)
        touch = coincident_touch(pcb)
        self.assertTrue(touch('K1', 'M1', 'B'))      # (0, 3) on M1's pads
        self.assertFalse(touch('K1', 'M2', 'B'))
        self.assertFalse(touch('M1', 'M2', 'B'))     # hole on hole only
        self.assertFalse(touch('M1', 'M2', 'F'))     # no copper on F


class TheRun19ShapeStillStacks(unittest.TestCase):

    def test_c1_inside_c3s_pads(self):
        """No pad intersects (blocking 0); the copper boxes overlap."""
        td = tempfile.mkdtemp(prefix='t1204r19_')
        try:
            with open(t19.FIXTURE, encoding='utf-8') as fh:
                text = fh.read()
            path = t19.rewrite(text, '(at 200.66 34.29 90)',
                               '(at 144.78 31.75 90)',
                               os.path.join(td, 'stacked.kicad_pcb'))
            pcb = _quiet(parse_kicad_pcb, path)
            g = _quiet(grade_body_overlap, pcb, 0.2, pcb_file=path)
            groups = coincident_stack_groups(
                pcb, path, contacts=body_contact_pairs(g))
        finally:
            shutil.rmtree(td, ignore_errors=True)
        self.assertEqual([sorted(x['refs']) for x in groups], [['C1', 'C3']])


class TheWaiver(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix='t1204w_')
        parts, _ref, _want = t1151.GRADER_CASES['coincident']
        self.path = t1151._two_part_board(self.td, 'js', parts)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_check_assembly_and_board_score_agree(self):
        _r, doc = _assembly(self.path, refuse='COINCIDENT ORIGINS', code=4)
        self.assertEqual(doc['coincident_origins'], 1)
        intent = _intent(self.td, ('S', 'J'))       # either order
        r, doc = _assembly(self.path, '--intent', intent, accept=True)
        self.assertEqual(doc['coincident_origins_exempt'],
                         [{'point': [20.0, 20.0], 'refs': ['J', 'S'],
                           'why': 'waived', 'waived_pairs': [['J', 'S']]}])
        for arm, extra, want in (('off', [], 1),
                                 ('on', ['--intent', intent], 0)):
            jp = os.path.join(self.td, f'score_{arm}.json')
            check([PY, '-X', 'utf8', BS, self.path, '--json', jp] + extra,
                  code=4)     # unrouted: board_score's own verdict
            with open(evidence(jp, 'board_score JSON'),
                      encoding='utf-8') as fh:
                asm = json.load(fh)['components']['assembly']
            self.assertEqual(asm['conjuncts']['coincident_origins'], want,
                             arm)
            self.assertEqual(asm['buildable'], want == 0, arm)

    def test_a_waiver_used_only_here_is_not_unused(self):
        """J's courtyard is drawn round one pad, clear of S's: no courtyard
        pair, so nothing but the shared origin uses the waiver. (Without a
        drawn courtyard the channel falls back to the pad box, which S's
        sits inside.)"""
        parts = [t1151._fp('J', 20, 20, 0, [('1', -5, 0, 'rect', 1, 1, 1),
                                             ('2', 5, 0, 'rect', 1, 1, 3)],
                           crt=(-5.6, -0.6, -4.4, 0.6)),
                 t1151.GRADER_CASES['coincident'][0][1]]
        path = t1151._two_part_board(self.td, 'bare', parts)
        r, doc = _assembly(path, '--intent', _intent(self.td, ('J', 'S')),
                           accept=True)
        self.assertEqual(doc['waived'], 0, 'no courtyard pair to waive')
        self.assertEqual(doc['coincident_origins_exempt'][0]['why'], 'waived')
        self.assertNotIn('matched no waivable overlap', r.stdout + r.stderr)


class TheSeederAgrees(unittest.TestCase):
    """The #1151 disposition stages a part exactly when check_assembly
    would gate it; it now asks the physical question too."""

    def _dispose(self, path, refs, **kw):
        import pose_score
        st = _quiet(pose_score.make_state, parse_kicad_pcb(path), path,
                    clearance=0.2)
        return _quiet(seeder._dispose_unseated, st, list(refs), **kw)

    def test_an_unseated_half_stays_at_its_input_pose(self):
        td = tempfile.mkdtemp(prefix='t1204s_')
        try:
            path = t1151._two_part_board(td, 'apart',
                                         [_half('M1', 6), _half('M2', -6)])
            d = self._dispose(path, ['M2'])
            parts, _ref, _want = t1151.GRADER_CASES['coincident']
            js = t1151._two_part_board(td, 'js', parts)
            plain = self._dispose(js, ['S'])
            waived = self._dispose(js, ['S'], waivers=[('J', 'S')])
        finally:
            shutil.rmtree(td, ignore_errors=True)
        self.assertEqual(d['M2']['disposition'], 'clear_at_input', d)
        self.assertEqual(plain['S']['refused_by'], ['coincident', 'J'], plain)
        self.assertEqual(waived['S']['disposition'], 'clear_at_input', waived)


if __name__ == '__main__':
    unittest.main()
