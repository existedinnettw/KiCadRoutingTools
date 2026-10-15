#!/usr/bin/env python3
"""#1194: three CLI papercuts from the fa10 campaign.

(a) `board_score`'s stdout SCORE_JSON carried every assembly pad-intersection
    pair uncapped -- 590 KB on One-Air-Max's pile, 396 KB on tigard's -- into
    every log and subprocess capture. The stdout copy now caps each assembly
    list (`STDOUT_LIST_CAP`) and carries `<list>_count` / `<list>_truncated`;
    `--json` keeps every entry. And the board is parsed ONCE in-process, so a
    parser warning (NET-TAGGED copper graphic) prints once, not three times.
(b) `place_pose --near` in a call with several pose ops was graded as an EXACT
    point (the snap applies to one op) and refused or written on that. It is
    a usage error now, at parse time, exit 2, and `--help` says so.
(c) `place_route_loop --accept-cmd` needed a judge printing `SCORE=` and the
    repo had none. `py_tools/accept_blocking.py` is that judge: board_score
    on the routed board with the intent, unrouted+broken first, then the rest
    of blocking, then vias. The loop's default (`better()`) is unchanged.

    python3 tests/test_1194_cli_papercuts.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ('py_router', 'py_placer', 'py_tools'):
    sys.path.insert(0, os.path.join(ROOT, _d))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_utils import check, evidence, tool    # noqa: E402

RUN_ALL_TIMEOUT = 900

TIGARD = os.path.join(ROOT, 'kicad_files', 'tigard.kicad_pcb')
ESP = os.path.join(ROOT, 'kicad_files', 'esp_prog.kicad_pcb')
MAKE_UNPLACED = os.path.join(ROOT, '.claude', 'skills', 'pcb-free-agent',
                             'scripts', 'make_unplaced.py')
SCORE = tool('board_score.py')
POSE = tool('place_pose.py')
LOOP = tool('place_route_loop.py')
JUDGE = tool('accept_blocking.py')
FLOORPLAN = tool('check_floorplan.py')


def _run(argv):
    return subprocess.run([sys.executable, '-X', 'utf8'] + [str(a) for a in argv],
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace', cwd=ROOT)


def _score_line(r):
    rows = [ln for ln in r.stdout.splitlines() if ln.startswith('SCORE_JSON=')]
    assert len(rows) == 1, (r.returncode, r.stdout[-400:], r.stderr[-400:])
    return rows[0]


# --------------------------------------------------------------------- (a) --

def test_stdout_payload_caps_assembly_lists_only_on_the_copy():
    import board_score as bs
    pairs = [{'a': 'R%d' % i, 'b': 'U1'} for i in range(bs.STDOUT_LIST_CAP + 30)]
    score = {'blocking': 1, 'components': {
        'assembly': {'pairs': pairs, 'pairs_count': len(pairs),
                     'pairs_truncated': 0, 'containments': [{'a': 'X'}],
                     'containments_count': 1, 'containments_truncated': 0},
        'drc': {'count': 0}}}
    out = bs.stdout_payload(score)
    asm = out['components']['assembly']
    assert len(asm['pairs']) == bs.STDOUT_LIST_CAP, len(asm['pairs'])
    assert asm['pairs'] == pairs[:bs.STDOUT_LIST_CAP]
    assert asm['pairs_count'] == len(pairs)
    assert asm['pairs_truncated'] == 30, asm['pairs_truncated']
    assert asm['containments'] == [{'a': 'X'}]
    assert asm['containments_truncated'] == 0
    # what --json writes is untouched
    assert len(score['components']['assembly']['pairs']) == len(pairs)
    assert score['components']['assembly']['pairs_truncated'] == 0
    # nothing long: the very same object, no copy
    short = {'components': {'assembly': {'pairs': pairs[:3]}}}
    assert bs.stdout_payload(short) is short
    assert bs.stdout_payload({'components': {'assembly': {'ran': False}}})
    print('  PASS: stdout copy capped at %d, --json copy whole'
          % bs.STDOUT_LIST_CAP)


def test_a_pile_prints_a_small_score_json_and_keeps_the_full_list():
    evidence(TIGARD, 'tigard board')
    evidence(MAKE_UNPLACED, 'make_unplaced.py')
    import board_score as bs
    with tempfile.TemporaryDirectory() as d:
        pile = os.path.join(d, 'pile.kicad_pcb')
        r = _run([MAKE_UNPLACED, TIGARD, pile])
        assert r.returncode == 0, r.stdout[-400:] + r.stderr[-400:]
        evidence(pile, 'the pile')
        js = os.path.join(d, 'score.json')
        r = _run([SCORE, pile, '--quiet', '--json', js])
        line = _score_line(r)
        with open(evidence(js, 'board_score --json'), encoding='utf-8') as f:
            full = json.load(f)
    s = json.loads(line[len('SCORE_JSON='):])
    asm, fasm = s['components']['assembly'], full['components']['assembly']
    # The pile has hundreds of pad intersections (971 on this commit); the
    # cap is what makes the line small.
    assert fasm['pairs_count'] == len(fasm['pairs']) > bs.STDOUT_LIST_CAP, \
        fasm['pairs_count']
    assert fasm['pairs_truncated'] == 0
    assert len(asm['pairs']) == bs.STDOUT_LIST_CAP
    assert asm['pairs_count'] == fasm['pairs_count']
    assert asm['pairs_truncated'] == fasm['pairs_count'] - bs.STDOUT_LIST_CAP
    for k in bs.ASSEMBLY_LISTS:
        assert len(asm[k]) <= bs.STDOUT_LIST_CAP, (k, len(asm[k]))
        assert asm[k + '_count'] == len(fasm[k]), k
    # Everything the score DECIDES is identical on both channels.
    for k in ('blocking', 'blocking_by', 'ungraded', 'unknown', 'quality',
              'board_sha'):
        assert s[k] == full[k], k
    assert len(line) < 60000, len(line)
    print('  PASS: SCORE_JSON %d bytes; pairs %d of %d on stdout, all in --json'
          % (len(line), len(asm['pairs']), fasm['pairs_count']))


def test_a_parser_warning_prints_once():
    """One in-process parse: board_score used to parse the board three times
    (unrouted shape, quality, the net-name audit), each printing the parser's
    two-line NET-TAGGED copper graphic warning."""
    evidence(ESP, 'esp_prog board')
    with open(ESP, encoding='utf-8') as f:
        text = f.read()
    blk = ('\t(gr_line\n\t\t(start 120 95)\n\t\t(end 122 95)\n\t\t(stroke\n'
           '\t\t\t(width 0.2)\n\t\t\t(type default)\n\t\t)\n'
           '\t\t(layer "F.Cu")\n\t\t(net "GND")\n'
           '\t\t(uuid "11111111-2222-3333-4444-555555555555")\n\t)\n')
    at = text.index('\t(gr_line\n')
    with tempfile.TemporaryDirectory() as d:
        tagged = os.path.join(d, 'tagged.kicad_pcb')
        with open(tagged, 'w', encoding='utf-8') as f:
            f.write(text[:at] + blk + text[at:])
        r = _run([SCORE, tagged, '--quiet'])
    _score_line(r)
    n = r.stdout.count('NET-TAGGED copper graphic')
    assert n == 1, (n, r.stdout[-600:])
    print('  PASS: the NET-TAGGED warning printed %d time' % n)


# --------------------------------------------------------------------- (b) --

def test_near_in_a_multi_op_call_is_a_usage_error():
    evidence(ESP, 'esp_prog board')
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'o.kicad_pcb')
        r = check([sys.executable, '-X', 'utf8', POSE, ESP, out,
                   'set', 'C3', '--near', '130', '95',
                   'set', 'C4', '--near', '131', '96'],
                  refuse='this call carries 2 (set C3, set C4)', code=2)
        assert 'one `set REF --near X Y` per call' in r.stderr, r.stderr[-400:]
        assert not os.path.exists(out), 'a usage error wrote the board'
        assert 'JSON_SUMMARY' not in r.stdout, 'graded before refusing'
        # A lock riding along is not a pose op.
        check([sys.executable, '-X', 'utf8', POSE, ESP, out, 'set', 'C3',
               '--near', '130', '95', 'lock', 'C3', '--dry-run',
               '--radius', '4'], accept=True)
    r = _run([POSE, '--help'])
    assert r.returncode == 0, r.stderr[-300:]
    flat = ' '.join(r.stdout.split())
    assert 'need a call with ONE pose op' in flat, r.stdout[-1500:]
    assert 'a second pose op is refused at exit 2' in flat, r.stdout[-1500:]
    print('  PASS: refused at exit 2 before grading; --help says so')


# --------------------------------------------------------------------- (c) --

def test_the_judge_orders_routed_outcome_then_blocking_then_vias():
    import accept_blocking as ab

    def doc(unrouted, broken, other, vias):
        return {'blocking': unrouted + broken + other,
                'blocking_by': {'unrouted': unrouted, 'broken': broken,
                                'drc': other},
                'quality': {'vias': vias}}

    s = lambda *a: ab.score_terms(doc(*a))[1]                  # noqa: E731
    assert s(0, 1, 0, 0) > s(0, 0, 999999, 999999)   # one open net outranks
    assert s(1, 0, 0, 0) == s(0, 1, 0, 0)            # unrouted ~ broken
    assert s(0, 0, 1, 0) > s(0, 0, 0, 999999)        # a blocker outranks vias
    assert s(0, 0, 0, 2) > s(0, 0, 0, 1)
    assert s(0, 0, 0, 0) == 0
    terms, score, why = ab.score_terms({'blocking': None, 'unknown': ['drc']})
    assert score is None and 'drc' in why, why
    terms, score, why = ab.score_terms(doc(0, 0, 0, 10 ** 6))
    assert score is None and 'band' in why, why
    print('  PASS: unrouted+broken > rest of blocking > vias; unknown and '
          'out-of-band refuse')


def test_the_loop_reads_the_judge_through_its_own_contract():
    evidence(ESP, 'esp_prog board')
    from place_route_loop import accept_score
    with tempfile.TemporaryDirectory() as d:
        intent = os.path.join(d, 'intent.json')
        r = _run([FLOORPLAN, ESP, '--emit-intent', intent])
        assert r.returncode == 0, r.stdout[-300:] + r.stderr[-300:]
        evidence(intent, 'the emitted intent')
        cmd = '"%s" -X utf8 "%s" --intent "%s"' % (sys.executable, JUDGE,
                                                    intent)
        score, note = accept_score(cmd, ESP, ESP, '')
        assert score is not None, note
        # esp_prog is unrouted: its open nets are the whole score.
        assert score >= 1e12 and score % 1e12 == 0, score
        # An intent the judge cannot read is a REJECT, never a blind score.
        bad = '"%s" -X utf8 "%s" --intent "%s"' % (
            sys.executable, JUDGE, os.path.join(d, 'missing.json'))
        score, note = accept_score(bad, ESP, ESP, '')
        assert score is None and 'exit 2' in note, note
    r = _run([LOOP, '--help'])
    assert 'py_tools/accept_blocking.py' in ' '.join(r.stdout.split()), \
        r.stdout[-800:]
    print('  PASS: the loop scores the judge; a missing intent rejects')


TESTS = [
    test_stdout_payload_caps_assembly_lists_only_on_the_copy,
    test_a_pile_prints_a_small_score_json_and_keeps_the_full_list,
    test_a_parser_warning_prints_once,
    test_near_in_a_multi_op_call_is_a_usage_error,
    test_the_judge_orders_routed_outcome_then_blocking_then_vias,
    test_the_loop_reads_the_judge_through_its_own_contract,
]


if __name__ == '__main__':
    only = sys.argv[1:]
    ran = 0
    for t in TESTS:
        if only and not any(o in t.__name__ for o in only):
            continue
        print(f"--- {t.__name__}")
        t()
        ran += 1
    if only and not ran:
        print(f"NO TEST matches {only}")
        sys.exit(2)
    print('ALL PASS')
