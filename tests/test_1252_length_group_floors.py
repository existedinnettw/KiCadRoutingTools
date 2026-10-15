#!/usr/bin/env python3
"""#1252: a matched group's length-floor spread, measured before routing.

A placement fixes the shortest copper each member of a matched group can
have, and no placement grader measured it: `placement_score`'s six terms are
none of them per group, and `--length-groups` existed only on the routed-board
graders, which on a copper-free board measure copper that does not exist yet.

`placement_score.py BOARD --length-groups FILE` (board_score's file format)
now fills a REPORT-ONLY `length_groups` section: per member the floor and its
basis (2 parts: closest pad pair; 3+ parts: farthest pad pair on different
parts; octilinear), the longest floor and its net, the spread, the copper the
group must add to match, the longest floor in ps on an outer and an inner
layer, and the tolerance beside the spread with no pass/fail.

Pinned here:
  * the issue's table on ulx3s, orangecrab_ext_pll and glasgow_revC, with the
    groups written from the board's own net names by the issue's regexes;
  * both floor bases, re-derived from the parser's pads;
  * it is not a term: TERM_ORDER, the terms and compare_terms are unchanged;
  * `ran: false` with a reason for no file, a group with fewer than 2 nets on
    the board, and a group with fewer than 2 floors; a missing or malformed
    file exits 2 for that reason; board_score reads the same file.

Run: python3 -X utf8 tests/test_1252_length_group_floors.py
"""
import contextlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ('py_placer', 'py_router', 'py_tools', 'tests'):
    sys.path.insert(0, os.path.join(ROOT, _p))

from kicad_parser import parse_kicad_pcb                       # noqa: E402
from run_utils import check, evidence, tool_env                # noqa: E402
import placement_score as ps                                   # noqa: E402

PY = sys.executable
PS = os.path.join(ROOT, 'py_placer', 'placement_score.py')
BOARDS = os.path.join(ROOT, 'kicad_files')
ULX = os.path.join(BOARDS, 'ulx3s.kicad_pcb')
OC = os.path.join(BOARDS, 'orangecrab_ext_pll.kicad_pcb')
GL = os.path.join(BOARDS, 'glasgow_revC.kicad_pcb')
ESP = os.path.join(BOARDS, 'esp_prog.kicad_pcb')

#: The issue's groups, by its regexes over each board's own net names.
GROUPS = {
    ULX: {'SDRAM_DQ': r'^SDRAM_(D\d+|DQM\d)$',
          'SDRAM_ADDR': r'^SDRAM_(A\d+|BA\d|nCAS|nRAS|nWE|nCS|CKE|CLK)$'},
    OC: {'BYTE0': r'^RAM_(D[0-7]|LDM|LDQS[+-])$',
         'BYTE1': r'^RAM_(D([89]|1[0-5])|UDM|UDQS[+-])$'},
    GL: {'Z': r'^/IO_Banks/Z\d+_[PN]$'},
}

#: The issue's table: nets, parts, floor min, max (net), spread, copper to
#: add (1 dp, as printed there), longest floor in ps outer / inner (0 dp).
TABLE = {
    'SDRAM_DQ': (18, ['U1', 'U2'], 13.689, 21.164, 'SDRAM_DQM1', 7.475,
                 78.2, 117, 150),
    'SDRAM_ADDR': (21, ['U1', 'U2'], 21.164, 31.621, 'SDRAM_A3', 10.457,
                   102.6, 175, 224),
    'BYTE0': (11, ['U3', 'U4'], 7.562, 12.522, 'RAM_LDQS-', 4.960,
              21.1, 84, 84),
    'BYTE1': (11, ['U3', 'U4'], 10.859, 15.605, 'RAM_D11', 4.746,
              28.5, 104, 104),
    'Z': (26, ['J5', 'U30'], 14.618, 25.502, '/IO_Banks/Z11_N', 10.883,
          122.5, 141, 180),
}

_PCB = {}


def _pcb(path):
    if path not in _PCB:
        evidence(path, 'tracked board')
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            _PCB[path] = parse_kicad_pcb(path)
    return _PCB[path]


