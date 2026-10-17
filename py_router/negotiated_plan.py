"""Negotiated-congestion re-probing for the global plan (#589 "PathFinder v2").

The global plan probes every net once against a map without the other nets
being routed (blind), or once in sequence with each earlier probe stamped as a
soft ghost (SEQ). Either way the result depends on probe order, and the
detailed router then routes nets one at a time, so the order decides which net
gets walled in. This pass iterates instead: every net is re-probed each round
against the CURRENT rough paths of all other nets plus a history of past
conflicts, PathFinder-style, until the rough paths no longer overlap (or the
round budget runs out). The negotiated paths then replace the plan's rough
paths, so the plan's existing consumers (ordering, corridor reservations,
owner attraction) carry them into detailed routing.

Conflict model, on the routing grid, per copper layer:
  - a net's CENTRELINE cells: its emitted copper rasterised (walk_line; the
    grid path when a probe has no copper), and its vias on every layer;
  - nets fall into size classes (track width, via diameter, via drill,
    clearance, all from the net class); for each class of PROBING net the
    PRESENT field counts, per cell, the OTHER nets whose conflict zone
    covers it -- the pairwise track-track, track-via and via-via distances
    at max(cl_a, cl_b), via-via also at drill_a/2 + drill_b/2 +
    hole-to-hole; track cells read the track field, via cells the via field;
  - the HISTORY field accumulates on cells where a conflict was seen at the
    end of a round.
Entering a cell costs  history + present_factor * present  (Rust cost units,
ORTHO step = 1000), loaded into the probe map's layer-proximity field, which
the probe map otherwise leaves empty. The present factor grows each round;
the round with the fewest conflicted nets is kept.

The rasterisation only guides the negotiation. A commit (route.py,
KICAD_GLOBAL_PLAN_COMMIT) ships only nets whose probe saw every other net's
pads (legal maps) and whose copper `exact_rejects` clears against every
other committed net on the real geometry.

Env knobs (env_knobs.GLOBAL_PLAN): negotiate (rounds; 0 = off), neg_pres
(first-round present factor), neg_growth (per-round multiplier),
neg_pres_max (cap, 0 = none), neg_hist (history increment per conflicted
round), neg_hweight / neg_iters (probe A* weight and iteration cap),
neg_legal (per-net probe maps with every other net's pads, required for a
commit), neg_tree (3+ terminal nets negotiate as main edge + taps, with a
one-pad-pair fallback), neg_exact (open disks at the true distance, off-grid
copper covered from every rounding), neg_dump (JSON of the kept paths and
conflict cells).
"""
from __future__ import annotations

import itertools
import copy
import math
import time
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

import numpy as np

from bresenham_utils import walk_line


def _disk(r: int) -> np.ndarray:
    """Integer (dx, dy) offsets within radius r (cells)."""
    rng = np.arange(-r, r + 1)
    dx, dy = np.meshgrid(rng, rng)
    keep = dx * dx + dy * dy <= r * r
    return np.stack([dx[keep], dy[keep]], axis=1)


def _open_disk(rho: float) -> np.ndarray:
    """Integer (dx, dy) offsets strictly closer than rho cells: a centreline
    at exactly the legal distance is legal (KiCad: actual >= clearance)."""
    r = int(math.ceil(rho))
    rng = np.arange(-r, r + 1)
    dx, dy = np.meshgrid(rng, rng)
    keep = dx * dx + dy * dy < rho * rho - 1e-6
    return np.stack([dx[keep], dy[keep]], axis=1)


