#!/usr/bin/env python3
"""#1251: a differential pair that runs through per-leg series parts.

`placement_score`'s `pair_length` read `board_context`'s pair rows, and a row
existed only for two parts that each carry BOTH nets of a pair. A pair through
AC caps or USB series resistors has no such two parts, so ulx3s (14 pairs),
rp2350_fpga_eensy_prePlane and cap_chain scored nothing -- and said
"list_nets.find_differential_pairs found no differential pair", which was
false. orangecrab_ext_pll scored its DDR pairs and missed the longer USB pair.
A raised pair branch read the same, because its row's scope `pair` failed the
`startswith('pair ')` filter.

Underneath, `diff_xnet.find_ac_coupled_xnets` (#196) -- the router's junction
rule, reused here -- counted `fp.pads`, so a 0201 resistor drawn with two
paste-only windows beside its copper pads had four "pads" and never bridged:
rp2350's R4/R5 and orangecrab's R21/R29. It now counts
`kicad_parser.non_aperture_pads` (#1143/#1148's rule).

Pinned here:
  * the router's rule: the tracked boards resolve the issue's XNets, and a
    part with two copper pads plus paste windows bridges where three copper
    pads do not;
  * `board_context.composed_pair_rows`: a three-pair chain is walked end to
    end, a branch is disclosed rather than composed, and an end two parts
    carry whole is said rather than chosen;
  * `pair_order.composed_pair_metrics` IS `pair_metrics` when the two ends
    name the same nets, and reverses its verdict when one end's legs swap;
  * `pair_length`'s four reasons are distinct and counted, the bare-`pair`
    error row is read, and the basis names the rule;
  * the tracked boards reproduce the issue's leg table (P / N / |P-N|).

Run: python3 -X utf8 tests/test_1251_series_part_pairs.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ('py_placer', 'py_router', 'py_tools', 'tests'):
    sys.path.insert(0, os.path.join(ROOT, _p))

from kicad_parser import parse_kicad_pcb                       # noqa: E402
from diff_xnet import find_ac_coupled_xnets                    # noqa: E402
from net_queries import find_differential_pairs                # noqa: E402
from routing_config import DiffPairNet                         # noqa: E402
from run_utils import evidence, tool_env                       # noqa: E402
import board_context                                           # noqa: E402
import placement_score as ps                                   # noqa: E402

BOARDS = os.path.join(ROOT, 'kicad_files')
RP = os.path.join(BOARDS, 'rp2350_fpga_eensy_prePlane.kicad_pcb')
OC = os.path.join(BOARDS, 'orangecrab_ext_pll.kicad_pcb')
ULX = os.path.join(BOARDS, 'ulx3s.kicad_pcb')
CAP = os.path.join(BOARDS, 'cap_chain.kicad_pcb')
ESP = os.path.join(BOARDS, 'esp_prog.kicad_pcb')
QFN = os.path.join(BOARDS, 'qfn_diffpair_escape.kicad_pcb')

_PCB = {}
_TERMS = {}


def _pcb(path):
    if path not in _PCB:
        evidence(path, 'tracked board')
        with contextlib.redirect_stdout(io.StringIO()):
            _PCB[path] = parse_kicad_pcb(path)
    return _PCB[path]


def _xnet_refs(path):
    pcb = _pcb(path)
    dp = find_differential_pairs(pcb, ['*'])
    xnets, _warnings = find_ac_coupled_xnets(pcb, dp)
    return sorted(x.bridge_refs for x in xnets)


def _pair_length(path):
    """`pair_length` as the CLI publishes it."""
    if path not in _TERMS:
        evidence(path, 'tracked board')
        with tempfile.TemporaryDirectory(prefix='t1251_') as tmp:
            out = os.path.join(tmp, 'ps.json')
            r = subprocess.run(
                [sys.executable, '-X', 'utf8',
                 os.path.join(ROOT, 'py_placer', 'placement_score.py'), path,
                 '--json', out], capture_output=True, text=True,
                encoding='utf-8', errors='replace', cwd=ROOT, env=tool_env(),
                timeout=900)
            if r.returncode != 0:
                raise AssertionError(f'placement_score exit {r.returncode} on '
                                     f'{path}: {r.stderr[-800:]}')
            with open(evidence(out, 'placement_score output'),
                      encoding='utf-8') as fh:
                _TERMS[path] = json.load(fh)['terms']['pair_length']
    return _TERMS[path]


def _state(path):
    """The QuenchState `board_context.pin_order_rows` builds."""
    from list_nets import board_floor_knobs
    from render_placement import PlacementModel
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        clr = board_floor_knobs(path, clearance=None)[0]
        return PlacementModel(_pcb(path), path,
                              quench_kwargs={'clearance': clr}).state


# ------------------------------------------------------------- the router

class TheRoutersJunctionRule(unittest.TestCase):

    def test_paste_only_windows_no_longer_hide_a_series_resistor(self):
        self.assertEqual(_xnet_refs(RP), [['R4', 'R5']])
        self.assertEqual(_xnet_refs(OC), [['R21', 'R29']])

    def test_the_boards_that_already_resolved_are_unchanged(self):
        self.assertEqual(_xnet_refs(ULX), [
            ['C38', 'C39'], ['C40', 'C41'], ['C42', 'C43'], ['C44', 'C45'],
            ['R49', 'R50'], ['R52', 'R53']])
        self.assertEqual(_xnet_refs(CAP), [['C1', 'C2']])

    def test_the_count_is_copper_pads_not_apertures(self):
        def pad(net, layers=('F.Cu', 'F.Mask')):
            return SimpleNamespace(net_id=net, layers=list(layers),
                                   pad_type='smd', drill=0.0)

        def part(*pads):
            return SimpleNamespace(pads=list(pads), dnp=False)

        paste = ('F.Paste',)
        pairs = {b: DiffPairNet(base_name=b, p_net_id=p, n_net_id=n,
                                p_net_name=b + '_P', n_net_name=b + '_N')
                 for b, p, n in (('A', 1, 2), ('B', 3, 4))}
        windows = part(pad(0, paste), pad(0, paste), pad(1), pad(3))
        board = SimpleNamespace(footprints={
            'R1': windows, 'R2': part(pad(0, paste), pad(0, paste),
                                      pad(2), pad(4))})
        xnets, _w = find_ac_coupled_xnets(board, pairs)
        self.assertEqual([x.bridge_refs for x in xnets], [['R1', 'R2']])
        # Three COPPER pads is not a series part, windows or not.
        board.footprints['R2'] = part(pad(0, paste), pad(2), pad(4), pad(9))
        xnets, _w = find_ac_coupled_xnets(board, pairs)
        self.assertEqual(xnets, [])


# ------------------------------------------------------------ the composer

_NETS = {'A_P': 1, 'A_N': 2, 'B_P': 3, 'B_N': 4, 'C_P': 5, 'C_N': 6,
         'D_P': 7, 'D_N': 8}
_FOUND = [('A_P', 'A_N'), ('B_P', 'B_N'), ('C_P', 'C_N')]


def _pad(net, x, y):
    return SimpleNamespace(net_id=_NETS[net], net_name=net, global_x=x,
                           global_y=y, layers=['F.Cu', 'F.Mask'],
                           pad_type='smd', drill=0.0, pinfunction='')


def _part(*pads):
    return SimpleNamespace(pads=list(pads), dnp=False,
                           footprint_name='t:part')


def _chain_board():
    """J1 -> C1/C2 -> C3/C4 -> J2 along x, the P leg at y=0 and N at y=1.
    Every leg is a straight line, so each is exactly its x extent."""
    return SimpleNamespace(footprints={
        'J1': _part(_pad('A_P', 0, 0), _pad('A_N', 0, 1)),
        'C1': _part(_pad('A_P', 10, 0), _pad('B_P', 11, 0)),
        'C2': _part(_pad('A_N', 10, 1), _pad('B_N', 11, 1)),
        'C3': _part(_pad('B_P', 20, 0), _pad('C_P', 21, 0)),
        'C4': _part(_pad('B_N', 20, 1), _pad('C_N', 21, 1)),
        'J2': _part(_pad('C_P', 30, 0), _pad('C_N', 30, 1))})


def _compose(board, found=_FOUND):
    no_state = SimpleNamespace(parts={})
    return board_context.composed_pair_rows(board, no_state, found, _NETS)


class TheComposer(unittest.TestCase):

    def test_a_chain_of_three_pairs_is_walked_end_to_end(self):
        rows, composed, _w, unchained = _compose(_chain_board())
        self.assertEqual(unchained, [])
        self.assertEqual(sorted(composed), ['A_P/A_N', 'B_P/B_N', 'C_P/C_N'])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r['a'], r['b']), ('J1', 'J2'))
        self.assertEqual(r['scope'], 'pair A_P/A_N + B_P/B_N + C_P/C_N')
        self.assertEqual(r['series'], {'P': ['C1', 'C3'], 'N': ['C2', 'C4']})
        self.assertEqual(r['legs_mm'], {'P': 30.0, 'N': 30.0})
        self.assertEqual((r['leg_skew_mm'], r['span_mm']), (0.0, 30.0))
        # No QuenchState part: the verdict is not measured, never guessed.
        self.assertTrue(r['verdict'].startswith('NOT MEASURED'), r['verdict'])
        self.assertIn('stops at J1', r['note'])

    def test_a_longer_leg_is_the_span_and_the_skew_is_a_number(self):
        board = _chain_board()
        board.footprints['C3'] = _part(_pad('B_P', 20, 4), _pad('C_P', 21, 4))
        rows, *_ = _compose(board)
        r = rows[0]
        self.assertEqual(r['legs_mm']['N'], 30.0)
        self.assertGreater(r['legs_mm']['P'], 30.0)
        self.assertEqual(r['span_mm'], r['legs_mm']['P'])
        self.assertAlmostEqual(r['leg_skew_mm'], r['legs_mm']['P'] - 30.0,
                               places=3)

    def test_a_branch_is_disclosed_not_composed(self):
        # B is bridged to A, C and D: three neighbours, so no single leg.
        board = _chain_board()
        board.footprints.update({
            'C5': _part(_pad('B_P', 15, 5), _pad('D_P', 16, 5)),
            'C6': _part(_pad('B_N', 15, 6), _pad('D_N', 16, 6))})
        rows, composed, _w, unchained = _compose(
            board, _FOUND + [('D_P', 'D_N')])
        self.assertEqual(rows, [])
        self.assertEqual(composed, set())
        self.assertEqual(len(unchained), 1)
        self.assertIn('branch', unchained[0]['why'])

    def test_an_end_two_parts_carry_whole_is_said_not_chosen(self):
        board = _chain_board()
        board.footprints['U9'] = _part(_pad('C_P', 40, 0), _pad('C_N', 40, 1))
        rows, *_ = _compose(board)
        r = rows[0]
        self.assertIsNone(r['span_mm'])
        self.assertEqual(r['b'], 'J2,U9')
        self.assertIn('carried whole by J2, U9', r['note'])
        self.assertTrue(r['verdict'].startswith('NOT MEASURED'))


# -------------------------------------------------------------- the verdict

class TheComposedVerdict(unittest.TestCase):

    def test_it_is_pair_metrics_when_both_ends_name_the_same_nets(self):
        from placement.pair_order import composed_pair_metrics, pair_metrics
        pcb, st = _pcb(ESP), _state(ESP)
        ids = [n.net_id for n in pcb.nets.values()
               if n.name in ('/D_P', '/D_N')]
        self.assertEqual(len(ids), 2)
        want = pair_metrics(st, 'U1', 'USB1', only_nets=ids)
        self.assertIsNotNone(want)
        self.assertEqual(
            composed_pair_metrics(st, 'U1', 'USB1', sorted(ids), sorted(ids)),
            want)

    def test_swapping_one_ends_legs_reverses_the_order(self):
        from placement.pair_order import composed_pair_metrics
        pcb, st = _pcb(CAP), _state(CAP)
        n = {x.name: x.net_id for x in pcb.nets.values()}
        a = (n['DPA_P'], n['DPA_N'])
        straight = composed_pair_metrics(st, 'J1', 'J2', a,
                                         (n['DPB_P'], n['DPB_N']))
        crossed = composed_pair_metrics(st, 'J1', 'J2', a,
                                        (n['DPB_N'], n['DPB_P']))
        self.assertEqual((straight['ties'], crossed['ties']), (0, 0))
        self.assertEqual(sorted((straight['inversions'],
                                 crossed['inversions'])), [0, 1])


# --------------------------------------------------------------- the term

def _row(scope, span=None, **kw):
    r = {'a': 'A', 'b': 'B', 'nets': 2, 'scope': scope, 'span_mm': span,
         'verdict': 'AGREES', 'inversions': 0}
    r.update(kw)
    return r


class ThePairLengthReasons(unittest.TestCase):

    def test_a_raised_branch_is_named_not_read_as_no_pair(self):
        # The issue's own document, verbatim.
        r = ps.pair_length({'error': None, 'rows': [
            {'a': '-', 'b': '-', 'nets': 0, 'scope': 'pair',
             'span_mm': None, 'verdict': 'NOT MEASURED (KeyError)'}]})
        self.assertIs(r['ran'], False)
        self.assertIsNone(r['value'])
        self.assertIn('raised (KeyError)', r['reason'])
        self.assertNotIn('found no differential pair', r['reason'])
        # With rows measured before it, the population is still incomplete.
        r = ps.pair_length({'error': None, 'rows': [
            _row('pair X+/X-', 3.0),
            {'a': '-', 'b': '-', 'nets': 0, 'scope': 'pair', 'span_mm': None,
             'verdict': 'NOT MEASURED (TypeError)',
             'error': 'the composed pairs: TypeError: boom'}]})
        self.assertIs(r['ran'], False)
        self.assertIn('TypeError: boom', r['reason'])
        self.assertIn('1 pair row(s) were measured before it', r['reason'])

    def test_found_but_not_carried_names_the_pairs(self):
        r = ps.pair_length({'error': None, 'rows': [], 'pairs': {
            'found': [['X+', 'X-'], ['Y+', 'Y-']],
            'not_measured': [
                {'pair': 'X+/X-', 'why': 'carried whole by U1 only'},
                {'pair': 'Y+/Y-', 'why': 'no part carries both nets'}]}})
        self.assertIs(r['ran'], False)
        self.assertIn('found 2 pair(s)', r['reason'])
        self.assertIn('X+/X- (carried whole by U1 only)', r['reason'])
        self.assertEqual(r['pairs_found'], 2)

    def test_no_pair_found_is_said_only_when_the_census_says_so(self):
        r = ps.pair_length({'error': None, 'rows': [],
                            'pairs': {'found': [], 'not_measured': []}})
        self.assertIn('found no differential pair', r['reason'])
        r = ps.pair_length({'error': None, 'rows': []})
        self.assertNotIn('found no differential pair', r['reason'])
        self.assertIn('no pair census', r['reason'])

    def test_rows_without_a_span_keep_their_own_reason(self):
        r = ps.pair_length({'error': None, 'rows': [_row('pair X+/X-')]})
        self.assertIn('none carries a span_mm', r['reason'])

    def test_the_basis_names_the_rule_so_an_old_lap_is_not_judged(self):
        r = ps.pair_length({'error': None, 'rows': [_row('pair X+/X-', 3.0)]})
        self.assertEqual(r['basis'],
                         [ps.PAIR_LENGTH_BASIS, 'A~B pair X+/X-'])
        old = dict(r, basis=None)
        verdict, detail = ps.compare_terms({'pair_length': old},
                                           {'pair_length': r})
        self.assertEqual(verdict, 'no-common-terms')
        self.assertEqual(detail[0]['why'], 'the basis moved')


# ------------------------------------------------------- the tracked boards

#: The issue's table: composed pair -> (ends, series P, series N, P, N, |P-N|).
TABLE = {
    ULX: [('GPDI1', 'U1', ['C42'], ['C43'], 10.265, 11.718, 1.453),
          ('GPDI1', 'U1', ['C40'], ['C41'], 9.923, 11.762, 1.838),
          ('U1', 'US2', ['R53'], ['R52'], 33.249, 34.872, 1.623),
          ('U6', 'US1', ['R50'], ['R49'], 48.122, 47.195, 0.927)],
    RP: [('U3', 'U6', ['R4'], ['R5'], 7.017, 6.908, 0.109)],
    OC: [('J3', 'U3', ['R21'], ['R29'], 22.022, 23.152, 1.130)],
    CAP: [('J1', 'J2', ['C1'], ['C2'], 16.523, 16.039, 0.484)],
}


class TheTrackedBoards(unittest.TestCase):

    def test_the_issues_leg_table_is_reproduced(self):
        for path, want in TABLE.items():
            t = _pair_length(path)
            got = {(p['a'], p['b'], tuple(p['series']['P']),
                    tuple(p['series']['N'])):
                   (p['legs_mm']['P'], p['legs_mm']['N'], p['leg_skew_mm'])
                   for p in t['pairs'] if p.get('composed')}
            for a, b, sp, sn, lp, ln, skew in want:
                with self.subTest(board=os.path.basename(path), series=sp):
                    self.assertEqual(got.get((a, b, tuple(sp), tuple(sn))),
                                     (lp, ln, skew), sorted(got))

    def test_every_composed_board_now_measures(self):
        want = {ULX: (48.122, 6), RP: (7.017, 1), CAP: (16.523, 1),
                OC: (23.152, 1)}
        for path, (value, composed) in want.items():
            t = _pair_length(path)
            with self.subTest(board=os.path.basename(path)):
                self.assertIs(t['ran'], True, t['reason'])
                self.assertEqual((t['value'], t['composed']),
                                 (value, composed))
        # orangecrab's DDR rows are still there, and the USB pair outranks
        # the old worst (18.289, RN6~U3 RAM_CK) it used to be missing from.
        oc = _pair_length(OC)
        self.assertIn(('RN6', 'U3', 18.289),
                      [(p['a'], p['b'], p['span_mm']) for p in oc['pairs']])
        self.assertTrue(oc['worst_pair'].startswith('J3~U3 pair '))

    def test_the_pairs_left_out_are_named_with_why(self):
        ulx = _pair_length(ULX)
        self.assertEqual(ulx['pairs_found'], 14)
        self.assertEqual(
            [(p['pair'], p['carried_whole_by']) for p in ulx['not_measured']],
            [('/serdes/SRCLKP//serdes/SRCLKN', ['U1']),
             ('USB_FPGA_PULL_D+/USB_FPGA_PULL_D-', ['U1'])])
        qfn = _pair_length(QFN)
        self.assertIs(qfn['ran'], False)
        self.assertIn('found 2 pair(s), but none is carried whole',
                      qfn['reason'])

    def test_rp2350s_row_says_where_it_stops(self):
        (row,) = [p for p in _pair_length(RP)['pairs'] if p.get('composed')]
        self.assertEqual([(s['ref'], s['copper_pads'])
                          for s in row['stops_at']], [('U3', 6), ('U6', 71)])
        self.assertIn('stops at U3', row['note'])
        self.assertIn('not followed', row['note'])

    def test_the_term_publishes_board_contexts_rows(self):
        """Called, not mirrored: the composed rows are board_context's own."""
        from list_nets import board_floor_knobs
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            theirs = board_context.pin_order_rows(
                _pcb(RP), RP, board_floor_knobs(RP, clearance=None)[0])
        want = sorted((r['a'], r['b'], r['scope'], r['span_mm'])
                      for r in theirs['rows']
                      if str(r.get('scope')).startswith('pair ')
                      and isinstance(r.get('span_mm'), (int, float)))
        got = sorted((p['a'], p['b'], p['scope'], p['span_mm'])
                     for p in _pair_length(RP)['pairs'])
        self.assertEqual(got, want)
        self.assertEqual(theirs['pairs']['composed'], [
            '/Power Supply and USB/D+//Power Supply and USB/D-',
            '/RP2354A/USB_D+//RP2354A/USB_D-'])


if __name__ == '__main__':
    unittest.main()