def _write_groups(path, tmp, tol=0.5):
    names = sorted(n.name for n in _pcb(path).nets.values() if n.name)
    out = os.path.join(tmp, os.path.basename(path) + '.groups.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump({g: {'nets': [n for n in names if re.match(rx, n)],
                       'tolerance_mm': tol}
                   for g, rx in GROUPS[path].items()}, fh)
    return evidence(out, 'groups file')


def _run(board, *extra):
    with tempfile.TemporaryDirectory(prefix='t1252_') as tmp:
        out = os.path.join(tmp, 'ps.json')
        r = subprocess.run([PY, '-X', 'utf8', PS, board, '--json', out,
                            *extra], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', cwd=ROOT,
                           env=tool_env(), timeout=900)
        if r.returncode != 0:
            raise AssertionError(f'placement_score exit {r.returncode}: '
                                 f'{r.stderr[-800:]}')
        with open(evidence(out, 'placement_score output'),
                  encoding='utf-8') as fh:
            return json.load(fh)


class TheIssuesTable(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='t1252g_')
        cls.docs = {b: _run(b, '--length-groups', _write_groups(b, cls.tmp))
                    for b in GROUPS}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_group_reproduces_its_row(self):
        for board, groups in GROUPS.items():
            sec = self.docs[board]['length_groups']
            self.assertIs(sec['ran'], True)
            self.assertIs(sec['report_only'], True)
            for g in groups:
                d = sec['groups'][g]
                (n, parts, lo, hi, net, spread, add, ps_out,
                 ps_in) = TABLE[g]
                with self.subTest(group=g):
                    self.assertIs(d['ran'], True)
                    self.assertEqual((len(d['found']), d['missing']), (n, []))
                    self.assertEqual(sorted({p for m in d['members'].values()
                                             for p in m['parts']}), parts)
                    self.assertEqual((d['shortest_floor_mm'],
                                      d['longest_floor_mm'], d['longest_net'],
                                      d['spread_mm']), (lo, hi, net, spread))
                    self.assertEqual(round(d['copper_to_add_mm'], 1), add)
                    self.assertEqual(
                        (round(d['longest_floor_ps']['outer']['ps']),
                         round(d['longest_floor_ps']['inner']['ps'])),
                        (ps_out, ps_in))
                    # Beside the spread, never judged against it.
                    self.assertEqual(d['tolerance_mm'], 0.5)
                    self.assertFalse({'pass', 'ok', 'verdict'} & set(d))

    def test_the_velocity_is_disclosed_with_its_source(self):
        ulx = self.docs[ULX]['length_groups']['velocity']
        self.assertEqual((ulx['outer']['ps_per_mm'], ulx['inner']['ps_per_mm'],
                          ulx['stackup']), (5.532, 7.076, True))
        oc = self.docs[OC]['length_groups']['velocity']
        self.assertEqual((oc['outer']['ps_per_mm'], oc['inner']['ps_per_mm'],
                          oc['stackup']), (6.671, 6.671, False))
        self.assertIn('no stackup', oc['note'])

    def test_it_says_what_it_cannot_see(self):
        seen = ' '.join(self.docs[ULX]['length_groups']['cannot_see'])
        for blind in ('detours', 'vias', 'meander room', 'fly-by',
                      'die_length', '#1248', 'pad-edge entry'):
            self.assertIn(blind, seen)

    def test_it_is_not_a_term(self):
        doc = self.docs[ULX]
        self.assertEqual(ps.TERM_ORDER, (
            'pair_length', 'pin_order_crossings', 'cluster_to_pin',
            'plane_cut_proxy', 'balance', 'edge_facing'))
        self.assertEqual(sorted(doc['terms']), sorted(ps.TERM_ORDER))
        self.assertNotIn('length_groups', doc['terms'])
        _verdict, detail = ps.compare_terms(doc['terms'], doc['terms'])
        self.assertEqual([r['term'] for r in detail], list(ps.TERM_ORDER))

    def test_board_score_reads_the_same_file(self):
        import board_score
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            got = board_score.score_length(ULX, _write_groups(ULX, self.tmp))
        self.assertEqual(sorted(got['groups']), ['SDRAM_ADDR', 'SDRAM_DQ'])


