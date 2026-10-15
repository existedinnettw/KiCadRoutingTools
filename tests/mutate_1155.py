"""The #1155 mutation battery: an edge connector is graded and seated by the
way its body leaves its pad row -- the mating face -- not by the edge it sits
nearest.

One row per load-bearing line, each reverting it; every row names the tests
that must fail. **THE ROW TO LOOK AT FIRST if this file ever goes red** is
`conjunct-off`: sonde_xilinx's J2 (DSUB-9 edge-mount, declared east) seated
backwards, its shell over the board, passed check_floorplan, board_context
and board_score because every conjunct read the courtyard's nearest edge.

NOT named `test_*.py`, so `tests/run_all.py` does not collect it: it REWRITES
the sources in place. One writer per tree. It refuses to start on a dirty
target, and it runs every witness UNMUTATED first -- a witness that already
fails would score every row as killed.

    python3 tests/mutate_1155.py
    python3 tests/mutate_1155.py --row conjunct-off

A row is KILLED by a failure or an error. An anchor that does not match
EXACTLY ONCE is BROKEN, never skipped; `preflight()` runs right after `ROWS`.
Edits are `str.replace(old, new, 1)`; anchors are LF and translated to the
target's own ending.
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys

_TESTS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TESTS)
_PL = os.path.join(_ROOT, 'py_placer', 'placement')

TARGETS = {
    'mt': os.path.join(_PL, 'mating.py'),
    'fp': os.path.join(_PL, 'floorplan.py'),
    'sd': os.path.join(_PL, 'seeder.py'),
    'bc': os.path.join(_ROOT, 'py_tools', 'board_context.py'),
}

T_FACE = os.path.join(_TESTS, 'test_1155_mating_face.py')
T_REV = os.path.join(_TESTS, 'test_1155_reversed_edge_connector.py')
T1172 = os.path.join(_TESTS, 'test_1172_edge_angle_and_courtyard.py')

# (name, target, old, new, tests, expect)
ROWS = [
    # -- the primitive (placement/mating.py) --
    ('local-frame-sign-flipped', 'mt',
     "    vx, vy = c * ux + s * uy, -s * ux + c * uy\n",
     "    vx, vy = c * ux - s * uy, s * ux + c * uy\n",
     (T_FACE,), 'KILLED'),
    ('disagreeing-sources-not-abstained', 'mt',
     "    if len(faces) > 1:\n",
     "    if False:\n",
     (T_FACE,), 'KILLED'),
    ('face-floor-lowered', 'mt',
     "FACE_MIN_OFFSET_MM = 0.57\n",
     "FACE_MIN_OFFSET_MM = 0.45\n",
     (T_FACE,), 'KILLED'),
    ('face-floor-raised', 'mt',
     "FACE_MIN_OFFSET_MM = 0.57\n",
     "FACE_MIN_OFFSET_MM = 0.65\n",
     (T_FACE,), 'KILLED'),
    ('strong-offset-lowered', 'mt',
     "FACE_STRONG_OFFSET_MM = 2.0\n",
     "FACE_STRONG_OFFSET_MM = 1.0\n",
     (T_FACE,), 'KILLED'),
    ('dominance-dropped', 'mt',
     "    if major < FACE_MIN_OFFSET_MM - _TOL_MM or major < FACE_DOMINANCE * minor:\n",
     "    if major < FACE_MIN_OFFSET_MM - _TOL_MM:\n",
     (T_FACE,), 'KILLED'),
    ('vertical-name-not-abstained', 'mt',
     "    if is_vertical_name(fp):\n",
     "    if False:\n",
     (T_FACE,), 'KILLED'),
    ('copper-plug-not-abstained', 'mt',
     "    if is_board_copper_plug(fp):\n",
     "    if False:\n",
     (T_FACE,), 'KILLED'),
    # -- the policy and the conjunct (placement/floorplan.py) --
    ('every-class-graded', 'fp',
     "    if cls == 'edge_receptacle':\n",
     "    if cls in ('edge_receptacle', 'edge_actuator', 'connector_affinity'):\n",
     (T_FACE, T_REV), 'KILLED'),
    ('vertical-mount-graded', 'fp',
     "    if mm in VERTICAL_MOUNTS:\n",
     "    if False:\n",
     (T_FACE,), 'KILLED'),
    ('strong-without-a-facing-edge', 'fp',
     "            'strong': bool(lf.strong and facing), 'reason': reason,\n",
     "            'strong': bool(lf.strong), 'reason': reason,\n",
     (T_FACE,), 'KILLED'),
    ('diagonal-reason-dropped', 'fp',
     "    reason = lf.reason or ('' if facing or not lf.face else\n",
     "    reason = lf.reason or ('' if True else\n",
     (T_REV,), 'KILLED'),
    ('conjunct-off', 'fp',
     "        if _ef['fails']:\n",
     "        if False:\n",
     (T_REV,), 'KILLED'),
    ('weak-face-errors-too', 'fp',
     "                severity=(ctx.sev('edge_connector') if _ef['strong']\n",
     "                severity=(ctx.sev('edge_connector') if True\n",
     (T_REV,), 'KILLED'),
    ('edgeless-entry-reads-pass', 'fp',
     "                            else 'no_edge' if not edge\n",
     "                            else 'pass' if not edge\n",
     (T_REV,), 'KILLED'),
    ('emitter-blesses-the-reversed-pose', 'fp',
     "    if ef['fails'] and ef['strong']:\n",
     "    if False:\n",
     (T_REV,), 'KILLED'),
    ('emitter-withholds-a-weak-face-too', 'fp',
     "    if ef['fails'] and ef['strong']:\n",
     "    if ef['fails']:\n",
     (T_REV,), 'KILLED'),
    ('emitter-withhold-pass-removed', 'fp',
     "        _withhold_reversed_edge(c, pcb_data, pcb_file, _emit_bodies)\n",
     "        pass\n",
     (T_REV,), 'KILLED'),
    ('withheld-entry-not-flagged-suspect', 'fp',
     "        entry['suspect'] = True\n",
     "        entry['suspect'] = False\n",
     (T_REV,), 'KILLED'),
    ('withheld-entry-loses-its-note', 'fp',
     "        entry['note'] = (f\"{entry['note']}; {note}\" if entry.get('note')\n",
     "        entry['note'] = (note if entry.get('note')\n",
     (T_REV,), 'KILLED'),
    ('emitter-calls-a-perpendicular-face-reversed', 'fp',
     "        if _OPPOSITE_EDGE.get(edge) == ef['facing']:\n",
     "        if True:\n",
     (T_FACE,), 'KILLED'),
    # -- the sheet (py_tools/board_context.py) --
    ('board-context-reads-the-nearest-edge', 'bc',
     "        edge = _LETTER.get(facing, near)\n",
     "        edge = near\n",
     (T_REV,), 'KILLED'),
    ('board-context-dist-to-the-nearest-edge', 'bc',
     "                    'dist_mm': round(max(0.0, dists[edge]), 3),\n",
     "                    'dist_mm': round(max(0.0, dist), 3),\n",
     (T_REV,), 'KILLED'),
    ('board-context-strong-without-an-edge', 'bc',
     "                    'facing_strong': bool(lf.strong and facing),\n",
     "                    'facing_strong': bool(lf.strong),\n",
     (T_REV,), 'KILLED'),
    ('board-context-interior-dropped-when-facing', 'bc',
     "                   'explain' if m['interior'] else ''))\n",
     "                   'explain' if False else ''))\n",
     (T_REV,), 'KILLED'),
    # -- the generator half (placement/seeder.py) --
    ('stage1-keeps-a-strong-reversed-face', 'sd',
     "            if strong and here['fails'] and facing:\n",
     "            if False:\n",
     (T1172,), 'KILLED'),
    ('stage1-pile-tries-reversed-angles', 'sd',
     "                order = first + ([] if strong else\n",
     "                order = first + ([] if False else\n",
     (T1172,), 'KILLED'),
    ('repair-facing-ladder-dropped', 'sd',
     "    if _facing1172 is not None and not any(\n",
     "    if False and not any(\n",
     (T1172,), 'KILLED'),
    ('repair-facing-reads-every-face', 'sd',
     "    if not any(v['policy'] == 'graded' and v['strong'] for v in per.values()):\n",
     "    if not any(v['policy'] == 'graded' for v in per.values()):\n",
     (T1172,), 'KILLED'),
    ('repair-ladder-turns-an-exempt-class', 'sd',
     "    if not any(v['policy'] == 'graded' and v['strong'] for v in per.values()):\n",
     "    if not any(v['strong'] for v in per.values()):\n",
     (T1172,), 'KILLED'),
    ('stage1-treats-a-weak-face-as-strong', 'sd',
     "            strong = graded and any(v['strong'] for v in per.values())\n",
     "            strong = bool(graded)\n",
     (T1172,), 'KILLED'),
    ('board-context-weak-face-unhedged', 'bc',
     "                + ('' if m.get('facing_strong') else ', a weak face')\n",
     "                + ''\n",
     (T_REV,), 'KILLED'),
]

sys.path.insert(0, _TESTS)
from mutation_anchors import preflight   # noqa: E402
preflight(__file__)


def _dirty(path):
    p = subprocess.run(['git', 'status', '--porcelain', '--', path],
                       capture_output=True, text=True, cwd=_ROOT)
    return bool(p.stdout.strip())


def _run_tests(tests):
    failed = []
    for t in tests:
        p = subprocess.run([sys.executable, '-X', 'utf8', t],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=2400, cwd=_ROOT)
        if p.returncode not in (0,):
            failed.append((os.path.basename(t), p.returncode,
                           [ln.strip()[:90] for ln in
                            ((p.stdout or '') + (p.stderr or '')).splitlines()
                            if 'FAIL' in ln or 'Error' in ln][:2]))
    return failed


def run(only=None):
    rows = [r for r in ROWS if only is None or r[0] == only]
    if not rows:
        print('no row named %r' % only)
        return 1
    for path in TARGETS.values():
        if _dirty(path):
            print('REFUSING: %s has uncommitted changes. Commit or stash '
                  'first -- this battery restores by overwriting.'
                  % os.path.basename(path))
            return 2
    # THE UNMUTATED BASELINE: every witness must pass as the code stands,
    # or a row it "kills" proves nothing.
    witnesses = sorted({t for r in rows for t in r[4]})
    base_fail = _run_tests(witnesses)
    if base_fail:
        print('REFUSING: witnesses fail UNMUTATED -- %s' % base_fail)
        return 2
    print('baseline: %d witnesses pass unmutated' % len(witnesses))
    orig = {k: io.open(v, encoding='utf-8', newline='').read()
            for k, v in TARGETS.items()}
    results = []
    try:
        for name, tgt, old, new, tests, expect in rows:
            path = TARGETS[tgt]
            base = orig[tgt]
            o, n = old, new
            if '\r\n' in base:
                o, n = o.replace('\n', '\r\n'), n.replace('\n', '\r\n')
            if base.count(o) != 1 or o == n:
                results.append((name, 'BROKEN', expect,
                                ['anchor matched %d times' % base.count(o)]))
                continue
            io.open(path, 'w', encoding='utf-8', newline='').write(
                base.replace(o, n, 1))
            try:
                failed = _run_tests(tests)
            finally:
                io.open(path, 'w', encoding='utf-8', newline='').write(base)
            got = 'KILLED' if failed else (
                'EQUIVALENT' if expect == 'EQUIVALENT' else 'SURVIVED')
            results.append((name, got, expect,
                            [str(f)[:150] for f in failed[:2]]))
            print('%-44s %s' % (name, results[-1][1]), flush=True)
    finally:
        for k, v in TARGETS.items():
            io.open(v, 'w', encoding='utf-8', newline='').write(orig[k])
    wrong = [r for r in results if r[1] != r[2]]
    print('')
    for name, verdict, expect, why in results:
        print('%-44s %-10s%s' % (name, verdict, '' if verdict == expect else
                                 '   <-- WRONG, expected %s' % expect))
        for w in why:
            print('      %s' % w)
    print('\n%d rows: %d killed, %d survived, %d equivalent, %d broken'
          % (len(results), sum(r[1] == 'KILLED' for r in results),
             sum(r[1] == 'SURVIVED' for r in results),
             sum(r[1] == 'EQUIVALENT' for r in results),
             sum(r[1] == 'BROKEN' for r in results)))
    return 1 if wrong else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--row', default=None, help='run only this row')
    a = ap.parse_args()
    return run(a.row)


if __name__ == '__main__':
    sys.exit(main())
