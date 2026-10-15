#!/usr/bin/env python3
"""#1191: the seed and rotation rankers break a failures tie on the probe's
vias, then its copper; and compare_seeds can rank a pile whose intent arms
the decap rule.

1. `converge.probe_route` returns `vias`, and neither ranker used it:
   `rank_rotations.rotation_key` went failures -> crossings -> hpwl, and
   `compare_seeds` failures -> iterations -> seed, printing no vias. On
   sonde_xilinx six probed arms all routed and 180 won on crossings (17 vs
   31) while the probe's own vias were 180: 0/3/4 (median 3), 270: 1/0/2
   (median 1). Once blocking ties, CLAUDE.md breaks the tie on vias then
   copper. Now both keys do, from the probe: `vias`, and `copper_mm`
   (board_score's own `quality()` read off the routed probe board, since
   route.py's summary carries no length). Both are printed and stored.
   With no probe, crossings/hpwl still decide.
2. With `--decap-claim-after-ics` every StickHub seed failed its gate on
   `decap_distance` / `decap_pin_distance` alone, so nothing was rankable
   (exit 4, "Fix the intent or the pile first"), while
   `place_seed --repair --repair-decaps` cleared seed 4 in 23 s. Now a seed
   whose only gate failure is decap rules goes through a SEED-THEN-REPAIR
   arm: the repaired board is ranked when the repair passes the same gate.
   place_seed publishes `grade_error_rules` so that can be decided, and
   rank_rotations' winner line, which said `repair it (place_seed
   --repair)` -- a repair that leaves a decap violator where it is -- now
   names `--repair-decaps` when the winner's seeds gate on decap rules only.

Pinned here: both keys and the rotation aggregate (no routing); probe_full's
copper (route stubbed); the decap predicate; the repair argv; and real runs
on the #1105 pile with esp_prog's decap limit: `compare_seeds`, which before
this exited 4 with nothing rankable, and rank_rotations' advice.

Run: python3 -X utf8 tests/test_1191_probe_vias_and_decap_repair.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ('py_router', 'py_placer', 'py_tools', 'tests'):
    sys.path.insert(0, os.path.join(ROOT, _p))

import compare_seeds as cs                                     # noqa: E402
import converge                                                # noqa: E402
import rank_rotations as rr                                    # noqa: E402
from run_utils import check, evidence                          # noqa: E402

PILE = os.path.join(HERE, 'fixtures', '959', 'run29_pile.kicad_pcb')
ESP = os.path.join(ROOT, 'kicad_files', 'esp_prog.kicad_pcb')
CS = os.path.join(ROOT, 'py_placer', 'compare_seeds.py')


def _probe(failures, vias, copper=None, iterations=100):
    return {'failures': failures, 'status': 'ok', 'vias': vias,
            'copper_mm': copper, 'iterations': iterations,
            'probe_kind': 'full'}


def _seed_row(seed, rot, crossings, probe):
    return {'seed': seed, 'rotation': rot, 'crossings': crossings,
            'hpwl': 100.0, 'grade_errors': 0, 'unseated': 0,
            'probe': probe, 'hard_fail': None}


class RotationsBreakATieOnVias(unittest.TestCase):

    def _sonde(self):
        """The issue's sonde_xilinx arms: every probe routes."""
        r180 = [_seed_row(s, 180, x, _probe(0, v, c)) for s, x, v, c in
                ((0, 17, 0, 90.0), (1, 15, 3, 95.0), (2, 19, 4, 99.0))]
        r270 = [_seed_row(s, 270, x, _probe(0, v, c)) for s, x, v, c in
                ((0, 31, 1, 97.0), (1, 30, 0, 96.0), (2, 33, 2, 98.0))]
        return rr.aggregate(180, r180, 0), rr.aggregate(270, r270, 1), r270

    def test_aggregate_takes_the_probes_vias_and_copper_medians(self):
        a180, a270, _rows = self._sonde()
        self.assertEqual((a180['probe_vias'], a180['probe_copper_mm']),
                         (3, 95.0))
        self.assertEqual((a270['probe_vias'], a270['probe_copper_mm']),
                         (1, 97.0))

    def test_fewer_vias_win_a_failures_tie_whatever_the_crossings(self):
        a180, a270, rows = self._sonde()
        ranked = [a['rotation'] for a in sorted([a180, a270],
                                                key=rr.rotation_key)]
        self.assertEqual(ranked, [270, 180], 'crossings decided the tie')
        # vias tie -> copper decides; failures still come first
        a, b = dict(a180, probe_vias=1, probe_copper_mm=99.0), \
            dict(a270, probe_vias=1, probe_copper_mm=90.0)
        self.assertEqual([x['rotation'] for x in
                          sorted([a, b], key=rr.rotation_key)], [270, 180])
        a, b = dict(a180, probe_failures=0, probe_vias=9), \
            dict(a270, probe_failures=1, probe_vias=0)
        self.assertEqual([x['rotation'] for x in
                          sorted([a, b], key=rr.rotation_key)], [180, 270])
        # a probed arm with no via count ranks after one that has it, and an
        # aggregate written before #1191 (no probe_vias key) still ranks
        old = {k: v for k, v in a180.items()
               if k not in ('probe_vias', 'probe_copper_mm')}
        self.assertEqual([x['rotation'] for x in
                          sorted([old, a270], key=rr.rotation_key)],
                         [270, 180])
        # the winner's seed: fewest vias among equal failures
        self.assertEqual(rr._best_seed_row(rows)['seed'], 1)

    def test_unprobed_arms_still_rank_on_crossings(self):
        a180, a270, _rows = self._sonde()
        for a in (a180, a270):
            a.update(probed=False, probe_failures=None, probe_vias=None,
                     probe_copper_mm=None)
        self.assertEqual([a['rotation'] for a in
                          sorted([a180, a270], key=rr.rotation_key)],
                         [180, 270])