class TheFloor(unittest.TestCase):
    """Both bases, re-derived from the parser's pads."""

    @staticmethod
    def _octi(a, b):
        dx, dy = abs(a.global_x - b.global_x), abs(a.global_y - b.global_y)
        return max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy)

    def _cross(self, pcb, name):
        net = next(n for n in pcb.nets.values() if n.name == name)
        pads = [p for p in net.pads if 'F.Cu' in p.layers or p.drill > 0
                or 'B.Cu' in p.layers]
        refs = {p.component_ref for p in pads}
        return refs, [self._octi(a, b) for a in pads for b in pads
                      if a.component_ref < b.component_ref]

    def test_two_parts_take_the_closest_pad_pair(self):
        pcb = _pcb(OC)
        refs, cross = self._cross(pcb, 'RAM_D0')
        self.assertEqual(refs, {'U3', 'U4'})
        nid = next(i for i, n in pcb.nets.items() if n.name == 'RAM_D0')
        m, why = ps.net_length_floor(pcb, nid)
        self.assertIsNone(why)
        self.assertEqual((m['basis'], m['floor_mm']),
                         ('2-part', round(min(cross), 3)))

    def test_three_parts_take_the_farthest_pad_pair(self):
        # Every orangecrab address net sits on U3, U4 and one of RN1-RN8.
        pcb = _pcb(OC)
        refs, cross = self._cross(pcb, 'RAM_A0')
        self.assertEqual(len(refs), 3)
        nid = next(i for i, n in pcb.nets.items() if n.name == 'RAM_A0')
        m, _why = ps.net_length_floor(pcb, nid)
        self.assertEqual((m['basis'], m['floor_mm']),
                         ('multipoint', round(max(cross), 3)))
        self.assertEqual(sorted(m['parts']), sorted(refs))


class WhenItCannotMeasure(unittest.TestCase):

    def test_no_file_is_a_reason(self):
        sec = ps.length_group_floors(_pcb(ESP), None)
        self.assertIs(sec['ran'], False)
        self.assertIn('no --length-groups given', sec['reason'])

    def test_a_group_with_fewer_than_two_nets_on_the_board(self):
        sec = ps.length_group_floors(_pcb(OC), {
            'BAD': {'nets': ['RAM_D0', 'NOT_A_NET']},
            'OK': {'nets': ['RAM_D0', 'RAM_D1']}})
        bad = sec['groups']['BAD']
        self.assertIs(bad['ran'], False)
        self.assertIn('fewer than 2 of its 2 net(s)', bad['reason'])
        self.assertEqual(bad['missing'], ['NOT_A_NET'])
        self.assertIs(sec['groups']['OK']['ran'], True)
        self.assertIs(sec['ran'], True)

    def test_a_net_on_one_part_has_no_floor(self):
        # EXT_PLL+/- reach U3 and a one-pad test point each; GND is many parts.
        pcb = _pcb(OC)
        lone = next(n.name for n in pcb.nets.values()
                    if n.name and len({p.component_ref
                                       for p in n.pads}) == 1)
        sec = ps.length_group_floors(pcb, {'G': {'nets': [lone, 'RAM_D0']}})
        g = sec['groups']['G']
        self.assertIs(g['ran'], False)
        self.assertIn('one part only', g['unmeasured'][lone])
        self.assertIs(sec['ran'], False)

    def test_a_two_layer_board_has_no_inner_figure(self):
        sec = ps.length_group_floors(_pcb(ESP),
                                     {'USB': {'nets': ['/D_P', '/D_N']}})
        self.assertIsNone(sec['velocity']['inner'])
        self.assertIsNone(sec['groups']['USB']['longest_floor_ps']['inner'])
        self.assertIn('no inner copper layer', sec['velocity']['note'])

    def test_a_missing_or_malformed_file_exits_2_for_that_reason(self):
        check([PY, '-X', 'utf8', PS, ESP, '--length-groups',
               os.path.join(ROOT, 'no-such-groups.json')],
              refuse='--length-groups: FileNotFoundError', code=2,
              allow=('No such file or directory',), env=tool_env())
        with tempfile.TemporaryDirectory(prefix='t1252m_') as tmp:
            bad = os.path.join(tmp, 'bad.json')
            with open(bad, 'w', encoding='utf-8') as fh:
                json.dump(['RAM_D0', 'RAM_D1'], fh)
            check([PY, '-X', 'utf8', PS, ESP, '--length-groups',
                   evidence(bad)],
                  refuse='must be a JSON object', code=2, env=tool_env())


if __name__ == '__main__':
    unittest.main()