class _Field:
    """Per-layer cell grids over a fixed window of the board."""

    def __init__(self, x0: int, y0: int, w: int, h: int, layers: int):
        self.x0, self.y0, self.w, self.h, self.L = x0, y0, w, h, layers

    def zeros(self, dtype=np.int32) -> np.ndarray:
        return np.zeros((self.L, self.h, self.w), dtype=dtype)

    def flat(self, layer: np.ndarray, gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
        """Flat indices of in-window cells (out-of-window cells dropped)."""
        x, y = gx - self.x0, gy - self.y0
        ok = (x >= 0) & (x < self.w) & (y >= 0) & (y < self.h)
        return (layer[ok] * self.h + y[ok]) * self.w + x[ok]


def centreline(path: List[Tuple[int, int, int]], layers: int):
    """(track cells [N,3] as layer,gx,gy ; via cells [M,2] as gx,gy)."""
    cells, vias = [], []
    for (x1, y1, l1), (x2, y2, l2) in zip(path, path[1:]):
        if l1 != l2:
            vias.append((x1, y1))
            continue
        for gx, gy in walk_line(x1, y1, x2, y2):
            cells.append((l1, gx, gy))
    if len(path) == 1:
        cells.append((path[0][2], path[0][0], path[0][1]))
    tr = np.array(cells, dtype=np.int64).reshape(-1, 3)
    vi = np.array(vias, dtype=np.int64).reshape(-1, 2)
    return tr, vi


def copper_centreline(res: dict, config, exact: bool = False):
    """Centreline cells of a probe's COPPER (new_segments / new_vias), as
    ``centreline`` gives for a grid path. A tree probe (main edge + taps) has
    no single path, and its copper is what a commit ships."""
    inv = 1.0 / config.grid_step
    li = {name: i for i, name in enumerate(config.layers)}

    def near(v: float):
        """Grid indices v may round to. A via off the grid (a tap landing at
        a half-cell pad centre) is covered from both sides, so its conflict
        disk holds from its TRUE centre: rounded to one cell, a 0.8 mm VDDA
        via shipped 0.17 mm from a track. Tracks stay rounded -- covering
        both sides of every half-cell escape stub made legal 0.5 mm-pitch
        neighbours conflict (47 conflicted nets instead of 6-9)."""
        g = v * inv
        lo, hi = math.floor(g + 1e-6), math.ceil(g - 1e-6)
        return (lo,) if lo == hi else (lo, hi)

    cells = []
    for sg in res.get('new_segments') or ():
        lay = li.get(getattr(sg, 'layer', None))
        if lay is None:
            continue
        if exact:
            # every rounding of an off-grid end (a stub to a half-cell pad)
            ends = itertools.product(near(sg.start_x), near(sg.start_y),
                                     near(sg.end_x), near(sg.end_y))
        else:
            ends = [(round(sg.start_x * inv), round(sg.start_y * inv),
                     round(sg.end_x * inv), round(sg.end_y * inv))]
        for x1, y1, x2, y2 in ends:
            for gx, gy in walk_line(x1, y1, x2, y2):
                cells.append((lay, gx, gy))
    vias = [(gx, gy) for v in res.get('new_vias') or ()
            for gx in near(v.x) for gy in near(v.y)]
    tr = np.array(cells, dtype=np.int64).reshape(-1, 3)
    vi = np.array(vias, dtype=np.int64).reshape(-1, 2)
    return tr, vi


def tree_route_fn(pcb_data, route_one):
    """Probe function for tree negotiation: a net of 3+ terminals is probed
    as the detailed router's rip-up loop re-routes it -- the main edge
    (route_multipoint_main) and then every tap (route_multipoint_taps) -- on
    the same map, so all of its connections negotiate, not only the one pad
    pair route_net_with_obstacles picks. Other nets go to ``route_one``."""
    from connectivity import get_multipoint_net_pads
    from single_ended_routing import (route_multipoint_main, route_multipoint_taps,
                                      _per_net_via_config)

    def fn(cfg, nid, obs):
        pads = get_multipoint_net_pads(pcb_data, nid, cfg)
        if not pads:
            return route_one(cfg, nid, obs)
        cfg = _per_net_via_config(cfg, pcb_data, nid)
        res = route_multipoint_main(pcb_data, nid, cfg, obs, pads)
        if not res or res.get('failed') or not res.get('is_multipoint'):
            return res
        tap = route_multipoint_taps(pcb_data, nid, cfg, obs, res)
        return tap or res
    return fn


def zone(field: _Field, tr: np.ndarray, vi: np.ndarray,
         track_disk: np.ndarray, via_disk: np.ndarray) -> np.ndarray:
    """Unique flat indices of a net's conflict zone."""
    parts = []
    if len(tr):
        lay = np.repeat(tr[:, 0], len(track_disk))
        gx = (tr[:, 1:2] + track_disk[None, :, 0]).ravel()
        gy = (tr[:, 2:3] + track_disk[None, :, 1]).ravel()
        parts.append(field.flat(lay, gx, gy))
    if len(vi):
        for layer in range(field.L):
            gx = (vi[:, 0:1] + via_disk[None, :, 0]).ravel()
            gy = (vi[:, 1:2] + via_disk[None, :, 1]).ravel()
            parts.append(field.flat(np.full(gx.shape, layer), gx, gy))
    if not parts:
        return np.empty(0, dtype=np.int64)
    return np.unique(np.concatenate(parts))


def own_cells(field: _Field, tr: np.ndarray, vi: np.ndarray) -> np.ndarray:
    parts = []
    if len(tr):
        parts.append(field.flat(tr[:, 0], tr[:, 1], tr[:, 2]))
    for layer in range(field.L):
        if len(vi):
            parts.append(field.flat(np.full(len(vi), layer), vi[:, 0], vi[:, 1]))
    return np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int64)


