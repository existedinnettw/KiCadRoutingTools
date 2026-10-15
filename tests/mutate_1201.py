"""The #1201 mutation battery: a pin-1 dot on F.Fab is not a part's body.

One row per load-bearing line, each reverting it; every row names the test
that must fail. **THE ROWS TO LOOK AT FIRST if this file ever goes red**:

  * `guard-disabled` -- CM5_MINIMA_3's USB-C receptacles J101/USB701 were
    graded "seated 8.90mm / 11.71mm from the nearest edge" on a 0.06 mm dot;
  * `fab-rect-bypasses-the-guard` -- the quench's own .Fab reader would keep
    vetoing on the dot while the grader read the silk body;
  * `silk-relabelled-a-marker` -- check_assembly would again call a silk
    containment "a marker or a board-sized container".

NOT named `test_*.py`, so `tests/run_all.py` does not collect it: it REWRITES
the sources in place. One writer per tree. It refuses to start on a dirty
target, and it runs every witness UNMUTATED first -- a witness that already
fails would score every row as killed.

    python3 tests/mutate_1201.py
    python3 tests/mutate_1201.py --row guard-disabled

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
    'body': os.path.join(_PL, 'body.py'),
    'leg': os.path.join(_PL, 'legality.py'),
    'quench': os.path.join(_PL, 'quench.py'),
    'asm': os.path.join(_ROOT, 'py_tools', 'check_assembly.py'),
    'cg': os.path.join(_PL, 'connector_geometry.py'),
}

T1201 = os.path.join(_TESTS, 'test_1201_fab_guard.py')

# (name, target, old, new, tests, expect)
ROWS = [
    ('guard-disabled', 'body',
     "    if fab is not None and fab_cannot_be_body(fab, pads):\n",
     "    if False:\n",
     (T1201,), 'KILLED'),
    ('threshold-rejects-real-bodies', 'body',
     "FAB_MIN_PAD_AREA_FRAC = 0.02\n",
     "FAB_MIN_PAD_AREA_FRAC = 0.5\n",
     (T1201,), 'KILLED'),
    ('threshold-admits-the-dot', 'body',
     "FAB_MIN_PAD_AREA_FRAC = 0.02\n",
     "FAB_MIN_PAD_AREA_FRAC = 1e-5\n",
     (T1201,), 'KILLED'),
    ('threshold-inclusive', 'body',
     "    return _area(fab) < FAB_MIN_PAD_AREA_FRAC * pa\n",
     "    return _area(fab) <= FAB_MIN_PAD_AREA_FRAC * pa\n",
     (T1201,), 'KILLED'),
    ('a-zero-area-fab-line-is-a-body', 'body',
     "    return _area(fab) < FAB_MIN_PAD_AREA_FRAC * pa\n",
     "    return 0.0 < _area(fab) < FAB_MIN_PAD_AREA_FRAC * pa\n",
     (T1201,), 'KILLED'),
    ('a-rejected-part-occupies-its-silk', 'body',
     "    elif drawn_local is not None and not fab_rejected:\n",
     "    elif drawn_local is not None:\n",
     (T1201,), 'KILLED'),
    ('the-marking-leaves-the-occupancy', 'body',
     "        occupancy = _union(marking, pads)\n",
     "        pass\n",
     (T1201,), 'KILLED'),
    ('a-marking-body-reads-as-the-dot', 'body',
     "        body_local, source = (drawn_local or pads), SOURCE_FAB_MARKING\n",
     "        body_local, source = marking, SOURCE_FAB_MARKING\n",
     (T1201,), 'KILLED'),
    ('a-padless-fab-body-is-rejected', 'body',
     "    if fab is None or pads is None:\n"
     "        return False\n",
     "    if fab is None:\n"
     "        return False\n"
     "    if pads is None:\n"
     "        return True\n",
     (T1201,), 'KILLED'),
    ('local-bounds-drops-the-flag', 'leg',
     "        fab_rejected = bool(getattr(geom, 'fab_rejected', False))\n",
     "        fab_rejected = False\n",
     (T1201,), 'KILLED'),
    ('fab-rect-bypasses-the-guard', 'quench',
     "        if p is None or ref in self._fab_rejected_refs():\n",
     "        if p is None:\n",
     (T1201,), 'KILLED'),
    ('fab-shape-bypasses-the-guard', 'quench',
     "        if p is None or not sides or ref in self._fab_rejected_refs():\n",
     "        if p is None or not sides:\n",
     (T1201,), 'KILLED'),
    ('silk-relabelled-a-marker', 'leg',
     "        return 'silk_drawn'\n",
     "        return 'marker_class'\n",
     (T1201,), 'KILLED'),
    ('a-waiver-reads-as-silk', 'leg',
     "        if p.waiver in _GATE_EXEMPT:\n"
     "            return p.waiver\n"
     "        return 'silk_drawn'\n",
     "        return 'silk_drawn'\n",
     (T1201,), 'KILLED'),
    ('a-gating-pair-labelled-exempt', 'leg',
     "        if id(p) in _gating:\n"
     "            return ''\n",
     "",
     (T1201,), 'KILLED'),
    ('labels-only-on-containment-pairs', 'leg',
     "    pairs = [_stamped.get(id(p), p) for p in pairs]\n",
     "",
     (T1201,), 'KILLED'),
    ('bodyless-reads-the-raw-fab', 'quench',
     "                    drawn = g.drawn_local if g is not None else None\n",
     "                    drawn = self.fab_rect(r)\n",
     (T1201,), 'KILLED'),
    ('rejected-refs-not-reported', 'leg',
     "        fab_rejected_refs = sorted(r for r, _g in _bodies.items()\n",
     "        fab_rejected_refs = sorted(r for r, _g in {}.items()\n",
     (T1201,), 'KILLED'),
    ('the-pair-tag-names-no-reason', 'asm',
     '            tag = (f"  [does not gate: {_GATE_EXEMPT_WHY.get(_ex, _ex)}]"\n',
     "            tag = (''\n",
     (T1201,), 'KILLED'),
    ('the-marker-sentence-always-prints', 'asm',
     "            if set(_why) <= {'marker_class', 'container_class'}:\n",
     "            if True:\n",
     (T1201,), 'KILLED'),
    ('the-marker-sentence-forgets-containers', 'asm',
     "            if set(_why) <= {'marker_class', 'container_class'}:\n",
     "            if set(_why) <= {'marker_class'}:\n",
     (T1201,), 'KILLED'),
    ('the-still-gates-tag-dropped', 'asm',
     '                   f"  [waived:{q.waiver}, still gates]" if q.waived else \'\')\n',
     "                   '')\n",
     (T1201,), 'KILLED'),
    ('the-reason-count-dropped', 'asm',
     '            print("    None of these BLOCK: " + \'; \'.join(\n',
     '            print("    None of these BLOCK." + \'\'.join(\n',
     (T1201,), 'KILLED'),
    # ---- #1243: the #961 band reader takes the same guard -----------------
    ('conngeom-ignores-the-marking-guard', 'cg',
     "        if layer == side + '.Fab' and self._fab_is_marking(ref):\n",
     "        if False:\n",
     (T1201,), 'KILLED'),
    ('conngeom-marking-without-silk-reads-a-missing-layer', 'cg',
     "        if layer not in layers:\n",
     "        if False:\n",
     (T1201,), 'KILLED'),
    ('conngeom-dot-without-silk-names-no-layer', 'cg',
     "            return None, side + '.Fab', ('the Fab drawing is a marking, not '\n",
     "            return None, None, ('the Fab drawing is a marking, not '\n",
     (T1201,), 'KILLED'),
    ('conngeom-cache-ignores-the-guard', 'cg',
     "        key = (ref, side, fab_is_marking)\n",
     "        key = (ref, side)\n",
     (T1201,), 'KILLED'),
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
