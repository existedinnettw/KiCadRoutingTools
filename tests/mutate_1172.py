"""The #1172 mutation battery: stage 1 chooses an edge connector's angle and
keeps its courtyard off what it has already seated.

One row per load-bearing line, each reverting it; every row names the tests
that must fail. **THE ROW TO LOOK AT FIRST if this file ever goes red** is
`chooser-off`: glasgow_revC's IDC box headers were seated at the pile's
angle 0, long axis across the board, and their shells interpenetrated.

NOT COVERED, said here rather than left to be assumed: `_stage1_one`'s undo
before the pad-only retry (a refused courtyard attempt's notes would leak),
`_packed_starts`' fit gate (opt-in), `_depth_order`'s deeper-than-the-board
filter, and place_seed's `edge_angle_chosen` summary key, which only a full
place_seed run reaches. (The #1125 walk's `_t2m` preference is covered, by
test_983's test_c5 -- the phase-5 verifier found the earlier note wrong.)

NOT named `test_*.py`, so `tests/run_all.py` does not collect it: it REWRITES
the sources in place. One writer per tree. It refuses to start on a dirty
target, and it runs every witness UNMUTATED first -- a witness that already
fails would score every row as killed.

    python3 tests/mutate_1172.py
    python3 tests/mutate_1172.py --row chooser-off

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
    'sd': os.path.join(_PL, 'seeder.py'),
}

T1172 = os.path.join(_TESTS, 'test_1172_edge_angle_and_courtyard.py')
T983 = os.path.join(_TESTS, 'test_983_seat_grade_bounds.py')

# (name, target, old, new, tests, expect)
ROWS = [
    # -- mechanism 1: stage 1 chooses the angle --
    ('chooser-off', 'sd',
     "_stage1_choose_edge_angle = True\n",
     "_stage1_choose_edge_angle = False\n",
     (T1172,), 'KILLED'),
    ('chosen-claim-read-as-a-candidate-set', 'sd',
     "    if isinstance(claim, _ChosenRot):\n",
     "    if False:\n",
     (T1172,), 'KILLED'),
    ('depth-order-reversed', 'sd',
     "    return [x[2] for x in sorted(fit)]\n",
     "    return [x[2] for x in sorted(fit, reverse=True)]\n",
     (T1172,), 'KILLED'),
    ('a-pile-is-not-uninformative', 'sd',
     "    if assessment is not None and assessment.pile:\n",
     "    if False:\n",
     (T1172,), 'KILLED'),
    ('every-input-is-uninformative', 'sd',
     "            if ref not in uninformative():\n",
     "            if False:\n",
     (T1172,), 'KILLED'),
    ('chosen-angle-not-reported', 'sd',
     "            'edge_angle_chosen': edge_angle_chosen,\n",
     "            'edge_angle_chosen': {},\n",
     (T1172,), 'KILLED'),
    ('turn-note-silenced', 'sd',
     "                    f\"edge connector {ref}: stage 1 turned it \"\n",
     "                    f\"edge connector {ref}: stage 1 set it \"\n",
     (T1172,), 'KILLED'),
    # -- mechanism 2: the courtyard tier --
    ('courtyard-tier-off', 'sd',
     "_edge_courtyard_tier = True\n",
     "_edge_courtyard_tier = False\n",
     (T1172,), 'KILLED'),
    ('rung-admission-skips-the-courtyard', 'sd',
     "                    if not _hit and _cy1172 is not None:\n",
     "                    if False:\n",
     (T1172,), 'KILLED'),
    ('tier-reads-the-pile', 'sd',
     "    peers = sorted(o for o in others if o != ref and o in state.parts)\n",
     "    peers = sorted(o for o in state.parts if o != ref)\n",
     (T1172,), 'KILLED'),
    ('tier-counts-disjoint-pairs', 'sd',
     "            and legality.pair_overlap_area_exact(me, gp[o]) > legality.EPS]\n",
     "            and legality.pair_overlap_area_exact(me, gp[o]) >= 0.0]\n",
     (T1172,), 'KILLED'),
    ('tier-ignores-courtyards-ignored', 'sd',
     "    if _cy_ignored:\n",
     "    if False:\n",
     (T1172,), 'KILLED'),
    ('no-pad-only-fallback', 'sd',
     "            if out[0] == 'skip' or not seen or (out[0] == 'clean'\n",
     "            if True or (out[0] == 'clean'\n",
     (T1172,), 'KILLED'),
    ('t2-seat-read-as-clean', 'sd',
     "                return 'clean_t2', out2[1]\n",
     "                return out2\n",
     (T1172,), 'KILLED'),
    ('packed-starts-off', 'sd',
     "    if len(specs) < 2 or any(_declared_frac(c) is not None for c in specs):\n",
     "    if True:\n",
     (T1172,), 'KILLED'),
    ('packed-starts-ignore-the-overlap-gate', 'sd',
     "    if not any(spans[i][1] > spans[i + 1][0] + 1e-6\n",
     "    if False and not any(spans[i][1] > spans[i + 1][0] + 1e-6\n",
     (T1172,), 'KILLED'),
    ('packed-starts-default-on', 'sd',
     "_stage1_packed_starts = False\n",
     "_stage1_packed_starts = True\n",
     (T1172,), 'KILLED'),
    # -- the phase-5 verifier's fixes --
    ('chosen-angle-kept-when-not-clean', 'sd',
     "            if _mine:\n",
     "            if False:\n",
     (T1172,), 'KILLED'),
    ('a-stacked-suspect-is-informative', 'sd',
     "    out = set(getattr(assessment, 'stacked_suspect_refs', None) or ()) & set(refs)\n",
     "    out = set()\n",
     (T1172,), 'KILLED'),
    ('a-part-off-the-outline-is-informative', 'sd',
     "            out.add(ref)\n",
     "            pass\n",
     (T1172,), 'KILLED'),
    ('a-declared-set-is-not-narrowed-to-its-face', 'sd',
     "                    if keep and len(keep) < len(dr[1]):\n",
     "                    if False:\n",
     (T1172,), 'KILLED'),
    ('a-declared-reversed-rotation-is-unnamed', 'sd',
     "                if strong and got['fails']:\n",
     "                if False:\n",
     (T1172,), 'KILLED'),
    ('facing-refusal-measured-at-the-old-angle', 'sd',
     "                part.rot = rot\n",
     "                pass\n",
     (T1172,), 'KILLED'),
    ('a-declared-set-with-no-facing-member-is-unnamed', 'sd',
     "                            f\"face point at the {edge} edge -- the author's \"\n",
     "                            f\"face point elsewhere -- the author's \"\n",
     (T1172,), 'KILLED'),
    # -- the round-2 verifier's fixes --
    ('a-diagonal-member-counts-as-facing', 'sd',
     "                                 if ef(m % 360.0)['facing'] == edge)\n",
     "                                 if not ef(m % 360.0)['fails'])\n",
     (T1172,), 'KILLED'),
    ('a-chosen-pad-clear-seat-loses-to-the-input', 'sd',
     "                if _t2m is not None:\n",
     "                if False:\n",
     (T1172,), 'KILLED'),
    ('a-fall-back-to-a-reversed-input-is-unnamed', 'sd',
     "                       f\"from the {edge} edge, which the facing grade \"\n",
     "                       f\"from the {edge} edge, which nothing \"\n",
     (T1172,), 'KILLED'),
    # -- the round-3 verifier's: each SURVIVED test_1172 before its test --
    ('an-angle-deeper-than-the-board-ignores-its-band', 'sd',
     "    span += max(0.0, float(reach or 0.0))\n",
     "    span += 0.0\n",
     (T1172,), 'KILLED'),
    ('the-band-max-is-not-passed-to-the-depth-filter', 'sd',
     "                                 (c.get('overhang_mm') or {}).get('max'))\n",
     "                                 None)\n",
     (T1172,), 'KILLED'),
    ('a-crowded-input-is-called-clear', 'sd',
     "                        'crowded': 'which seats it crowding what is placed',\n",
     "                        'crowded': 'which seats it clear',\n",
     (T1172,), 'KILLED'),
    ('a-clean-input-is-called-crowded', 'sd',
     "                _how = {'clean': 'which seats it clear',\n",
     "                _how = {'clean': 'which seats it crowding what is placed',\n",
     (T1172,), 'KILLED'),
    ('every-fall-back-faces-away', 'sd',
     "                _away = (stage1_why.get(_ref1125) or '').startswith(\n",
     "                _away = True or (stage1_why.get(_ref1125) or '').startswith(\n",
     (T1172,), 'KILLED'),
    ('the-pad-clear-redo-is-the-first-angle', 'sd',
     "                    _stage1_one(k, c, _member1125=_t2m)\n",
     "                    _stage1_one(k, c)\n",
     (T1172,), 'KILLED'),
    # -- the PR review's: a declared set is reordered facing-first, never
    #    narrowed, so a clean non-facing member beats a crowded facing one --
    ('a-declared-set-is-narrowed-not-reordered', 'sd',
     "    return tuple(_group(fac) + _group([m for m in ms if m not in fac]))\n",
     "    return tuple(_group(fac))\n",
     (T1172,), 'KILLED'),
    # -- the PR review's: pad copper at the board edge outranks a courtyard
    #    overlap, in one attempt and across the #1125 walk --
    ('a-floor-short-courtyard-clear-seat-wins', 'sd',
     "            _short = out[0] == 'clean' and ref in edge_floor_fallback\n",
     "            _short = False\n",
     (T1172,), 'KILLED'),
    ('a-walked-member-short-of-the-floor-wins', 'sd',
     "                        and _ref1125 in edge_floor_fallback):\n",
     "                        and False):\n",
     (T1172,), 'KILLED'),
    # -- the PR review's: chosen angles that do not fit the edge at all
    #    still owe the part its input-angle attempt --
    ('a-chosen-angle-that-does-not-fit-skips-the-input-attempt', 'sd',
     "            _out = 'skip_late' if _skip1172 else _out\n",
     "            _out = _out\n",
     (T1172,), 'KILLED'),
    # -- the final review's: the #1125 walk's note named the wrong seat --
    ('the-walk-note-names-the-crowded-member', 'sd',
     "                       if _t2m is not None else\n",
     "                       if False else\n",
     (T983,), 'KILLED'),
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
