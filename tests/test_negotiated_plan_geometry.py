#!/usr/bin/env python3
"""Grid geometry of the negotiated plan's conflict model (negotiated_plan.py).

The commit ships a probe's copper as real tracks, so the conflict model must
neither miss a real clearance violation nor flag a legal pair. These tests pin:

  * the open disk (KICAD_GLOBAL_PLAN_NEG_EXACT): a centreline at exactly the
    legal distance is legal, one cell closer is a conflict;
  * the closed ceil disk (default) stays as before;
  * centreline(): a grid path's track cells, and a layer change as a via;
  * copper_centreline(): an off-grid via covers every cell it may round to;
    off-grid track ends do so only in exact mode;
  * exact_rejects(): the commit's real-geometry check, track-track at the
    pair's clearance, and via-via at the drill hole-to-hole spacing.
"""
import os
import sys

_TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_TESTS)
for p in (ROOT, _TESTS, os.path.join(ROOT, 'py_router')):
    if p not in sys.path:
        sys.path.insert(0, p)

from kicad_parser import Segment, Via  # noqa: E402
from negotiated_plan import (_disk, _open_disk, centreline, copper_centreline,  # noqa: E402
                             exact_rejects, seg_seg_dist)


class _Cfg:
    grid_step = 0.1
    layers = ['F.Cu', 'B.Cu']


def _cells(arr):
    return {tuple(int(v) for v in row) for row in arr.tolist()}


def test_open_disk_keeps_the_legal_distance():
    # 0.2 mm tracks at 0.2 mm clearance: centrelines 0.4 mm = 4 cells apart are legal
    d = _cells(_open_disk(4.0))
    assert (4, 0) not in d and (0, -4) not in d
    assert (3, 0) in d and (3, 2) in d          # 3.6 cells: a real violation
    assert (3, 3) not in d                      # 4.24 cells: legal


def test_closed_disk_is_unchanged():
    d = _cells(_disk(4))
    assert (4, 0) in d and (3, 3) not in d


def test_centreline_tracks_and_vias():
    tr, vi = centreline([(0, 0, 0), (3, 0, 0), (3, 0, 1), (3, 2, 1)], 2)
    assert _cells(tr) >= {(0, 0, 0), (0, 3, 0), (1, 3, 0), (1, 3, 2)}
    assert _cells(vi) == {(3, 0)}


def test_off_grid_via_covers_both_roundings():
    res = {'new_segments': [], 'new_vias': [Via(x=0.3, y=8.095, size=0.6, drill=0.3,
                                               layers=['F.Cu', 'B.Cu'], net_id=1)]}
    _tr, vi = copper_centreline(res, _Cfg())
    assert _cells(vi) == {(3, 80), (3, 81)}


def test_off_grid_track_ends_only_in_exact_mode():
    seg = Segment(start_x=0.0, start_y=0.25, end_x=0.5, end_y=0.25,
                  width=0.2, layer='F.Cu', net_id=1)
    res = {'new_segments': [seg], 'new_vias': []}
    rounded = {c[2] for c in _cells(copper_centreline(res, _Cfg())[0])}
    exact = {c[2] for c in _cells(copper_centreline(res, _Cfg(), exact=True)[0])}
    assert len(rounded) == 1
    assert exact == {2, 3}


def test_seg_seg_dist():
    assert abs(seg_seg_dist((0, 0, 1, 0), (0, 0.4, 1, 0.4)) - 0.4) < 1e-9
    assert seg_seg_dist((0, 0, 1, 1), (0, 1, 1, 0)) == 0
    assert abs(seg_seg_dist((0, 0, 1, 0), (2, 0, 3, 0)) - 1) < 1e-9


def _res(segs=(), vias=()):
    return {'new_segments': list(segs), 'new_vias': list(vias)}


def test_exact_rejects_tracks_by_pair_clearance():
    a = Segment(start_x=0, start_y=0, end_x=5, end_y=0, width=0.2, layer='F.Cu', net_id=1)
    b = Segment(start_x=0, start_y=0.4, end_x=5, end_y=0.4, width=0.2, layer='F.Cu', net_id=2)
    c = Segment(start_x=0, start_y=0.35, end_x=5, end_y=0.35, width=0.2, layer='F.Cu', net_id=3)
    size = {1: (0.2, 0.6, 0.3, 0.2), 2: (0.2, 0.6, 0.3, 0.2), 3: (0.2, 0.6, 0.3, 0.2)}
    res = {1: _res([a]), 2: _res([b]), 3: _res([c])}
    assert exact_rejects([1, 2], res, size, 0.25) == set()        # exactly 0.4: legal
    assert exact_rejects([1, 3], res, size, 0.25) == {3}          # 0.35: violation
    wide = {**size, 2: (0.2, 0.6, 0.3, 0.25)}                    # class clearance 0.25
    assert exact_rejects([1, 2], res, wide, 0.25) == {2}


