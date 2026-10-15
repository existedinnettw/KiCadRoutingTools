#!/usr/bin/env python3
"""Rank place_seed seeds by ONE identical full-board probe each.

Run 7 hand-rolled exactly this: three seeds, each graded against the intent,
then compared -- and the comparison that actually discriminated was a
FULL-BOARD probe route per seed, not the portfolio's shared-window probe and
not crossings (crossings ranked seed 0 first; the probe measured seed 2 at
39 failures vs seed 0's 53 and run 7 shipped on seed 2). The skill's ladder
now calls for identical full-board probes on the seed axis; this CLI is that
step as one command instead of an afternoon of bookkeeping.

Per seed: place_seed subprocess (the seed must GRADE CLEAN against its own
intent -- a seed failing its gate is recorded but never ranked) -> one
full-board probe via converge.probe_route, every seed routed with the SAME
net patterns and the SAME route args and with NO timeout (#713: a verdict a
clock erased was dropped from the ranking, not ranked worse). A seed that
produces no verdict carries a `status` naming why. Seeds rank by probe
failures, then -- when failures tie -- probe vias, then probe copper_mm
(#1191: CLAUDE.md's tie-break; board_score's own copper measure, read off
the routed probe board), then router iterations.

A seed whose ONLY gate failure is decap rules (`decap_distance` /
`decap_pin_distance`, nothing unseated, no copper conflict seeded) goes
through the SEED-THEN-REPAIR arm (#1191): `place_seed --repair
--repair-decaps` writes seed_N_repaired.kicad_pcb, and when that passes the
same gate it is the board probed and ranked (gate `rep`). On a pile whose
intent arms the decap rule that is every seed, which used to leave nothing
rankable. Output: a ranked table, seeds.json in --out-dir, and a
JSON_SUMMARY line with best_seed and best_board.

    python compare_seeds.py board.kicad_pcb --intent floorplan.json \\
        --seeds 0 1 2 --out-dir seedcmp --ignore-nets GND VCC

Exit 0 when at least one seed was probed; 4 when every seed failed its
intent gate (nothing rankable); 2 for usage errors.
"""

#: #937 registry: which door(s) show this tool, and whether it changes
#: the board. Read by krt_registry.py -- by AST, never imported.
KRT_TOOL = {'scope': ['placement'], 'kind': 'actor'}

import _path  # noqa: F401  (py_placer -> py_router/py_tools on sys.path)
import argparse
import json
import os
import shlex
import subprocess
import sys

# REPO root (this script lives in py_placer/): subprocesses run with cwd=ROOT.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _split_forwarded(tokens):
    """Normalize --seed-args/--route-args values into an argv list.

    argparse's nargs="+" refuses dash-prefixed values, so forwarded flags must
    arrive as ONE quoted string via the = form
    (--seed-args='--clearance 0.15 --max-displacement 2'); split those on
    shell rules. Plain tokens pass through untouched.
    """
    out = []
    for t in tokens or []:
        if any(ch.isspace() for ch in t):
            out.extend(shlex.split(t))
        else:
            out.append(t)
    return out