class SeedsBreakATieOnVias(unittest.TestCase):

    def test_seed_key(self):
        rows = [{'seed': 0, 'probe': _probe(0, 12, 300.0, iterations=10)},
                {'seed': 4, 'probe': _probe(0, 3, 310.0, iterations=900)},
                {'seed': 7, 'probe': _probe(0, 3, 250.0, iterations=999)},
                {'seed': 9, 'probe': _probe(0, None, None, iterations=1)},
                {'seed': 2, 'probe': _probe(1, 0, 10.0, iterations=1)},
                {'seed': 5, 'gated': True, 'probe': _probe(0, 0, 1.0)},
                {'seed': 6, 'probe': None}]
        self.assertEqual([r['seed'] for r in sorted(rows, key=cs.seed_key)],
                         [7, 4, 0, 9, 2, 5, 6])

    def test_probe_full_reads_copper_off_the_routed_probe_board(self):
        td = tempfile.mkdtemp(prefix='t1191p_')
        real = converge.probe_route
        try:
            src = os.path.join(td, 'routed.kicad_pcb')
            with open(src, 'w', encoding='utf-8') as fh:
                fh.write('(kicad_pcb (version 20240108) (generator "t")\n'
                         '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal))\n'
                         '  (net 0 "") (net 1 "A")\n'
                         '  (segment (start 0 0) (end 3 4) (width 0.2) '
                         '(layer "F.Cu") (net 1) (uuid "s1"))\n'
                         '  (segment (start 3 4) (end 3 10) (width 0.2) '
                         '(layer "F.Cu") (net 1) (uuid "s2"))\n)\n')
            seen = {}

            def fake(board, nets, extra_args=(), out=None, note_prefix=''):
                seen['out'] = out
                shutil.copy(src, out)
                return {'failures': 0, 'status': 'ok', 'note': 'clean',
                        'iterations': 5, 'vias': 2, 'nets': len(nets),
                        'returncode': 0}
            converge.probe_route = fake
            pr = cs.probe_full(src, ['*'], [])
            self.assertEqual(pr['copper_mm'], 11.0, pr)    # 5 + 6 mm
            self.assertEqual((pr['vias'], pr['probe_kind']), (2, 'full'))
            self.assertNotEqual(seen['out'], src)

            def no_verdict(board, nets, extra_args=(), out=None,
                           note_prefix=''):
                return {'failures': None, 'status': 'crashed', 'note': 'rc=1',
                        'iterations': None, 'vias': None, 'nets': len(nets),
                        'returncode': 1}
            converge.probe_route = no_verdict
            self.assertIsNone(cs.probe_full(src, ['*'], [])['copper_mm'])
        finally:
            converge.probe_route = real
            shutil.rmtree(td, ignore_errors=True)


