#!/usr/bin/env python3
"""#1209: a snap that refuses every candidate says what its census found.

fa10 board 08: `set SW501 --near 130.9 31.0 --radius 4 --snap-tries 30`
re-graded 32 candidates, every one failing `hole_conflicts` against the CM5
module's NPTH mount hole, and then ended its refusal with "The pose ranker's
nearest candidate is x=130.9 y=31 rot=0 (0.000 mm away) -- try it with --snap".
That was the requested pose, refused moments earlier in the same call, and
`--snap` was already in effect: the snap path stored the first RANKED
candidate as `nearest_legal` before any re-grade, and the refusal printed it
with the advice meant for a call that had not snapped. What each case pins:

* a pose the snap's own re-grade refused is never offered back -- forced
  in-process by a ranker that hands back the requested pose itself, which is
  the disagreement the issue measured (ranker legal, hole grade not);
* the refusal names the categories and the PAIRS that refused the candidates
  (`hole_conflicts x4 (H1/TP1)`), and the JSON carries the same census;
* a call that snapped is never told to "try it with --snap"; the levers it
  still has (`--snap-tries`, `--radius`) are named when they exist;
* a call that did NOT snap still gets the unverified suggestion and the
  `--snap` advice, which is the right advice there;
* a snap that succeeds names, as `nearest_legal`, only the pose it wrote.

    python3 tests/test_1209_snap_refusal_census.py
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

ESP = os.path.join(ROOT, 'kicad_files', 'esp_prog.kicad_pcb')
TIGARD = os.path.join(ROOT, 'kicad_files', 'tigard.kicad_pcb')
POSE = tool('place_pose.py')

#: esp_prog's C4 pose: C3 there stacks on Q2/Y1/C4, and nothing within 4 mm
#: grades no worse (test_892 measured it), so a snap there refuses.
C4_X, C4_Y = '136.4', '103.8'
#: tigard's TP1 (a through-hole test point) beside the NPTH mounting hole H1
#: at (33, 33): the only thing it can hit there is the hole's keep-out.
TP1_NEAR = ('35.2', '33.0')


def _summary(r):
    rows = [ln for ln in r.stdout.splitlines() if ln.startswith('JSON_SUMMARY:')]
    assert len(rows) == 1, (r.returncode, r.stdout[-600:], r.stderr[-600:])
    return json.loads(rows[0].split(':', 1)[1])


def _refusal(r):
    return next((ln for ln in r.stderr.splitlines()
                 if ln.startswith('place_pose REFUSED')), '')


def test_a_refused_ranked_pose_is_not_offered_back():
    """The issue's shape, forced: the ranker calls the requested pose legal
    (dist 0), the re-grade refuses it, and nothing else is in reach."""
    import pose_score
    from kicad_parser import parse_kicad_pcb
    from placement import pose_ops
    evidence(ESP, 'esp_prog board')
    real = pose_score.rank_poses

    def ranker_that_likes_the_request(pcb, board_path, ref, **kw):
        fp = pcb.footprints[ref]
        return [{'x': fp.x, 'y': fp.y, 'rot': (kw.get('rotations')
                                                or (fp.rotation,))[0],
                 'dist_mm': 0.0, 'legal': True, 'cost': 0.0}]

    pose_score.rank_poses = ranker_that_likes_the_request
    try:
        with tempfile.TemporaryDirectory() as d:
            try:
                pose_ops.apply_poses(
                    ESP, os.path.join(d, 'o.kicad_pcb'),
                    [{'kind': 'set', 'ref': 'C3', 'x': float(C4_X),
                      'y': float(C4_Y), 'rot': None}],
                    pcb_data=parse_kicad_pcb(ESP), snap=True,
                    snap_radius=0.0, snap_tries=3, dry_run=True)
            except pose_ops.PoseRefusal as exc:
                s = exc.extra['summary']
                reason = exc.reason
            else:
                raise AssertionError('C3 on C4 was written; expected a refusal')
    finally:
        pose_score.rank_poses = real
    sc = s['snap_census']
    assert sc['candidates_tried'] == 1 and sc['ranked'] == 1, sc
    assert s['nearest_legal'] is None, s['nearest_legal']
    assert 'try it with --snap' not in reason, reason
    assert '0.000 mm away' not in reason, reason
    assert sc['regrade_refused_by'].get('pad_conflicts') == 1, sc
    assert '--snap re-graded 1 candidate(s)' in reason, reason
    assert 'pad_conflicts x1 (' in reason, reason
    print('  PASS: the refused ranked pose is not named; the census is')


def test_a_hole_only_refusal_names_the_pair():
    evidence(TIGARD, 'tigard board')
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'o.kicad_pcb')
        r = check([sys.executable, '-X', 'utf8', POSE, TIGARD, out,
                   '--allow-routed', 'set', 'TP1', '--near', *TP1_NEAR,
                   '--radius', '0.5', '--snap-tries', '4'],
                  refuse='hole_conflicts x4 (H1/TP1)', code=4)
        assert not os.path.exists(out), 'a refusal wrote the board'
    s = _summary(r)
    sc = s['snap_census']
    assert s['nearest_legal'] is None, s['nearest_legal']
    assert sc['regrade_refused_by'] == {'hole_conflicts': 4}, sc
    assert sc['regrade_refused_pairs'] == {'hole_conflicts': {'H1/TP1': 4}}, sc
    # 12 lattice poses within 0.5 mm at 0.25 mm, 4 re-graded.
    assert sc['untried'] == sc['ranked'] + sc['lattice'] - 4 > 0, sc
    text = _refusal(r)
    assert 'try it with --snap' not in text, text
    assert 'raise --snap-tries' in text, text
    print('  PASS: %s' % text[text.index('--snap re-graded'):][:120])


def test_a_snap_is_pointed_at_radius_not_at_snap():
    evidence(ESP, 'esp_prog board')
    with tempfile.TemporaryDirectory() as d:
        r = check([sys.executable, '-X', 'utf8', POSE, ESP,
                   os.path.join(d, 'o.kicad_pcb'), 'set', 'C3', '--near',
                   C4_X, C4_Y, '--radius', '4'],
                  refuse='--snap re-graded', code=4)
    s = _summary(r)
    text = _refusal(r)
    sc = s['snap_census']
    # Before #1209 this call named a ranker pose 5 mm out (beyond --radius 4)
    # and told a call that had snapped to "try it with --snap".
    assert 'try it with --snap' not in text, text
    assert s['nearest_legal'] is None, s['nearest_legal']
    assert sc['regrade_refused_by'], sc
    nb = sc.get('nearest_beyond_radius')
    if nb:
        assert nb['dist_mm'] > 4.0, nb
        assert 'would let the snap try it' in text, text
    print('  PASS: no --snap advice; beyond the bound: %s' % nb)


def test_a_call_that_did_not_snap_still_gets_the_suggestion():
    evidence(ESP, 'esp_prog board')
    with tempfile.TemporaryDirectory() as d:
        r = check([sys.executable, '-X', 'utf8', POSE, ESP,
                   os.path.join(d, 'o.kicad_pcb'), 'set', 'C3', C4_X, C4_Y],
                  refuse='WORSE', code=4)
    s = _summary(r)
    assert s.get('snap_census') is None, s.get('snap_census')
    if s['nearest_legal'] is not None:
        assert 'try it with --snap' in _refusal(r), _refusal(r)
    else:
        assert (s.get('nearest_legal_census') or {}).get('ranked') == 0, s
    print('  PASS: an exact request still gets the ranker suggestion')


def test_a_successful_snap_names_only_the_pose_it_wrote():
    evidence(ESP, 'esp_prog board')
    with tempfile.TemporaryDirectory() as d:
        r = check([sys.executable, '-X', 'utf8', POSE, ESP,
                   os.path.join(d, 'o.kicad_pcb'), 'set', 'C3', '--near',
                   C4_X, C4_Y, '--radius', '8'], accept=True)
    s = _summary(r)
    nl, to = s['nearest_legal'], (s['snapped'] or {}).get('to')
    assert to and nl and [nl['x'], nl['y'], nl['rot']] == to, (nl, to)
    print('  PASS: nearest_legal is the snapped pose %s' % (to,))


def test_regrade_refusal_folds_pairs_and_marks_strict():
    """The attribution one candidate gets: a pair arm folds into its count
    arm (one entry, named by its NEW pairs), a pair-only refusal keeps its
    own key, and a --strict-legal-only refusal is marked as such."""
    from placement.pose_ops import regrade_refusal, worsened
    before = {'hole_conflicts': 0, 'hole_conflict_pairs': [],
              'pad_conflicts': 1, 'pad_conflict_pairs': [['C1', 'C2']]}
    after = {'hole_conflicts': 1, 'hole_conflict_pairs': [['H1', 'TP1']],
             'pad_conflicts': 1, 'pad_conflict_pairs': [['C1', 'C3']]}
    bad = worsened(before, after)
    assert set(bad) == {'hole_conflicts', 'hole_conflict_pairs',
                        'pad_conflict_pairs'}, bad
    got = regrade_refusal(before, after, bad, strict=False)
    assert got == {'hole_conflicts': ['H1/TP1'],
                   'pad_conflict_pairs': ['C1/C3']}, got
    dirty = {'pad_conflicts': 1, 'pad_conflict_pairs': [['C1', 'C2']],
             'pad_edge': {'complete': False}}
    assert regrade_refusal(dirty, dirty, [], strict=False) == {}
    got = regrade_refusal(dirty, dirty, [], strict=True)
    assert got == {'pad_conflicts (--strict-legal)': ['C1/C2'],
                   'pad_edge incomplete (--strict-legal)': []}, got
    print('  PASS: %s' % sorted(got))


TESTS = [
    test_regrade_refusal_folds_pairs_and_marks_strict,
    test_a_refused_ranked_pose_is_not_offered_back,
    test_a_hole_only_refusal_names_the_pair,
    test_a_snap_is_pointed_at_radius_not_at_snap,
    test_a_call_that_did_not_snap_still_gets_the_suggestion,
    test_a_successful_snap_names_only_the_pose_it_wrote,
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
