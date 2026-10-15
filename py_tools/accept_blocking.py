#!/usr/bin/env python3
"""place_route_loop's --accept-cmd judge: board_score's blocking, routed outcome first.

    place_route_loop.py IN OUT --intent INTENT.json \\
        --accept-cmd "python3 py_tools/accept_blocking.py --intent INTENT.json"

The loop runs its judge as `CMD <placed> <routed> <route.json>` after every
round's route, keeps a round only when the judge's `SCORE=<float>` is strictly
LOWER than the incumbent's, and rejects a round whose judge exits non-zero or
prints no SCORE line. Its built-in comparison (`better()`: router failures,
then iterations) cannot see an intent rule, a DRC violation or an assembly
defect, and no repo tool printed the SCORE= line a judge needs (#1194), so
every intent-armed loop needed a hand-written one. This is that judge.

It runs `board_score` on the ROUTED board -- with `--intent`, so a broken
decap or zone rule counts in `blocking` -- and scores it in the order CLAUDE.md
("What a placement run is FOR") grades a placement:

  1. unrouted + broken      the routed outcome
  2. the rest of blocking   DRC, assembly, floorplan/intent, ...
  3. vias                   the quality tie-break once blocking is level

as ONE float, lexicographic by bands: (1) * 1e12 + (2) * 1e6 + (3). A term too
large for its band (a million DRC rows, nine thousand open nets) would order
the score wrongly, so it is refused instead (exit 1, which the loop reads as a
reject), never folded in. `ACCEPT_TERMS=` on the line before carries the three
terms and board_score's `blocking_by` for the log.

The intent reaches the judge INSIDE the --accept-cmd string, because the loop
appends only the three paths: pass the same `--intent` the loop was given.
`--baseline` (the loop's INPUT board, the one the rounds derive from) arms
board_score's moved-part courtyard gate; omitted, that gate stays in
board_score's `ungraded` list on every round alike. `--clearance` grades DRC
at a value other than the routed board's sibling .kicad_pro.

Exit 0 with a SCORE= line; 1 board_score could not grade (blocking unknown,
no JSON, a term out of band, or an --intent it left ungraded); 2 bad
arguments, an --intent / --baseline that is not a file included; 3 the routed
board is missing.
"""

#: #937 registry: which door(s) show this tool, and whether it changes
#: the board. Read by krt_registry.py -- by AST, never imported.
KRT_TOOL = {'scope': ['placement', 'combined'], 'kind': 'instrument'}

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD_SCORE = os.path.join(HERE, 'board_score.py')

#: The band each term is multiplied into, and the size a term must stay under
#: so the next band up is never reached. All three products stay below 2**53,
#: so the float is an exact integer and the order is exact.
ROUTED_BAND = 1e12
REST_BAND = 1e6
ROUTED_MAX = 9000


def score_terms(doc: dict):
    """(terms, score, why) from a board_score document. `score` is None, with
    `why`, when the document cannot be ordered: blocking unknown, or a term
    outside its band. Pure."""
    blocking = doc.get('blocking')
    by = doc.get('blocking_by') or {}
    if blocking is None:
        return None, None, ('board_score could not grade: blocking unknown '
                            '(asked-for component(s) did not run: %s)'
                            % ', '.join(doc.get('unknown') or ['?']))
    routed = int(by.get('unrouted') or 0) + int(by.get('broken') or 0)
    rest = int(blocking) - routed
    vias = int((doc.get('quality') or {}).get('vias') or 0)
    terms = {'unrouted_broken': routed, 'other_blocking': rest, 'vias': vias}
    if routed >= ROUTED_MAX or not 0 <= rest < REST_BAND \
            or not 0 <= vias < REST_BAND:
        return terms, None, ('a term is outside its band (unrouted+broken < '
                             '%d, other blocking and vias < %d), so one float '
                             'cannot order it' % (ROUTED_MAX, REST_BAND))
    return terms, routed * ROUTED_BAND + rest * REST_BAND + vias, ''


def build_parser():
    p = argparse.ArgumentParser(
        description=__doc__.split('\n\n')[0],
        epilog='Run by place_route_loop as CMD <placed> <routed> <route.json>.')
    p.add_argument('placed', help="the round's placed (unrouted) board")
    p.add_argument('routed', help="the round's routed board -- the one graded")
    p.add_argument('route_json', nargs='?', default='',
                   help="route.py's merged summary (accepted, unused)")
    p.add_argument('--intent', metavar='PATH',
                   help='floorplan intent JSON, handed to board_score; pass '
                        "the loop's own --intent")
    p.add_argument('--baseline', metavar='BOARD',
                   help="handed to board_score: the loop's INPUT board")
    p.add_argument('--clearance', type=float,
                   help='handed to board_score; omit it to grade at the '
                        "routed board's sibling .kicad_pro")
    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    for flag, path in (('--intent', args.intent), ('--baseline', args.baseline)):
        if path is not None and not os.path.isfile(path):
            # board_score would grade WITHOUT it and say so only in
            # `ungraded`: a judge asked to see the intent must not score a
            # round blind to it.
            p.error('%s %s is not a file' % (flag, path))
    if not os.path.isfile(args.routed):
        print('routed board not found: %s' % args.routed, file=sys.stderr)
        return 3
    with tempfile.TemporaryDirectory(prefix='accept_blocking_') as tmp:
        out = os.path.join(tmp, 'score.json')
        cmd = [sys.executable, '-X', 'utf8', BOARD_SCORE, args.routed,
               '--json', out, '--quiet']
        for flag, val in (('--intent', args.intent),
                          ('--baseline', args.baseline),
                          ('--clearance', args.clearance)):
            if val is not None:
                cmd += [flag, str(val)]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        if not os.path.isfile(out):
            tail = (r.stderr or r.stdout or '').strip().splitlines()
            print('board_score exit %d wrote no score%s' % (
                r.returncode, (': ' + tail[-1][:200]) if tail else ''),
                file=sys.stderr)
            return 1
        with open(out, encoding='utf-8') as f:
            doc = json.load(f)
    terms, score, why = score_terms(doc)
    if score is not None and args.intent \
            and (doc.get('blocking_by') or {}).get('floorplan') is None:
        score = None
        why = ('--intent was given but board_score did not grade it: %s'
               % (((doc.get('components') or {}).get('floorplan') or {})
                  .get('reason') or 'floorplan is ungraded'))
    print('ACCEPT_TERMS=' + json.dumps(
        {'terms': terms, 'blocking': doc.get('blocking'),
         'blocking_by': doc.get('blocking_by'),
         'ungraded': doc.get('ungraded')}, sort_keys=True))
    if score is None:
        print('accept_blocking: %s' % why, file=sys.stderr)
        return 1
    print('SCORE=%d' % score)
    return 0


if __name__ == '__main__':
    sys.exit(main())
