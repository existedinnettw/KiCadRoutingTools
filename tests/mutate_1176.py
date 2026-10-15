"""The #1176 mutation battery: a mounting hole by its item name, not a substring.

One row per load-bearing line, each reverting it; every row names the test
that must fail. **THE ROW TO LOOK AT FIRST if this file ever goes red** is
`substring-rule-restored`: five tracked D-subs named
`..._MountingHolesOffset...` read `mount_hole`, so their edge was never
emitted and flat_hierarchy's edge rule reported "not applicable".

NOT named `test_*.py`, so `tests/run_all.py` does not collect it: it REWRITES
the sources in place. One writer per tree. It refuses to start on a dirty
target, and it runs every witness UNMUTATED first -- a witness that already
fails would score every row as killed.

    python3 tests/mutate_1176.py
    python3 tests/mutate_1176.py --row substring-rule-restored

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
    'pc': os.path.join(_PL, 'part_class.py'),
    'lock': os.path.join(_PL, 'lock_advisor.py'),
    'bc': os.path.join(_ROOT, 'py_tools', 'board_context.py'),
}

T1176 = os.path.join(_TESTS, 'test_1176_mount_hole_name.py')

# (name, target, old, new, tests, expect)
ROWS = [
    ('substring-rule-restored', 'pc',
     "    if is_mount_hole_name(name):\n",
     "    if 'mountinghole' in name:\n",
     (T1176,), 'KILLED'),
    ('prefix-tested-on-the-whole-name', 'pc',
     "    item = str(name or '').rsplit(':', 1)[-1].lower()\n",
     "    item = str(name or '').lower()\n",
     (T1176,), 'KILLED'),
    ('substring-test-on-the-item', 'pc',
     "_MOUNT_HOLE_MID = re.compile(r'mountinghole(?!s)')\n",
     "_MOUNT_HOLE_MID = re.compile(r'mountinghole')\n",
     (T1176,), 'KILLED'),
    ('mid-name-hole-not-read', 'pc',
     "            or _MOUNT_HOLE_MID.search(item) is not None)\n",
     "            or False)\n",
     (T1176,), 'KILLED'),
    ('lock-advisor-substring-restored', 'lock',
     "        _marker_name = (_is_mount_hole_name(fp_name)\n",
     "        _marker_name = ('mountinghole' in fp_name\n",
     (T1176,), 'KILLED'),
    ('board-context-substring-restored', 'bc',
     "    if is_mount_hole_name(name):\n",
     "    if 'mountinghole' in name:\n",
     (T1176,), 'KILLED'),
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