def legal_map_builder(probe_map, pcb_data, config, ids: List[int]):
    """Per-net probe maps that see every OTHER routed net's pads and stubs,
    as the detailed router's maps do (routing_context.build_single_ended_
    obstacles: clone the base, add the other nets from the obstacle cache,
    lift the net's own graphic pad rows). The plan's shared probe map leaves
    all routed nets' pads out by design, so its rough paths may cross foreign
    pads -- fine for a rough plan, not for copper a commit would ship."""
    from obstacle_cache import precompute_all_net_obstacles, add_net_obstacles_from_cache
    import routing_defaults as defaults
    cache = precompute_all_net_obstacles(pcb_data, list(ids), config, extra_clearance=0.0,
                                         diagonal_margin=defaults.DIAGONAL_MARGIN)

    def build(nid: int):
        obs = probe_map.clone_fresh()
        # per-route terminal / unblock allowances of the rough probes (BGA
        # exclusion holes) belong to other nets: start clean, as detailed
        # routing does between routes
        if hasattr(obs, 'clear_allowed_cells'):
            obs.clear_allowed_cells()
        lift = (getattr(pcb_data, '_graphic_own_pad_lift', None) or {}).get(nid)
        lift_v = (getattr(pcb_data, '_graphic_own_pad_via_lift', None) or {}).get(nid)
        for other in ids:
            if other != nid and other in cache:
                add_net_obstacles_from_cache(obs, cache[other])
        if lift is not None and len(lift):
            obs.remove_blocked_cell_spans_batch(lift)
        if lift_v is not None and len(lift_v):
            obs.remove_blocked_via_spans_batch(lift_v)
        return obs
    return build


def _pt_seg(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L))
    return math.hypot(px - x1 - t * dx, py - y1 - t * dy)


def seg_seg_dist(a, b) -> float:
    """Distance between 2D segments a, b = (x1, y1, x2, y2)."""
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    p1, p2, q1, q2 = a[:2], a[2:], b[:2], b[2:]
    if (cross(p1, p2, q1) * cross(p1, p2, q2) < 0
            and cross(q1, q2, p1) * cross(q1, q2, p2) < 0):
        return 0.0
    return min(_pt_seg(*p1, *b), _pt_seg(*p2, *b), _pt_seg(*q1, *a), _pt_seg(*q2, *a))


