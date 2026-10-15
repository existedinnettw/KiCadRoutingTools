#!/usr/bin/env python3
"""#1199: make_film --from-ledger plays the ledger along its LINEAGE.

`shots_from_ledger` walked the rows in file order and tweened every beat
from the previous row's board; a rejected row "reverted" to the previous
accepted row in FILE order, and `parent_sha` was never read. Since #1034 a
ledger holding two lineages records true parents, so the film flew the board
from one lineage's placement to the other's and back, and captioned a
routing step "moving 67 part(s)" (complex_hierarchy i11, parent i3, filmed
after i10 of the other lineage).

Now (`make_film.lineage_plan`): the shipped row is the last `--final` row,
else the last accepted one; its parents walked back are the shipped lineage.
The beats run depth-first over the parent tree, every node's side branches
before the child the shipped board descends from, so a side lineage plays as
one contiguous attempt (badged, captioned `-- side lineage`) and the film
ends on what shipped. A beat whose parent is not the previous beat is CUT to
its parent's board first (`film_steps`' 'revert' step, a silent snap), so its
tween -- and the camera's "moving N part(s)" -- is measured against the board
it was made from.

Pinned here, no rendering (the shot and step lists, and the camera's own
`synth_rounds` over the steps' boards):
  * the issue's minimal input, recorded with the real `converge.py record`:
    A0, B0, then A1 `--parent A0` films A0, B0 (attempt), cut to A0, A1 --
    and A1 moves the 1 part it moved from A0, not the 3 B0 had moved;
  * complex_hierarchy's shape (two lineages interleaved, three routing
    attempts off one placement): the s2 lineage plays as one attempt off its
    branch point, every routing beat moves 0 parts, and `--accepted-only`
    films the shipped lineage alone;
  * a ledger whose last beat is not the shipped board ends on a cut back to
    it; rows without any parent keep their order and are not attempts.

Run: python3 -X utf8 tests/test_1199_film_follows_lineage.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ('py_router', 'py_placer', 'py_tools', 'tests'):
    sys.path.insert(0, os.path.join(ROOT, _p))

import make_film                                               # noqa: E402
import movie_camera                                            # noqa: E402
from board_store import BoardStore                             # noqa: E402
from run_utils import check                                    # noqa: E402

CONVERGE = os.path.join(ROOT, 'py_placer', 'converge.py')


def _board(path, poses, copper=0):
    """A board with one 0603 per pose; `copper` segments make a routed
    board with the SAME poses but a different sha."""
    fps = []
    for i, (x, y) in enumerate(poses):
        fps.append(
            '  (footprint "R_0603" (layer "F.Cu") (uuid "00000000-0000-4000-'
            '8000-%012d")\n    (at %s %s)\n    (property "Reference" "R%d" '
            '(at 0 0) (layer "F.SilkS"))\n    (pad "1" smd rect (at -0.8 0) '
            '(size 0.8 0.9) (layers "F.Cu"))\n    (pad "2" smd rect (at 0.8 0) '
            '(size 0.8 0.9) (layers "F.Cu"))\n  )' % (i, x, y, i + 1))
    segs = ['  (segment (start %d 50) (end %d 52) (width 0.2) (layer "F.Cu") '
            '(net 0) (uuid "10000000-0000-4000-8000-%012d"))' % (k, k, k)
            for k in range(copper)]
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('(kicad_pcb (version 20240108) (generator "t1199")\n'
                 '  (general (thickness 1.6))\n'
                 '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) '
                 '(44 "Edge.Cuts" user))\n'
                 + '\n'.join(fps + segs) + '\n)\n')
    return path


ROW_A = [(10, 10), (20, 10), (30, 10), (40, 10)]
ROW_B = [(10, 30), (20, 30), (30, 30), (40, 10)]        # 3 parts off A0
ROW_A1 = [(12, 10), (20, 10), (30, 10), (40, 10)]       # 1 part off A0


def _moves(shots):
    """{step label head: parts moved} as the film's camera computes them:
    `synth_rounds` over the boards of `film_steps`, in film order."""
    steps, _owner = make_film.film_steps(shots)
    by_board = {os.path.abspath(r['board']): len(r['moved'])
                for r in movie_camera.synth_rounds([st[1] for st in steps])}
    return {st[0].split()[0]: by_board.get(os.path.abspath(st[1]), 0)
            for st in steps if len(st) < 4}


def _heads(shots):
    return [s['label'].split()[0] for s in shots]


class FilmFollowsLineage(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix='t1199_')

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _record(self, led, bd, lever, *extra):
        check([sys.executable, '-X', 'utf8', CONVERGE, 'record', '--ledger',
               led, '--board', bd, '--kind', 'placement', '--lever', lever]
              + list(extra) + ['--argv', sys.executable, '-V'], accept=True)

    def test_the_issues_three_rows(self):
        a0 = _board(os.path.join(self.td, 'A0.kicad_pcb'), ROW_A)
        b0 = _board(os.path.join(self.td, 'B0.kicad_pcb'), ROW_B)
        a1 = _board(os.path.join(self.td, 'A1.kicad_pcb'), ROW_A1)
        led = os.path.join(self.td, 'run', 'ledger.jsonl')
        self._record(led, a0, 'lineage A start')
        self._record(led, b0, 'lineage B start')
        self._record(led, a1, 'A1 made FROM A0', '--parent', a0)
        shots = make_film.shots_from_ledger(led)
        self.assertEqual(_heads(shots), ['i0', 'i1', 'i2'])
        self.assertEqual([s['accepted'] for s in shots], [True, False, True])
        self.assertIn('-- side lineage', shots[1]['label'])
        self.assertNotIn('cut_from', shots[1])     # B0 tweens from A0, its parent
        self.assertIn('cut to i0, the board i2 was made from',
                      shots[2].get('cut_label', ''))
        steps, owner = make_film.film_steps(shots)
        self.assertEqual([len(st) == 4 for st in steps],
                         [False, False, True, False])
        self.assertEqual(len(owner), len(steps))
        with open(steps[2][1], encoding='utf-8') as fh, \
                open(a0, encoding='utf-8') as g:
            self.assertEqual(fh.read(), g.read(), 'the cut is to A0')
        self.assertNotEqual(steps[2][1], shots[0]['path'],
                            'a cut gets its own copy (the camera keys by path)')
        # the camera's count: A1 moved the 1 part it moved from A0. Filmed in
        # record order, it was measured against B0 and moved 3.
        self.assertEqual(_moves(shots), {'i0': 0, 'i1': 3, 'i2': 1})
        old = [{'kind': 'board', 'path': s['path'], 'label': s['label'],
                'accepted': True} for s in shots]
        self.assertEqual(_moves(old)['i2'], 3, 'control: record order')

    def _complex(self, final=True):
        """complex_hierarchy's shape: s1 and s2 lineages off one first
        placement, recorded interleaved, three routing attempts off s1."""
        store = BoardStore(os.path.join(self.td, 'boards'))
        many = [(10 + 5 * k, 10) for k in range(8)]
        s2 = [(10 + 5 * k, 40) for k in range(8)]                   # 8 moved
        s1 = [(12 + 5 * k, 10) for k in range(4)] + many[4:]      # 4 moved
        s1b = [(13 + 5 * k, 10) for k in range(4)] + many[4:]
        boards = {
            0: (many, 0), 1: (s2, 0), 2: (s1, 0), 3: (s1b, 0),
            9: (s1b, 1), 10: (s2, 2), 11: (s1b, 3), 12: (s1b, 4), 13: (s2, 5)}
        sha = {i: store.put(_board(os.path.join(self.td, 'b%d.kicad_pcb' % i),
                                   p, c))
               for i, (p, c) in boards.items()}
        rows = [(0, 'placement', None, True), (1, 'placement', 0, True),
                (2, 'placement', 0, True), (3, 'placement', 2, True),
                (9, 'completion', 3, True), (10, 'placement', 1, True),
                (11, 'completion', 3, False), (12, 'completion', 3, True),
                (13, 'completion', 10, True)]
        led = os.path.join(self.td, 'ledger.jsonl')
        with open(led, 'w', encoding='utf-8') as fh:
            for it, kind, par, acc in rows:
                fh.write(json.dumps({
                    'iteration': it, 'kind': kind, 'accepted': acc,
                    'result_sha': sha[it],
                    'parent_sha': sha[par] if par is not None else None,
                    'lever': 'step %d' % it}) + '\n')
            if final:
                fh.write(json.dumps({
                    'iteration': 14, 'kind': 'completion', 'accepted': True,
                    'final': True, 'stop_condition': '1',
                    'result_sha': sha[12], 'parent_sha': sha[12]}) + '\n')
        return led

    def test_interleaved_lineages(self):
        led = self._complex()
        shots = make_film.shots_from_ledger(led)
        self.assertEqual(_heads(shots), ['i0', 'i1', 'i10', 'i13', 'i2', 'i3',
                                         'i9', 'i11', 'i12', 'i14'])
        kept = {s['label'].split()[0]: s['accepted'] for s in shots}
        self.assertEqual(sorted(k for k, v in kept.items() if v),
                         ['i0', 'i12', 'i14', 'i2', 'i3'])
        cut = {s['label'].split()[0]: s.get('cut_label') for s in shots
               if s.get('cut_from')}
        self.assertEqual(sorted(cut), ['i11', 'i12', 'i2'], cut)
        self.assertIn('cut to i3', cut['i11'])
        self.assertNotIn('back_to', shots[-1], 'it ends on the shipped board')
        moves = _moves(shots)
        for it in ('i9', 'i11', 'i12', 'i13', 'i14'):     # routing steps
            self.assertEqual(moves[it], 0, (it, moves))
        self.assertEqual((moves['i1'], moves['i2'], moves['i3']), (8, 4, 4))
        # the same rows in record order: the routing step i11 is measured
        # against i10, the other lineage's placement
        rec = sorted(shots, key=lambda s: s['iteration'])
        old = [{'kind': 'board', 'path': s['path'], 'label': s['label'],
                'accepted': True} for s in rec]
        self.assertEqual(_moves(old)['i11'], 8, 'control: record order')
        # --accepted-only: the shipped lineage, no cuts needed
        only = make_film.shots_from_ledger(led, include_rejected=False)
        self.assertEqual(_heads(only), ['i0', 'i2', 'i3', 'i12', 'i14'])
        self.assertFalse(any(s.get('cut_from') for s in only))

    def test_no_final_row_ships_the_last_accepted_and_ends_on_it(self):
        store = BoardStore(os.path.join(self.td, 'boards'))
        sa = store.put(_board(os.path.join(self.td, 'a.kicad_pcb'), ROW_A))
        sb = store.put(_board(os.path.join(self.td, 'b.kicad_pcb'), ROW_B))
        led = os.path.join(self.td, 'ledger.jsonl')
        with open(led, 'w', encoding='utf-8') as fh:
            for it, s, par, acc in ((0, sa, None, True), (1, sb, sa, True),
                                    (2, sa, sb, False)):
                fh.write(json.dumps({'iteration': it, 'kind': 'placement',
                                     'result_sha': s, 'parent_sha': par,
                                     'accepted': acc}) + '\n')
        shots = make_film.shots_from_ledger(led)
        self.assertEqual(_heads(shots), ['i0', 'i1', 'i2'])
        self.assertEqual([s['accepted'] for s in shots], [True, True, False])
        self.assertIn('back to i1, the shipped board',
                      shots[-1].get('back_label', ''))
        steps, _owner = make_film.film_steps(shots)
        with open(steps[-1][1], encoding='utf-8') as fh, \
                open(os.path.join(self.td, 'b.kicad_pcb'),
                     encoding='utf-8') as g:
            self.assertEqual(fh.read(), g.read(), 'the film ends on i1')

    def test_rows_without_parents_keep_their_order(self):
        store = BoardStore(os.path.join(self.td, 'boards'))
        sha = store.put(_board(os.path.join(self.td, 'a.kicad_pcb'), ROW_A))
        led = os.path.join(self.td, 'ledger.jsonl')
        with open(led, 'w', encoding='utf-8') as fh:
            for it in range(3):
                fh.write(json.dumps({'iteration': it, 'kind': 'placement',
                                     'result_sha': sha, 'accepted': True,
                                     'final': it == 2}) + '\n')
        shots = make_film.shots_from_ledger(led)
        self.assertEqual(_heads(shots), ['i0', 'i1', 'i2'])
        self.assertTrue(all(s['accepted'] for s in shots))
        self.assertFalse(any(s.get('cut_from') or s.get('back_to')
                             for s in shots))


if __name__ == '__main__':
    unittest.main()