class DecapOnlySeedsAreRepairedAndRanked(unittest.TestCase):

    def test_the_predicate(self):
        base = {'grade_error_rules': {'decap_distance': 2,
                                      'decap_pin_distance': 1},
                'unseated': 0, 'pad_conflicts_seeded': 0,
                'hole_conflicts_added': 0, 'keepout_copper_seeded': [],
                'pad_copper_off_board_seeded': [],
                'pad_copper_off_board_locked': []}
        self.assertTrue(cs.decap_only(base))
        for k, v in (('grade_error_rules', {'decap_distance': 1,
                                            'zone_containment': 1}),
                     ('grade_error_rules', {}),
                     ('unseated', 1), ('pad_conflicts_seeded', 1),
                     ('hole_conflicts_added', 1),
                     ('keepout_copper_seeded', [['U1', 0.1]]),
                     ('pad_copper_off_board_seeded', [['J1', 0.4]]),
                     ('pad_copper_off_board_locked', [['J2', 0.4]]),
                     ('fixed_refused', ['J1'])):
            self.assertFalse(cs.decap_only(dict(base, **{k: v})), (k, v))

    def test_the_repair_carries_the_seed_args_but_not_force(self):
        seen = {}
        real = subprocess.run

        def fake(argv, **kw):
            seen['argv'] = argv
            return subprocess.CompletedProcess(
                argv, 0, 'JSON_SUMMARY: {"grade_errors": 0}\n', '')
        cs.subprocess.run = fake
        try:
            _r, s = cs.run_decap_repair(
                'seed.kicad_pcb', 'i.json', 'fixed.kicad_pcb',
                ignore_nets=['GND'],
                seed_args=['--force', '--decap-claim-after-ics',
                           '--clearance', '0.15'])
        finally:
            cs.subprocess.run = real
        argv = seen['argv']
        self.assertTrue(argv[3].endswith('place_seed.py'), argv)
        self.assertEqual(argv[4:10], ['seed.kicad_pcb', 'fixed.kicad_pcb',
                                     '--intent', 'i.json', '--repair',
                                     '--repair-decaps'])
        self.assertNotIn('--force', argv)
        self.assertIn('--decap-claim-after-ics', argv)
        self.assertEqual(argv[argv.index('--clearance') + 1], '0.15')
        self.assertEqual(s, {'grade_errors': 0})

    def _decap_intent(self, td):
        from kicad_parser import parse_kicad_pcb
        from placement import floorplan as fp
        evidence(PILE, 'the #1105 pile')
        intent = os.path.join(td, 'intent.json')
        with open(intent, 'w', encoding='utf-8') as fh:
            json.dump(fp.emit_intent(parse_kicad_pcb(PILE), PILE,
                                     decaps_from=ESP), fh)
        return intent

    def test_rank_rotations_points_a_decap_gate_at_repair_decaps(self):
        """Its winner line said `repair it (place_seed --repair)`, which
        reports a decap violator unresolved and does not move it."""
        td = tempfile.mkdtemp(prefix='t1191r_')
        try:
            intent = self._decap_intent(td)
            out = os.path.join(td, 'rot')
            r = check([sys.executable, '-X', 'utf8',
                       os.path.join(ROOT, 'py_placer', 'rank_rotations.py'),
                       PILE, '--intent', intent, '--out-dir', out,
                       '--seeds', '0', '2', '--rotations', '0', '180',
                       '--ignore-nets', 'GND'], accept=True)
            with open(os.path.join(out, 'rotations.json'),
                      encoding='utf-8') as fh:
                doc = json.load(fh)
        finally:
            shutil.rmtree(td, ignore_errors=True)
        gated = [row for row in doc['rows']
                 if row['rotation'] == doc['best_rotation'] and row['gated']]
        self.assertTrue(gated, 'the winner has no gated seed to advise on')
        for row in doc['rows']:
            if row['decap_only']:
                self.assertTrue(row['gated'])
                self.assertTrue(set(row['grade_error_rules'])
                                <= {'decap_distance', 'decap_pin_distance'})
        decap = all(row['decap_only'] for row in gated)
        self.assertTrue(decap, 'seeds 0 and 2 gate on decap_distance alone')
        self.assertEqual('(place_seed --repair --repair-decaps)' in r.stdout,
                         decap, r.stdout[-600:])

    def test_a_decap_armed_pile_is_rankable(self):
        """The real chain on the #1105 pile with esp_prog's decap limit:
        seeds 1-3 each fail their gate on decap_distance alone (measured
        2026-10-14; the copy before this exited 4, nothing rankable)."""
        td = tempfile.mkdtemp(prefix='t1191c_')
        try:
            intent = self._decap_intent(td)
            out = os.path.join(td, 'cmp')
            r = check([sys.executable, '-X', 'utf8', CS, PILE, '--intent',
                       intent, '--seeds', '1', '2', '3', '--out-dir', out,
                       '--ignore-nets', 'GND',
                       '--seed-args=--decap-claim-after-ics'], accept=True)
            doc_path = os.path.join(out, 'seeds.json')
            evidence(doc_path, 'seeds.json')
            with open(doc_path, encoding='utf-8') as fh:
                doc = json.load(fh)
        finally:
            shutil.rmtree(td, ignore_errors=True)
        rows = {row['seed']: row for row in doc['rows']}
        tried = [row for row in rows.values() if row.get('repair')]
        self.assertEqual(len(tried), 3, 'every seed was decap-only gated')
        for row in tried:
            self.assertEqual(set(row['grade_error_rules']), {'decap_distance'})
            self.assertEqual(bool(row.get('repaired')), row['repair']['rc'] == 0)
        fixed = [row for row in tried if row.get('repaired')]
        self.assertTrue(fixed, 'no repair cleared its seed')
        for row in fixed:
            self.assertTrue(row['board'].endswith('_repaired.kicad_pcb'))
            self.assertFalse(row['gated'])
            self.assertEqual(row['repair']['grade_errors'], 0)
            self.assertEqual(row['probe']['status'], 'ok')
            self.assertIsInstance(row['probe']['copper_mm'], float)
        best = rows[doc['best_seed']]
        self.assertTrue(best.get('repaired'), best)
        self.assertIn(' rep ', r.stdout)
        self.assertIn('vias  copper_mm', r.stdout)


if __name__ == '__main__':
    unittest.main()