def exact_rejects(cand: List[int], results: Dict[int, dict],
                  net_size: Dict[int, tuple], h2h: float,
                  hole_cl: float = 0.0, pair_cl=None,
                  pair_cl_max: float = 0.0) -> set:
    """Nets of ``cand`` (in order) whose probe copper violates clearance to a
    net accepted before them, on the real geometry: track-track on a shared
    layer, track-via (also copper to the via's drill at ``hole_cl``, KiCad's
    min_hole_clearance), via-via (also drill hole-to-hole), each pair at
    max(cl_a, cl_b). ``net_size[n]`` = (track width, via diameter, via
    drill, clearance); the copper's own widths and sizes are used.
    ``pair_cl(a, b, kind, layer)``, when given, replaces max(cl_a, cl_b)
    with the board's pairwise value for each geometry, as check_drc grades
    it: kind 'track' on the shared layer for track-track, 'layer' on the
    track's layer for track-via, 'stack' for via-via (e.g. a .kicad_dru
    net-class pair rule, which the class clearances do not carry);
    ``pair_cl_max`` bounds it for the prefilter."""
    def copper(n):
        r = results.get(n) or {}
        segs = [(sg.layer, (sg.start_x, sg.start_y, sg.end_x, sg.end_y), sg.width)
                for sg in r.get('new_segments') or ()]
        vias = [(v.x, v.y, v.size, getattr(v, 'drill', 0.0) or 0.0)
                for v in r.get('new_vias') or ()]
        return segs, vias

    def box(segs, vias, pad):
        xs = [c for _l, g, _w in segs for c in (g[0], g[2])] + [v[0] for v in vias]
        ys = [c for _l, g, _w in segs for c in (g[1], g[3])] + [v[1] for v in vias]
        if not xs:
            return None
        return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

    def overlap(a, b):
        return a and b and not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])

    def clash(ca, cb, cl):
        """``cl(kind, layer)``: the pair's clearance for that geometry."""
        (sa, va), (sb, vb) = ca, cb
        for la, ga, wa in sa:
            for lb, gb, wb in sb:
                if la == lb and seg_seg_dist(ga, gb) < wa / 2 + wb / 2 + cl('track', la) - 1e-6:
                    return True
            for x, y, d, dr in vb:
                if _pt_seg(x, y, *ga) < max(wa / 2 + d / 2 + cl('layer', la),
                                            wa / 2 + dr / 2 + hole_cl) - 1e-6:
                    return True
        for x, y, d, dr in va:
            for lb, gb, wb in sb:
                if _pt_seg(x, y, *gb) < max(d / 2 + wb / 2 + cl('layer', lb),
                                            dr / 2 + wb / 2 + hole_cl) - 1e-6:
                    return True
            for x2, y2, d2, dr2 in vb:
                need = max(d / 2 + d2 / 2 + cl('stack', None), dr / 2 + dr2 / 2 + h2h)
                if math.hypot(x - x2, y - y2) < need - 1e-6:
                    return True
        return False

    accepted, rejected = [], set()
    info = {n: copper(n) for n in cand}
    # Boxes padded so any two items that could clash overlap: the largest
    # half-size plus half the largest pair clearance, or the drill analogue.
    half = max([w / 2 for segs, _v in info.values() for _l, _g, w in segs]
               + [d / 2 for _s, vias in info.values() for _x, _y, d, _dr in vias] + [0.0])
    drill = max([dr for _s, vias in info.values() for _x, _y, _d, dr in vias] + [0.0])
    cl_max = max([(net_size.get(n) or (0, 0, 0, 0.0))[3] for n in cand] + [pair_cl_max, 0.0])
    pad = max(half + cl_max / 2, drill / 2 + h2h / 2,
              (half + drill / 2 + hole_cl) / 2) + 1e-3
    info = {n: (cu, box(*cu, pad)) for n, cu in info.items()}
    for n in cand:
        cu, bx = info[n]
        cl_n = (net_size.get(n) or (0, 0, 0, 0.0))[3]
        bad = False
        for m in accepted:
            cm, bm = info[m]
            if not overlap(bx, bm):
                continue
            if pair_cl is None:
                flat = max(cl_n, (net_size.get(m) or (0, 0, 0, 0.0))[3])
                cl = lambda _kind, _layer, _v=flat: _v
            else:
                memo = {}

                def cl(kind, layer, _m=m, _memo=memo):
                    if (kind, layer) not in _memo:
                        _memo[kind, layer] = pair_cl(n, _m, kind, layer)
                    return _memo[kind, layer]
            if clash(cu, cm, cl):
                bad = True
                break
        if bad:
            rejected.add(n)
        else:
            accepted.append(n)
    return rejected


