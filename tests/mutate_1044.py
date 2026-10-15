#!/usr/bin/env python3
"""The #1044 mutation battery: the edge seat's rule-area band conjunct and
place_seed's band gate.

`tests/test_1044_edge_seat_band.py` pins them on a semi-synthetic board
(rp2350 with a 3mm tracks-forbidden edge ring). Each row is a plausible
regression; each must be KILLED.

NOT named `test_*.py`, so `tests/run_all.py` does not collect it: it REWRITES
the engine in place. One writer per tree. It refuses to start on a dirty
engine.

    python3 tests/mutate_1044.py
    python3 tests/mutate_1044.py --list
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys

_TESTS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TESTS)

SEEDER = os.path.join(_ROOT, 'py_placer', 'placement', 'seeder.py')
PLACE_SEED = os.path.join(_ROOT, 'py_placer', 'place_seed.py')
TARGETS = {'s': SEEDER, 'p': PLACE_SEED}

T1044 = os.path.join(_TESTS, 'test_1044_edge_seat_band.py')

ROWS = [
    # The conjunct is gone: stage 1 seats J3's pad in the band again.
    ('the-edge-seat-ignores-the-band', 's',
     "            if ko > 1e-6:\n",
     "            if False:\n",
     (T1044,), 'KILLED'),

    # Seed-relative instead of absolute: the pile gives J3 no baseline, but
    # a pose already in the band is licensed.
    ('the-edge-seat-band-is-seed-relative', 's',
     "            ko = ctx.keepout_amount(part.ref, x, y, part.rot)\n",
     "            ko = 0.0 if ctx.keepout_ok(part.ref, x, y, part.rot) else 1.0\n",
     (T1044,), 'KILLED'),

    # The conjunct refuses but does not say why.
    ('the-band-refusal-is-unnamed', 's',
     "                    reasons.append(f\"pad copper {ko:.3f}mm into a rule-area \"\n",
     "                    reasons.append(f\"pad copper {ko:.3f}mm into a \"\n",
     (T1044,), 'KILLED'),

    # place_seed charges nothing for a band pad the seed placed.
    ('place-seed-charges-no-band-pad', 'p',
     "                    if r in _seeded and a > _band_in.get(r, 0.0) + 1e-6]\n",
     "                    if False]\n",
     (T1044,), 'KILLED'),

    # ... or charges an inherited one.
    ('place-seed-charges-an-inherited-band-pad', 'p',
     "                    if r in _seeded and a > _band_in.get(r, 0.0) + 1e-6]\n",
     "                    if a > 0]\n",
     (T1044,), 'KILLED'),

    # The gate line forgets the band. (#1241 appended `or oob` to the line;
    # the mutant keeps it, so only the band is forgotten.)
    ('the-gate-forgets-the-band', 'p',
     "    if not (unseated or own or my_pads or hole_delta or band or oob):\n",
     "    if not (unseated or own or my_pads or hole_delta or oob):\n",
     (T1044,), 'KILLED'),
]

# Every anchor must match its target exactly once BEFORE anything is
# rewritten. A stale anchor otherwise reports BROKEN mid-run, after the
# witnesses have been paid for; this is the one second (#877).
from mutation_anchors import preflight   # noqa: E402
preflight(__file__)


def _git_clean(paths):
    r = subprocess.run(['git', 'diff', '--quiet', '--'] + list(paths),
                       cwd=_ROOT)
    return r.returncode == 0


def _run(tests):
    for t in tests:
        r = subprocess.run([sys.executable, '-X', 'utf8', t],
                           cwd=_ROOT, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0:
            return True, f"{os.path.basename(t)} exit {r.returncode}"
    return False, "all named tests passed"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--row', action='append', default=None)
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()

    if a.list:
        for name, tgt, _o, _n, tests, exp in ROWS:
            print(f"  {exp:9} {name}  [{tgt}] "
                  f"-> {', '.join(os.path.basename(t) for t in tests)}")
        return 0

    rows = ROWS
    if a.row:
        unknown = [n for n in a.row if n not in {r[0] for r in ROWS}]
        if unknown:
            print(f"no such row: {', '.join(unknown)}; try --list",
                  file=sys.stderr)
            return 2
        rows = [r for r in ROWS if r[0] in set(a.row)]

    if not _git_clean(TARGETS.values()):
        print("REFUSED: the engine files are dirty. Restoring would write the "
              "COMMITTED text back over uncommitted work.", file=sys.stderr)
        return 2

    # The killers, UNMUTATED, must pass first: `_run` counts any non-zero
    # exit as KILLED, so a broken fixture or an ImportError would otherwise
    # score every row killed (mutate_974's gate, and its reason).
    killers = sorted({t for r in rows for t in r[4]})
    red, why = _run(killers)
    if red:
        print(f"REFUSED: the killer tests fail UNMUTATED ({why}) -- every row "
              f"would read KILLED for a reason unrelated to its mutation.",
              file=sys.stderr)
        return 2
    for t in killers:
        print(f"  unmutated {os.path.basename(t):40} passes")
    originals = {k: io.open(p, encoding='utf-8').read()
                 for k, p in TARGETS.items()}
    verdicts = []
    try:
        for name, tgt, old, new, tests, expect in rows:
            src = originals[tgt]
            n = src.count(old)
            if n != 1:
                verdicts.append((name, 'BROKEN', f"anchor matched {n} times"))
                print(f"  BROKEN   {name} -- anchor matched {n} times")
                continue
            io.open(TARGETS[tgt], 'w', encoding='utf-8', newline='').write(
                src.replace(old, new, 1))
            killed, why = _run(tests)
            io.open(TARGETS[tgt], 'w', encoding='utf-8',
                    newline='').write(src)
            got = 'KILLED' if killed else 'SURVIVED'
            mark = 'ok' if got == expect else 'WRONG'
            verdicts.append((name, got, why))
            print(f"  {got:9}{'' if mark == 'ok' else ' WRONG'} {name} -- {why}")
    finally:
        for k, p in TARGETS.items():
            io.open(p, 'w', encoding='utf-8', newline='').write(originals[k])

    wrong = [v for v, (name, got, _w) in zip(rows, verdicts)
             if got != v[5]]
    broken = [n for n, g, _w in verdicts if g == 'BROKEN']
    print(f"\n{len(verdicts)} row(s): "
          f"{sum(1 for _n, g, _w in verdicts if g == 'KILLED')} killed, "
          f"{sum(1 for _n, g, _w in verdicts if g == 'SURVIVED')} survived, "
          f"{len(broken)} broken, {len(wrong)} disagreeing with expectation")
    return 1 if (wrong or broken) else 0


if __name__ == '__main__':
    sys.exit(main())