def test_exact_rejects_pairwise_rule():
    # a .kicad_dru class-pair rule (0.75 mm between nets 1 and 2) that the
    # class clearances (0.2) do not carry
    a = Segment(start_x=0, start_y=0, end_x=5, end_y=0, width=0.2, layer='F.Cu', net_id=1)
    b = Segment(start_x=0, start_y=0.6, end_x=5, end_y=0.6, width=0.2, layer='F.Cu', net_id=2)
    size = {1: (0.2, 0.6, 0.3, 0.2), 2: (0.2, 0.6, 0.3, 0.2)}
    res = {1: _res([a]), 2: _res([b])}
    rule = lambda x, y, kind, layer: 0.75 if {x, y} == {1, 2} else 0.2
    assert exact_rejects([1, 2], res, size, 0.25) == set()        # 0.4 >= 0.2
    assert exact_rejects([1, 2], res, size, 0.25, pair_cl=rule, pair_cl_max=0.75) == {2}
    far = {1: _res([a]), 2: _res([Segment(start_x=0, start_y=0.95, end_x=5, end_y=0.95,
                                          width=0.2, layer='F.Cu', net_id=2)])}
    assert exact_rejects([1, 2], far, size, 0.25, pair_cl=rule, pair_cl_max=0.75) == set()  # 0.75 exactly


def test_exact_rejects_pair_rule_per_geometry():
    # a track-only 0.75 mm rule: two vias 0.7 mm apart edge to edge are
    # graded at the via-stack value (0.2) and pass; two tracks at 0.6 fail
    tr = lambda x, y, kind, layer: 0.75 if kind == 'track' else 0.2
    v1, v2 = Via(x=0, y=0, size=0.6, drill=0.3, layers=['F.Cu', 'B.Cu'], net_id=1), \
        Via(x=1.3, y=0, size=0.6, drill=0.3, layers=['F.Cu', 'B.Cu'], net_id=2)
    size = {1: (0.2, 0.6, 0.3, 0.2), 2: (0.2, 0.6, 0.3, 0.2)}
    assert exact_rejects([1, 2], {1: _res(vias=[v1]), 2: _res(vias=[v2])}, size, 0.25,
                         pair_cl=tr, pair_cl_max=0.75) == set()
    a = Segment(start_x=0, start_y=0, end_x=5, end_y=0, width=0.2, layer='F.Cu', net_id=1)
    b = Segment(start_x=0, start_y=0.8, end_x=5, end_y=0.8, width=0.2, layer='F.Cu', net_id=2)
    assert exact_rejects([1, 2], {1: _res([a]), 2: _res([b])}, size, 0.25,
                         pair_cl=tr, pair_cl_max=0.75) == {2}
    # a rule on F.Cu only does not reach a pair of B.Cu tracks
    fl = lambda x, y, kind, layer: 0.75 if layer == 'F.Cu' else 0.2
    a2 = Segment(start_x=0, start_y=0, end_x=5, end_y=0, width=0.2, layer='B.Cu', net_id=1)
    b2 = Segment(start_x=0, start_y=0.8, end_x=5, end_y=0.8, width=0.2, layer='B.Cu', net_id=2)
    assert exact_rejects([1, 2], {1: _res([a2]), 2: _res([b2])}, size, 0.25,
                         pair_cl=fl, pair_cl_max=0.75) == set()


def test_exact_rejects_via_hole_to_hole():
    v1 = Via(x=0, y=0, size=0.4, drill=0.3, layers=['F.Cu', 'B.Cu'], net_id=1)
    v2 = Via(x=0.65, y=0, size=0.4, drill=0.3, layers=['F.Cu', 'B.Cu'], net_id=2)
    size = {1: (0.2, 0.4, 0.3, 0.2), 2: (0.2, 0.4, 0.3, 0.2)}
    res = {1: _res(vias=[v1]), 2: _res(vias=[v2])}
    assert exact_rejects([1, 2], res, size, 0.0) == set()         # copper 0.6 <= 0.65
    assert exact_rejects([1, 2], res, size, 0.4) == {2}           # drills need 0.7


def test_exact_rejects_copper_to_hole():
    v = Via(x=0, y=0, size=0.4, drill=0.3, layers=['F.Cu', 'B.Cu'], net_id=1)
    t = Segment(start_x=-2, start_y=0.5, end_x=2, end_y=0.5, width=0.2, layer='F.Cu', net_id=2)
    size = {1: (0.2, 0.4, 0.3, 0.2), 2: (0.2, 0.4, 0.3, 0.2)}
    res = {1: _res(vias=[v]), 2: _res([t])}
    assert exact_rejects([1, 2], res, size, 0.25) == set()               # copper 0.2 clear
    assert exact_rejects([1, 2], res, size, 0.25, hole_cl=0.3) == {2}    # drill edge 0.25 < 0.3


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-q']))