def build_parser():
    p = argparse.ArgumentParser(
        description="Rank place_seed seeds by one identical full-board "
                    "probe each.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog="""
Examples:
  python compare_seeds.py board.kicad_pcb --intent fp.json --seeds 0 1 2 \\
      --out-dir seedcmp --ignore-nets GND VCC
  python compare_seeds.py board.kicad_pcb --intent fp.json --seeds 0 5 \\
      --out-dir seedcmp --route-args='--clearance 0.15'
""")
    p.add_argument("input_file", help="Unplaced board (outline + parts pile)")
    p.add_argument("--intent", required=True,
                   help="Floorplan intent, forwarded to place_seed")
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2],
                   help="Seed values to generate and rank (default: 0 1 2)")
    p.add_argument("--out-dir", required=True,
                   help="Directory for seed boards + seeds.json")
    p.add_argument("--ignore-nets", nargs="+", default=None,
                   help="Net patterns excluded from the probe (plane-routed "
                        "rails) and forwarded to place_seed's polish")
    p.add_argument("--seed-args", nargs="+", default=None,
                   help="Extra args forwarded to every place_seed call. "
                        "Dash-prefixed flags must ride in ONE quoted = value: "
                        "--seed-args='--clearance 0.15 --max-displacement 2'")
    p.add_argument("--route-args", nargs="+", default=None,
                   help="Extra args for every probe route -- identical "
                        "across seeds by construction. Same quoting rule as "
                        "--seed-args: --route-args='--clearance 0.15'")
    # NO --route-timeout (#713 item 2). It defaulted to 1800 here and 900 in
    # place_portfolio -- two clocks, two defaults, neither covered by a test or
    # named in any doc -- and a timed-out seed became `failures: None`, which
    # `_key` sorts into tier 2 and `best` then never selects. A comparison one
    # of whose arms a clock erased is not one. The bound is SCOPE: the net
    # patterns already passed below.
    p.add_argument("--skip-gated", action="store_true", default=True,
                   help=argparse.SUPPRESS)
    p.add_argument("--probe-gated", action="store_true",
                   help="Probe seeds that FAILED their intent gate too "
                        "(recorded but still never ranked above a clean "
                        "seed)")
    return p


def run_place_seed(input_file, intent, seed, out_board, *, ignore_nets=None,
                   seed_args=()):
    """One place_seed subprocess, run with cwd=ROOT: `(CompletedProcess,
    JSON_SUMMARY dict)`, the dict empty when no summary was printed. Shared
    with rank_rotations.py (#1113), so a seed arm there is exactly one here."""
    argv = [sys.executable, '-X', 'utf8',
            os.path.join(ROOT, 'py_placer', 'place_seed.py'), input_file, out_board,
            '--intent', intent, '--seed', str(seed)]
    if ignore_nets:
        argv += ['--ignore-nets'] + list(ignore_nets)
    if seed_args:
        argv += list(seed_args)
    r = subprocess.run(argv, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', cwd=ROOT)
    summary = {}
    for line in r.stdout.splitlines():
        if line.startswith('JSON_SUMMARY:'):
            try:
                summary = json.loads(line.split(':', 1)[1])
            except ValueError:
                pass
    return r, summary


def _run_place_seed(args, seed, out_board):
    return run_place_seed(args.input_file, args.intent, seed, out_board,
                          ignore_nets=args.ignore_nets,
                          seed_args=args.seed_args)


def probe_nets(ignore_nets):
    """The full-board probe's net patterns: every net but the ignored ones."""
    return ['*'] + [f'!{p}' for p in (ignore_nets or [])]


def probe_full(board, nets, route_args):
    """One full-board probe route (`converge.probe_route`, no timeout --
    #713). Shared with rank_rotations.py (#1113).

    #1191: also `copper_mm`, board_score's own `quality()` read off the
    routed probe board (ROUTED copper; footprint graphic copper excluded),
    because once failures tie the tie-break is vias then copper
    (CLAUDE.md), and route.py's summary carries vias but no length. One
    parse of a board the probe already wrote; None when there is no verdict.
    """
    import tempfile
    from converge import probe_route
    out = os.path.join(tempfile.mkdtemp(prefix='probe_full_'),
                       'routed.kicad_pcb')
    pr = probe_route(board, nets, extra_args=route_args or [], out=out)
    pr['probe_kind'] = 'full'
    pr['copper_mm'] = None
    if pr.get('status') == 'ok' and os.path.isfile(out):
        from board_score import quality
        pr['copper_mm'] = quality(out).get('copper_mm')
    return pr


def probe_order(pr):
    """The probe part of a ranking key (#1191): failures, then vias, then
    copper -- CLAUDE.md's tie-break once blocking ties, and the probe's own
    numbers. A missing value sorts after any measured one."""
    big = float('inf')
    pr = pr or {}

    def _v(k):
        v = pr.get(k)
        return big if v is None else v
    return (_v('failures'), _v('vias'), _v('copper_mm'))


def seed_key(row):
    """A seed row's rank, lower first: clean-gated probed seeds by (failures,
    vias, copper_mm, iterations, seed); probed-but-gated after them;
    everything else last, unranked. #1191: vias then copper break a
    failures tie (CLAUDE.md's tie-break once blocking ties) -- they used to
    be ignored, so seeds that all routed were ordered by router iterations."""
    pr = row.get('probe') or {}
    f = pr.get('failures')
    return ((0 if (f is not None and not row.get('gated')) else
             1 if f is not None else 2,)
            + probe_order(pr)
            + (pr.get('iterations') or 0, row['seed']))


#: The rules `place_seed --repair --repair-decaps` seats a cap FOR -- read
#: from the seeder, never restated here.
def _decap_rules():
    from placement.seeder import DECAP_RUNG_RULES
    return frozenset(DECAP_RUNG_RULES)


def decap_only(summary):
    """True when a gated seed's ONLY gate failure is decap rules (#1191):
    its own grade errors are all `decap_distance` / `decap_pin_distance`,
    and no other gate channel fired (no part unseated, no pad or hole
    conflict seeded, no keep-out copper, no pad copper left past the outline
    (#1241), no fixed pose refused). That is the
    failure `place_seed --repair --repair-decaps` exists for. The channels
    are place_seed's `gate_reason` inputs as its summary publishes them, and
    the fixed-pose one is its own `fixed_pose_reason`."""
    from place_seed import fixed_pose_reason
    rules = summary.get('grade_error_rules') or {}
    return (bool(rules) and set(rules) <= _decap_rules()
            and not summary.get('unseated')
            and not summary.get('pad_conflicts_seeded')
            and not (summary.get('hole_conflicts_added') or 0) > 0
            and not summary.get('keepout_copper_seeded')
            and not summary.get('pad_copper_off_board_seeded')
            and not summary.get('pad_copper_off_board_locked')
            and fixed_pose_reason(summary) is None)


def run_decap_repair(seed_board, intent, out_board, *, ignore_nets=None,
                     seed_args=()):
    """`place_seed --repair --repair-decaps` on a seed: `(CompletedProcess,
    JSON_SUMMARY dict)`. The seed's own args ride along (its clearance is
    the grade's), except `--force`, which --repair refuses."""
    argv = [sys.executable, '-X', 'utf8',
            os.path.join(ROOT, 'py_placer', 'place_seed.py'), seed_board,
            out_board, '--intent', intent, '--repair', '--repair-decaps']
    if ignore_nets:
        argv += ['--ignore-nets'] + list(ignore_nets)
    argv += [a for a in (seed_args or ()) if a != '--force']
    r = subprocess.run(argv, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', cwd=ROOT)
    summary = {}
    for line in r.stdout.splitlines():
        if line.startswith('JSON_SUMMARY:'):
            try:
                summary = json.loads(line.split(':', 1)[1])
            except ValueError:
                pass
    return r, summary


def main():
    parser = build_parser()
    args = parser.parse_args()
    # Subprocesses run with cwd=ROOT: a caller-relative path silently breaks
    # every one of them (the place_portfolio lesson, 8bd1344). Absolutize at
    # the door.
    args.input_file = os.path.abspath(args.input_file)
    args.intent = os.path.abspath(args.intent)
    args.out_dir = os.path.abspath(args.out_dir)
    args.seed_args = _split_forwarded(args.seed_args)
    args.route_args = _split_forwarded(args.route_args)
    if len(set(args.seeds)) != len(args.seeds):
        parser.error("--seeds has duplicates")
    os.makedirs(args.out_dir, exist_ok=True)
    try:
        from redo_record import record_invocation
        record_invocation()
    except Exception:
        pass


    full_nets = probe_nets(args.ignore_nets)
    rows = []
    for seed in args.seeds:
        out_board = os.path.join(args.out_dir, f'seed_{seed}.kicad_pcb')
        print(f"[seed {seed}] place_seed -> {os.path.basename(out_board)}")
        r, s = _run_place_seed(args, seed, out_board)
        if r.returncode == 5:
            # #959 (#998): the PLAN was refused before anything was written,
            # and the plan is the same for every seed -- so every seed would
            # print the same refusal. Say it once and stop.
            print(f"[seed {seed}] the zone plan is refused before seeding "
                  f"(place_seed exit 5): "
                  + (r.stderr or r.stdout)[-400:].strip())
            rows.append({'seed': seed, 'board': None, 'place_seed_rc': 5,
                         'refused': 'plan_check', 'probe': None,
                         'note': 'plan refused; no seed was written'})
            break
        row = {'seed': seed, 'board': out_board,
               'place_seed_rc': r.returncode,
               'grade_errors': s.get('grade_errors'),
               'grade_error_rules': s.get('grade_error_rules'),
               'unseated': s.get('unseated'),
               'crossings': s.get('crossings'), 'hpwl': s.get('hpwl'),
               'probe': None}
        if r.returncode not in (0, 4):
            row['note'] = ('place_seed failed: '
                           + (r.stderr or r.stdout)[-300:].strip())
            print(f"[seed {seed}] FAILED (rc={r.returncode})")
            rows.append(row)
            continue
        row['gated'] = (r.returncode == 4)
        if row['gated'] and decap_only(s):
            # #1191: the SEED-THEN-REPAIR arm. A seed whose only gate failure
            # is decap rules is what `--repair --repair-decaps` is for, and on
            # a pile whose intent arms them (`--decap-claim-after-ics`) it is
            # every seed: StickHub 2-7 all gated, nothing rankable, exit 4,
            # while seed 4 repaired to grade_errors 0 in 23 s. Rank the
            # REPAIRED board -- the one you would keep -- and only when the
            # repair passes the same gate (its exit 0).
            fixed = os.path.join(args.out_dir,
                                 f'seed_{seed}_repaired.kicad_pcb')
            print(f"[seed {seed}] fails its intent gate on decap rules only "
                  f"({s.get('grade_error_rules')}) -- place_seed --repair "
                  f"--repair-decaps -> {os.path.basename(fixed)}")
            rr, rs = run_decap_repair(out_board, args.intent, fixed,
                                      ignore_nets=args.ignore_nets,
                                      seed_args=args.seed_args)
            row['repair'] = {'board': fixed, 'rc': rr.returncode,
                             'grade_errors': rs.get('grade_errors'),
                             'grade_error_rules': rs.get('grade_error_rules'),
                             'unresolved_by_rule': rs.get('unresolved_by_rule'),
                             'moved_refs': rs.get('moved_refs'),
                             'max_move_mm': rs.get('max_move_mm')}
            if rr.returncode == 0 and os.path.isfile(fixed):
                row.update(board=fixed, gated=False, repaired=True)
                print(f"[seed {seed}] repaired: grade errors "
                      f"{s.get('grade_errors')} -> {rs.get('grade_errors')}, "
                      f"moved {rs.get('moved_refs')}")
            else:
                print(f"[seed {seed}] the decap repair did not clear the gate "
                      f"(rc={rr.returncode}, unresolved "
                      f"{rs.get('unresolved_by_rule')})")
        if row['gated']:
            print(f"[seed {seed}] fails its intent gate "
                  f"({s.get('grade_errors')} error(s), "
                  f"{s.get('unseated')} unseated)"
                  + ("" if args.probe_gated else " -- not probed"))
            if not args.probe_gated:
                rows.append(row)
                continue
        print(f"[seed {seed}] probing full board "
              f"({len(full_nets)} pattern(s))")
        row['probe'] = probe_full(out_board, full_nets, args.route_args)
        if row['probe']['failures'] is None:
            # Previously silent: the only print sat on the success path, so a
            # seed that produced no verdict vanished from the console AND from
            # the ranking.
            print(f"[seed {seed}] NO VERDICT ({row['probe']['status']}: "
                  f"{row['probe']['note']})")
        else:
            print(f"[seed {seed}] failures={row['probe']['failures']} "
                  f"({row['probe']['note']})")
        rows.append(row)

    ranked = sorted(rows, key=seed_key)
    print("\nseed  gate   failures  vias  copper_mm  iterations  crossings  hpwl")
    for row in ranked:
        pr = row.get('probe') or {}
        gate = ('FAIL' if row.get('gated') else
                'rep' if row.get('repaired') else
                'ok' if row.get('place_seed_rc') == 0 else 'ERR')
        print(f"{row['seed']:>4}  {gate:<5}  "
              f"{str(pr.get('failures', '-')):>8}  "
              f"{str(pr.get('vias', '-')):>4}  "
              f"{str(pr.get('copper_mm', '-')):>9}  "
              f"{str(pr.get('iterations', '-')):>10}  "
              f"{str(row.get('crossings', '-')):>9}  "
              f"{str(row.get('hpwl', '-')):>8}")
    if any(r.get('repaired') for r in rows):
        print("(gate `rep`: seeded, then repaired by place_seed --repair "
              "--repair-decaps; probed on the repaired board, crossings/hpwl "
              "are the seed's)")

    best = next((r for r in ranked
                 if (r.get('probe') or {}).get('failures') is not None
                 and not r.get('gated')), None)
    doc = {'input': args.input_file, 'intent': args.intent,
           'seeds': args.seeds, 'probe_nets': full_nets,
           'route_args': args.route_args or [], 'rows': rows,
           'ranking': [r['seed'] for r in ranked],
           'best_seed': best['seed'] if best else None}
    doc_path = os.path.join(args.out_dir, 'seeds.json')
    with open(doc_path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    print(f"Wrote {doc_path}")
    print("JSON_SUMMARY: " + json.dumps({
        'seeds': len(args.seeds), 'probed': sum(1 for r in rows if r['probe']),
        'gated': sum(1 for r in rows if r.get('gated')),
        'repaired': sum(1 for r in rows if r.get('repaired')),
        'best_seed': best['seed'] if best else None,
        'best_board': best['board'] if best else None,
        'best_failures': ((best.get('probe') or {}).get('failures')
                          if best else None),
        'best_vias': ((best.get('probe') or {}).get('vias')
                      if best else None),
        'best_copper_mm': ((best.get('probe') or {}).get('copper_mm')
                           if best else None),
        'out_dir': args.out_dir}, sort_keys=True))
    if best is not None:
        return 0
    if rows and any(r.get('refused') == 'plan_check' for r in rows):
        # #959 (#998): no seed was written, so there is nothing to rank or
        # probe -- say THAT, not "not probed".
        print("compare_seeds: the zone plan was refused before any seed was "
              "written (place_seed exit 5) -- fix the plan; `check_floorplan "
              "--intent <plan> --plan-only` checks it without seeding.",
              file=sys.stderr)
        return 4
    if rows and all(r.get('gated') for r in rows):
        tried = [r for r in rows if r.get('repair')]
        if tried and len(tried) == len(rows):
            # #1191: every seed failed on decap rules alone, and the repair
            # arm already ran the tool for that. Say what it left, not
            # "fix the intent", which reads as "the intent is wrong".
            print("compare_seeds: every seed failed its intent gate on decap "
                  "rules only, and `place_seed --repair --repair-decaps` did "
                  "not clear them -- nothing rankable. Still unresolved: "
                  + '; '.join(f"seed {r['seed']}: "
                              f"{r['repair'].get('unresolved_by_rule')}"
                              for r in tried[:6])
                  + ". Seat those caps by hand (place_pose.py), or loosen "
                    "the decap limits the intent declares.", file=sys.stderr)
        else:
            print("compare_seeds: every seed failed its intent gate -- "
                  "nothing rankable"
                  + (f" ({len(tried)} failed on decap rules only and were "
                     f"not cleared by place_seed --repair --repair-decaps)"
                     if tried else '')
                  + ". Fix the intent or the pile first.", file=sys.stderr)
        return 4
    # `no winner` is `nothing rankable`, full stop (#713 item 2). The old form
    # was `4 if not any(r['probe'])`, and a no-verdict row is a truthy dict --
    # so a run where every probe failed returned 0 with `best_seed: null`,
    # contradicting docs/utilities.md's own contract ("exit 0 with a ranked
    # winner, 4 when nothing was rankable"). A caller branching on the exit
    # code read a total failure as a success.
    _no_verdict = [r for r in rows
                   if (r.get('probe') or {}).get('failures') is None]
    if _no_verdict:
        print(f"compare_seeds: {len(_no_verdict)} of {len(rows)} seed(s) "
              f"produced NO VERDICT and none was rankable -- "
              + '; '.join(f"seed {r['seed']}: "
                          f"{(r.get('probe') or {}).get('status', 'not probed')}"
                          for r in _no_verdict[:6]), file=sys.stderr)
    return 4


if __name__ == "__main__":
    sys.exit(main())