def negotiate(plan, results: Dict[int, dict], order: List[int], probe_map,
              rough_cfg, config, pcb_data, k: dict, route_fn, build_map=None,
              pair_fn=None) -> dict:
    """Run the negotiation rounds in place on ``plan.rough_paths`` / ``results``.

    ``route_fn(cfg, nid, obstacles)`` probes one net (the caller's
    route_net_with_obstacles binding) on ``build_map(nid)`` when given, else
    on the shared ``probe_map``. ``pair_fn`` (tree mode): the one-pad-pair
    probe, tried on nets still conflicted after the kept round. Returns a
    stats dict."""
    from rip_up_reroute import RUN_LEDGERS
    t0 = time.time()
    layers = len(config.layers)
    step = config.grid_step
    bb = getattr(getattr(pcb_data, 'board_info', None), 'board_bounds', None)
    if bb:
        x0, y0 = int(math.floor(bb[0] / step)) - 2, int(math.floor(bb[1] / step)) - 2
        x1, y1 = int(math.ceil(bb[2] / step)) + 2, int(math.ceil(bb[3] / step)) + 2
    else:
        pts = [p for path in plan.rough_paths.values() for p in path]
        if not pts:
            return {'rounds': 0}
        x0 = min(p[0] for p in pts) - 50
        y0 = min(p[1] for p in pts) - 50
        x1 = max(p[0] for p in pts) + 50
        y1 = max(p[1] for p in pts) + 50
    field = _Field(x0, y0, x1 - x0 + 1, y1 - y0 + 1, layers)
    w, cl = config.track_width, config.clearance
    via_d = getattr(config, 'via_size', 0.6)
    via_dr = getattr(config, 'via_drill', 0.3)
    h2h = float(getattr(config, 'hole_to_hole_clearance', 0.0) or 0.0)

    def net_cl(nid) -> float:
        """The net's own class clearance (KiCad takes the larger of a pair's)."""
        f = getattr(config, 'obstacle_clearance', None)
        if f is not None:
            try:
                return float(f(nid))
            except Exception:                                  # noqa: BLE001
                pass
        return float((getattr(config, 'net_clearances', None) or {}).get(nid, cl))

    def sizes(nid, res) -> Tuple[float, float, float, float]:
        """A net's track width, via diameter, via drill and clearance: its net
        class's (power classes route wider tracks and bigger vias than the
        default), at least what its probe copper used. A probe with no via
        says nothing about the via a later re-probe may drop, so the class
        must decide."""
        wl = []
        for name in config.layers:
            try:
                wl.append(float(config.get_net_track_width(nid, name)))
            except Exception:                                  # noqa: BLE001
                pass
        vsz = (getattr(config, 'net_via_sizes', None) or {}).get(nid)
        ws = [getattr(sg, 'width', None) for sg in (res or {}).get('new_segments') or ()]
        vs = [getattr(v, 'size', None) for v in (res or {}).get('new_vias') or ()]
        tw = max([x for x in wl + ws if x] or [w])
        vd = max([x for x in ([float(vsz[0])] if vsz else []) + vs if x] or [via_d])
        dr = max([x for x in ([float(vsz[1])] if vsz and len(vsz) > 1 else [])
                  + [getattr(v, 'drill', None) for v in (res or {}).get('new_vias') or ()]
                  if x] or [via_dr])
        return tw, vd, dr, net_cl(nid)

    net_size = {nid: sizes(nid, results.get(nid)) for nid in plan.rough_paths}
    # Size classes (track width, via diameter, via drill, clearance). Two nets
    # conflict when their centrelines come closer than w_a/2 + w_b/2 +
    # max(cl_a, cl_b) (track-track), or the via analogues (via-via also at
    # least drill_a/2 + drill_b/2 + hole-to-hole) -- a pairwise distance, so
    # each class of PROBING net reads its own present field, built from every
    # net's zones dilated for that class. Sizing every neighbour at the
    # largest class instead made legal 0.2 mm tracks at 0.4 mm pitch
    # conflict, and the plan never converged.
    default_size = (w, via_d, via_dr, cl)

    def key(t):
        return tuple(round(x, 4) for x in t)

    classes = sorted({key(t) for t in net_size.values()} | {key(default_size)})
    net_cls = {nid: classes.index(key(t)) for nid, t in net_size.items()}
    _disks: Dict[int, np.ndarray] = {}

    exact = bool(k.get('neg_exact'))

    def disk_mm(r_mm: float) -> np.ndarray:
        if exact:
            # Exact on the grid: centrelines are cells (octilinear paths) and
            # off-grid copper covers every cell it may round to, so the open
            # disk needs no rounding slack. The closed ceil disk below adds
            # up to a cell: 0.2/0.2 tracks then need 0.5 mm, not 0.4 mm.
            rho = round(r_mm / step, 6)
            if rho not in _disks:
                _disks[rho] = _open_disk(rho)
            return _disks[rho]
        r = max(1, int(math.ceil(r_mm / step - 1e-9)))
        if r not in _disks:
            _disks[r] = _disk(r)
        return _disks[r]

    # Legal probes may be committed as real copper, so they keep the
    # detailed router's terminal-short verdict (#157) that plan probes skip.
    neg_cfg = replace(rough_cfg, heuristic_weight=k['neg_hweight'],
                      max_iterations=max(1, int(k['neg_iters'])),
                      plan_probe=rough_cfg.plan_probe and build_map is None)
    # A via move pays the cell's cost x via_proximity_cost, a track move pays
    # it once. A via needs other centrelines farther away than a track does,
    # so cells in between are legal for tracks and not for vias: they carry
    # present/via_mult, which a via then pays in full.
    via_mult = max(1, int(getattr(config, 'via_proximity_cost', 10) or 1))
    C = len(classes)
    zones = [dict() for _ in range(C)]   # per probing class: other-track conflict zone
    rings = [dict() for _ in range(C)]   # per probing class: other-via conflict zone
    owns: Dict[int, np.ndarray] = {}     # track centreline cells
    owns_v: Dict[int, np.ndarray] = {}   # via centre cells (every layer)
    present = [field.zeros() for _ in range(C)]
    present_v = [field.zeros() for _ in range(C)]
    history = field.zeros()

    def load(nid: int, sign: int = 1) -> None:
        for c in range(C):
            z = zones[c].get(nid)
            if z is not None:
                np.add.at(present[c].reshape(-1), z, sign)
                np.add.at(present_v[c].reshape(-1), rings[c][nid], sign)

    def unload(nid: int) -> None:
        load(nid, -1)

    def set_path(nid: int, path, res: Optional[dict] = None) -> None:
        # The emitted copper, not just the grid path: a commit also ships the
        # off-grid terminal bridges and via-unblock pieces the path leaves out.
        if res and (res.get('new_segments') or res.get('new_vias')):
            tr, vi = copper_centreline(res, config, exact)
        else:
            tr, vi = centreline(path, layers)
        wn, vn, dn, cln = net_size.get(nid, default_size)
        for c, (wc, vc, dc, clc) in enumerate(classes):
            cp = max(cln, clc)
            # where a class-c track centreline / via centre conflicts with this net
            zones[c][nid] = zone(field, tr, vi, disk_mm(wn / 2 + cp + wc / 2),
                                 disk_mm(vn / 2 + cp + wc / 2))
            rings[c][nid] = zone(field, tr, vi, disk_mm(wn / 2 + cp + vc / 2),
                                 disk_mm(max(vn / 2 + cp + vc / 2, dn / 2 + dc / 2 + h2h)))
        owns[nid] = own_cells(field, tr, vi[:0])
        owns_v[nid] = own_cells(field, tr[:0], vi)

    for nid, path in plan.rough_paths.items():
        set_path(nid, path, results.get(nid))
        load(nid)

    def others_at(nid: int) -> Tuple[np.ndarray, np.ndarray]:
        """(own cells, how many OTHER nets' zones cover each) in nid's class:
        track cells read the track field, via cells the via field."""
        c = net_cls.get(nid, 0)
        own_t, own_v = owns[nid], owns_v[nid]
        mine_t = np.isin(own_t, zones[c][nid], assume_unique=True).astype(np.int32)
        mine_v = np.isin(own_v, rings[c][nid], assume_unique=True).astype(np.int32)
        return (np.concatenate([own_t, own_v]),
                np.concatenate([present[c].reshape(-1)[own_t] - mine_t,
                                present_v[c].reshape(-1)[own_v] - mine_v]))

    def conflicted() -> List[int]:
        out = []
        for nid in owns:
            own, oth = others_at(nid)
            if len(own) and (oth > 0).any():
                out.append(nid)
        return out

    legal = set()  # nets whose current result came from a per-net (legal) map
    stats = {'rounds': 0, 'conflicted': [], 'failed': 0, 'iterations': 0,
             'failed_per_round': []}
    def probe(nid: int, fn, pres: float):
        """Probe ``nid`` (unloaded) with ``fn`` against the current fields."""
        c = net_cls.get(nid, 0)
        cost = (history.astype(np.int64) + (pres * present[c]).astype(np.int64)
                + (pres / via_mult * (present_v[c] - present[c])).astype(np.int64))
        nz = np.flatnonzero(cost > 0)
        obs = build_map(nid) if build_map else probe_map
        obs.clear_layer_proximity()
        if len(nz):
            lay, rem = np.divmod(nz, field.h * field.w)
            gy, gx = np.divmod(rem, field.w)
            rows = np.stack([lay, gx + field.x0, gy + field.y0,
                             np.minimum(cost.reshape(-1)[nz], 2**31 - 1)],
                            axis=1).astype(np.int32)
            obs.set_layer_proximity_batch(rows)
        # Legal probes run with plan_probe off (for the terminal-short gate),
        # which lets the via-unblock / rescue paths append copper to pcb_data
        # directly; a probe is a proposal, so put the board model back.
        # The run ledgers (shrunk-via sizes, unblock blame, ...) are restored
        # with the copper.
        segs0, vias0 = list(pcb_data.segments), list(pcb_data.vias)
        led0 = {a: copy.copy(vars(pcb_data)[a]) for a in RUN_LEDGERS if a in vars(pcb_data)}
        try:
            res = fn(neg_cfg, nid, obs)
        finally:
            pcb_data.segments[:] = segs0
            pcb_data.vias[:] = vias0
            for a in RUN_LEDGERS:
                if a in led0:
                    setattr(pcb_data, a, led0[a])
                elif a in vars(pcb_data):
                    delattr(pcb_data, a)
        stats['iterations'] += int((res or {}).get('iterations', 0) or 0)
        return res

    pres_f = float(k['neg_pres'])
    stats['conflicted'].append(len(conflicted()))
    # Keep the round with the fewest conflicted nets: on a board with no
    # overlap-free plan the costs keep growing until re-probes hit their
    # iteration cap and fail, and late rounds get worse again.
    # With legal maps the relaxed round-0 snapshot holds nothing committable,
    # so it is never kept over a legal round.
    best = (math.inf if build_map else stats['conflicted'][0], dict(plan.rough_paths),
            dict(results), 0, set(legal))
    for rnd in range(int(k['negotiate'])):
        # With legal maps, round 0 holds only relaxed probes, which a commit
        # must not ship: probe every net legally at least once.
        if stats['conflicted'][-1] == 0 and (rnd > 0 or not build_map):
            break
        failed0 = stats['failed']
        for nid in order:
            unload(nid)
            res = probe(nid, route_fn, pres_f)
            if res and not res.get('failed') and res.get('path'):
                if build_map:
                    legal.add(nid)
                results[nid] = res
                plan.rough_paths[nid] = res['path']
                set_path(nid, res['path'], res)
            else:
                stats['failed'] += 1  # keep the previous path
            load(nid)
        probe_map.clear_layer_proximity()
        bad = conflicted()
        stats['conflicted'].append(len(bad))
        stats['failed_per_round'].append(stats['failed'] - failed0)
        stats['rounds'] = rnd + 1
        # history: every cell of a conflicted net that another net's zone covers
        flat_h = history.reshape(-1)
        for nid in bad:
            own, oth = others_at(nid)
            flat_h[own[oth > 0]] += int(k['neg_hist'])
        pres_f *= float(k['neg_growth'])
        if k.get('neg_pres_max'):
            # Uncapped growth makes the cost field dwarf the A* heuristic
            # (ORTHO step 1000 vs 2000 * 1.3^20 per cell): probes flood and
            # hit the iteration cap, a failed probe keeps its old path, and
            # the negotiation stalls with the same nets conflicted.
            pres_f = min(pres_f, float(k['neg_pres_max']))
        if (len(bad), -len(legal)) < (best[0], -len(best[4])):
            best = (len(bad), dict(plan.rough_paths), dict(results), rnd + 1, set(legal))
    plan.rough_paths.clear()
    plan.rough_paths.update(best[1])
    results.clear()
    results.update(best[2])
    stats['best_round'], stats['best_conflicted'] = best[3], best[0]
    # What a commit can ship: the kept round's probe results and which nets
    # still conflict in it (recomputed on the kept paths).
    for c in range(C):
        present[c][:] = 0
        present_v[c][:] = 0
    for nid, path in plan.rough_paths.items():
        set_path(nid, path, results.get(nid))
        load(nid)
    legal = set(best[4])
    if pair_fn is not None and build_map:
        # A tree that stays conflicted ships nothing; its main pad pair alone
        # may fit (USB-C: the pair to the MCU fits, the A6-B6 tap under the
        # connector does not). Re-probe each such net as one pad pair against
        # the kept field and keep it when it conflicts with no other net --
        # what a commit shipped before trees -- so the router only adds taps.
        stats['pair_fallback'] = []
        for nid in sorted(conflicted()):
            unload(nid)
            old = (plan.rough_paths[nid], results.get(nid))
            res = probe(nid, pair_fn, pres_f)
            if res and not res.get('failed') and res.get('path'):
                set_path(nid, res['path'], res)
                if not (others_at(nid)[1] > 0).any():
                    results[nid] = res
                    plan.rough_paths[nid] = res['path']
                    legal.add(nid)
                    stats['pair_fallback'].append(nid)
                else:
                    set_path(nid, *old)
            load(nid)
        probe_map.clear_layer_proximity()
    plan.negotiated_results = dict(results)
    plan.negotiated_conflicted = set(conflicted())
    # Commit only copper from probes that saw every other net's pads.
    plan.negotiated_legal = legal if build_map else set()
    if build_map:
        # The grid model guides the negotiation; what a commit ships is
        # checked on the real geometry. Accept nets in probe order, each
        # only if its copper clears every net accepted before it.
        cand = [n for n in order if n in plan.negotiated_legal
                and n not in plan.negotiated_conflicted]
        from obstacle_map import resolve_hole_clearance
        pair_cl, pair_cl_max = None, 0.0
        if hasattr(config, 'pair_clearance_inert') and not config.pair_clearance_inert():
            # the board's pairwise rules (.kicad_dru class pairs, layer and
            # track rules), per geometry and layer as check_drc grades them
            pair_cl = lambda a, b, kind, layer: config.pair_clearance(
                a, b, layer if kind != 'stack' else None, kind=kind)
            pair_cl_max = config.max_pair_clearance()
        rejected = exact_rejects(cand, results, net_size, h2h,
                                 float(resolve_hole_clearance(pcb_data, config) or 0.0),
                                 pair_cl=pair_cl, pair_cl_max=pair_cl_max)
        stats['exact_rejected'] = len(rejected)
        plan.negotiated_conflicted |= rejected
    dump = k.get('neg_dump')
    if dump:
        # Rebuild the field for the kept paths and write where they conflict.
        for c in range(C):
            present[c][:] = 0
            present_v[c][:] = 0
        for nid, path in plan.rough_paths.items():
            set_path(nid, path, results.get(nid))
            load(nid)
        hot = {}
        for nid in conflicted():
            own, oth = others_at(nid)
            cells = own[oth > 0]
            lay, rem = np.divmod(cells, field.h * field.w)
            gy, gx = np.divmod(rem, field.w)
            hot[str(nid)] = np.stack([lay, gx + field.x0, gy + field.y0], 1).tolist()
        import json
        with open(dump, 'w') as f:
            json.dump({'grid_step': step, 'stats': stats,
                       'paths': {str(n): p for n, p in plan.rough_paths.items()},
                       'conflicts': hot}, f)
    # Trees whose taps left terminals unconnected (stage 2 finishes them).
    stats['open_terminals'] = sum(len(r.get('failed_pads_info') or ())
                                  for r in results.values() if r)
    stats['seconds'] = round(time.time() - t0, 1)
    print(f"Negotiated plan: {stats['rounds']} round(s), conflicted nets per "
          f"round {stats['conflicted']}, kept round {stats['best_round']} "
          f"({stats['best_conflicted']} conflicted), {stats['failed']} failed re-probe(s), "
          f"{stats['iterations']} iterations, failed per round {stats['failed_per_round']}, "
          f"{stats['open_terminals']} open tree "
          f"terminal(s), {len(stats.get('pair_fallback', []))} pair fallback(s), "
          f"{stats.get('exact_rejected', 0)} rejected by the exact check, "
          f"{stats['seconds']}s")
    return stats
