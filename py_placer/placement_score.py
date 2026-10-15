#!/usr/bin/env python3
"""Placement quality on a COPPER-FREE board -- the terms a lap can be ranked by (#894).

    python3 -X utf8 py_placer/placement_score.py BOARD [--json PATH] [--intent PATH]
                                                 [--length-groups JSON]

Why this exists
---------------
On a copper-free board every placement lap scores the same `blocking` (the
board's unrouted count, which is the ROUTING half's job) and the same `quality`
(`vias 0, copper_mm 0.0, segments 0`). So the ledger cannot rank two placements
and the plateau test has nothing to compare: measured on run 25, seven laps of
one board produced one number, while the layout that shipped had a crossed USB
pair, a connector whose pin order fought the bridge IC's, three nets pushed to
the back cutting the ground pour, and 22 vias for 24 nets. The routing half
paid for all of it and the placement half's instruments said clean.

NO SCALAR. There is deliberately no aggregate over these terms, and no weight
anywhere in this file. #694 is why: a corridor term's measured sign REVERSED
while an aggregate verdict kept printing PASS, because collapsing several
signals into one mark means no reader can say which of its inputs moved. Laps
are compared by `compare_terms`, which is PARETO -- better on every term it can
compare, worse on every term, or `mixed` with both sides named.

THREE of the six terms CALL an existing grader rather than mirroring it:
`pair_length` and `pin_order_crossings` read `board_context.pin_order_rows`,
and `cluster_to_pin` reads `floorplan.grade`'s measured proximity rows plus
`board_context.serves_map`. A re-implementation of a grader in this repo has
already been measured disagreeing with it 83 times, worst 0.234mm.

The other THREE deliberately do not, and each says why at its definition: the
nearest existing implementation measures a DIFFERENT QUANTITY.
`plane_cut_proxy` does not call `routability.corridor_cut_mm` (it needs a
declared corridor, and its chord is position-invariant along the lane, so it
cannot rank two placements that differ by a nudge); `balance` does not call
`check_pockets.census_scalars` (courtyard-area weighted, a hypot of both axes,
per-side); `edge_facing` does not call `escape.assign_faces` (one face per
pad, with a row's end pads sent to the row's ends -- it answers "which lane
does this pad escape through", not "which side of the part is it on"). All
three build on geometry primitives -- `legality.pad_rect`, `rect_area`,
`part_class.mechanical_parts`, `placement.body`, `escape._face_geometry` --
rather than on a second copy of a grader, and `edge_facing`'s core lives in
`placement.edge_facing` so `floorplan.rule_pins_to_edge` and the seeder read
the same number.

One REPORT-ONLY section rides beside the terms and is NOT one of them:
`length_groups` (#1252), each declared matched group's length-FLOOR spread --
the copper the placement forces each member to have, and how much the group
must add to match at all. It is not in `TERM_ORDER`, so `compare_terms` and
the lap ledger never read it; `length_group_floors` says why and what it
cannot see.

Vacuity
-------
A term that could not be measured reports `ran: False` with a `reason` and
`value: None`. It NEVER reports 0. "No differential pair on this board" and
"the pairs are all perfectly short" are different answers, and a score that
cannot tell them apart will rank a board nothing measured above one that was
examined and found wanting.

Exit codes
    0  graded (any values)
    2  bad arguments
    3  board state (missing file, unparseable board)
This tool grades nothing and gates nothing, so there is no "4".
"""

#: #937 registry: which door(s) show this tool, and whether it changes
#: the board. Read by krt_registry.py -- by AST, never imported.
KRT_TOOL = {'scope': ['placement', 'combined'], 'kind': 'instrument'}

import argparse
import contextlib
import json
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ('', 'py_router', 'py_placer', 'py_tools'):
    _p = os.path.join(_ROOT, _d) if _d else _ROOT
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

SCHEMA = 1

#: The terms, IN ORDER, published so a consumer never invents one. There is
#: deliberately NO aggregate over them: see the module docstring.
TERM_ORDER = ('pair_length', 'pin_order_crossings', 'cluster_to_pin',
              'plane_cut_proxy', 'balance', 'edge_facing')

#: `edge_facing` counts parts with at least this many CONNECTED pads: a
#: two-pad passive has no row to face anything with. The value lives in
#: the geometry core, shared with the rule and the seeder's tie-break.
from placement.edge_facing import MIN_PADS as EDGE_FACING_MIN_PADS  # noqa: E402

#: Every term is lower-is-better. Shipped per term rather than assumed, so a
#: future term cannot silently invert `compare_terms` by being added.
_DIRECTION = 'lower-is-better'

#: `plane_cut_proxy` is defined on two layers only -- see its docstring.
PLANE_PROXY_LAYERS = 2

#: Below this the board has no long axis worth calling one, and `balance`
#: abstains rather than picking a side of a coin flip.
SQUARE_TOL = 0.01


def _term(value, unit, basis=None, **extra):
    """A measured term.

    `basis` names the POPULATION the number was taken over, when that
    population is not fixed by the board. Two totals over different
    populations are not larger and smaller versions of each other -- the same
    argument `converge.commensurability` makes about `blocking` -- so
    `term_deltas` refuses to compare a term whose basis moved, rather than
    reporting a change the placement did not cause.

    Measured, and the reason this exists: `plane_cut_proxy` counts nets forced
    around LOCKED parts, and the blocker set is whatever the operator froze.
    Over the four tracked laps of one board it is 3, 11, 3, 3 -- and the value
    goes 0.822, 22.324, 0.0, 0.0 with it. Most of that swing is bookkeeping
    about what was locked, not a fact about the arrangement.

    The same holds when the RULE that chooses the population changes, even on
    a fixed board: `pad_area_balance` stopped weighing paste/mask apertures as
    copper in #1143, and names that rule as its basis so a lap scored before
    the change is not compared with one scored after it.
    """
    out = {'ran': True, 'reason': None, 'value': value, 'unit': unit,
           'direction': _DIRECTION, 'basis': basis}
    out.update(extra)
    return out


def _skip(reason, unit, **extra):
    """A term that did not measure. `value` is None, NEVER 0 (see Vacuity)."""
    out = {'ran': False, 'reason': reason, 'value': None, 'unit': unit,
           'direction': _DIRECTION, 'basis': None}
    out.update(extra)
    return out


# --------------------------------------------------------------- pin order

def pin_order_rows(pcb_data, pcb_file, clearance):
    """`board_context.pin_order_rows`, called. Kept as one call site because
    it builds a QuenchState and both pin-order terms read the same rows."""
    import board_context
    return board_context.pin_order_rows(pcb_data, pcb_file, clearance)


def pin_order_crossings(pin_order_doc) -> dict:
    """How many part pairs must cross to be routed at all.

    PURE -- takes what `pin_order_rows` returned, so it can be tested without
    a board. `inversions` is `placement.pair_order`'s answer and a proven
    LOWER BOUND on the crossings any router must pay (Supowit 1987; Leiserson
    & Pinter 1983); this counts the rows whose verdict is CROSSED and does not
    re-derive it.

    UNDETERMINED rows are counted separately and EXCLUDED from the value.
    `board_context._verdict` declares that a tie on the channel axis outranks
    the inversion count, because the tie-break invented an order the geometry
    does not have -- so calling such a row 0 crossings would tell a reader the
    pin order is fine when nothing measured it.
    """
    unit = 'crossed part pairs'
    if not isinstance(pin_order_doc, dict):
        return _skip('pin order was not measured', unit)
    err = pin_order_doc.get('error')
    if err:
        # The Pillow trap: `board_context.pin_order_rows` imports
        # `render_placement`, whose module scope runs a dependency check that
        # RAISES without Pillow -- and its own call site swallows it, so the
        # failure arrives as an empty row list. Empty rows and "no pair
        # crosses" look identical, which is why the error is checked first.
        return _skip(f'board_context.pin_order_rows could not measure: {err}',
                     unit)
    rows = pin_order_doc.get('rows') or []
    iface = [r for r in rows if r.get('scope') == 'interface']
    if not iface:
        return _skip('no part pair on this board shares >= 2 scoring nets, '
                     'so there is no pin order to agree or disagree', unit)
    crossed = [r for r in iface if r.get('verdict') == 'CROSSED']
    undet = [r for r in iface
             if str(r.get('verdict') or '').startswith('UNDETERMINED')]
    if len(undet) == len(iface):
        return _skip(
            f'all {len(iface)} row(s) tie on the channel axis, so every '
            f'inversion count is one the tie-break invented rather than one '
            f'the geometry has', unit, rows=len(iface), undetermined=len(undet))
    # BASIS: the DETERMINABLE rows. Undetermined rows are excluded from the
    # count, so the count is taken over a population that moves: measured on
    # the tracked lineage, one board has 2 undetermined of 21 interface rows
    # and another has 0, so `6` is a count over 19 rows and `10` over 21. A
    # delta of 4 across that pair is partly a change in what could be judged.
    return _term(len(crossed), unit,
                 basis=sorted(f"{r['a']}~{r['b']}" for r in iface
                              if r not in undet),
                 rows=len(iface),
                 undetermined=len(undet),
                 inversions=sum(r.get('inversions') or 0 for r in crossed),
                 pairs=[{'a': r['a'], 'b': r['b'], 'nets': r.get('nets'),
                         'inversions': r.get('inversions'),
                         'span_mm': r.get('span_mm')} for r in crossed])


#: The rule that chooses `pair_length`'s population, named in its basis so a
#: lap scored before #1251 (direct rows only) is `not-comparable` against one
#: scored after it, as `BALANCE_BASIS` does for #1143.
PAIR_LENGTH_BASIS = ('pair rows: two parts carrying both nets, plus pairs '
                     'composed end to end through per-leg series parts '
                     '(#1251)')

#: The fields a composed row (#1251) carries into `pairs[]`.
_COMPOSED_KEYS = ('series', 'legs_mm', 'leg_skew_mm', 'span_basis',
                  'stops_at', 'note')


def _is_pair_row(row) -> bool:
    """A pair-scoped row: `pair POS/NEG...`, or the bare `pair` a raised pair
    branch leaves. Reading only `startswith('pair ')` dropped the second, and
    the term then reported that no pair had been found (#1251)."""
    scope = str(row.get('scope') or '')
    return scope == 'pair' or scope.startswith('pair ')


def _raised(row) -> str:
    """The exception a bare-`pair` row records, as precisely as it says."""
    if row.get('error'):
        return str(row['error'])
    verdict = str(row.get('verdict') or '')
    if verdict.startswith('NOT MEASURED (') and verdict.endswith(')'):
        return verdict[len('NOT MEASURED ('):-1]
    return verdict or 'an unnamed error'


def pair_length(pin_order_doc) -> dict:
    """The worst span of a declared differential pair, in mm.

    PURE, over the same rows. For a pair two parts carry whole, `span_mm` is
    `board_context._pair_span_mm`'s number and PR #917 added it for exactly
    this: over the nets two parts share, the MINIMUM pad-centre distance per
    net, then the MAXIMUM over nets. Worst-net, best-pad -- a bus is as long
    as its longest member.

    A pair through per-leg series parts (AC caps, USB series resistors) is
    carried whole by no two parts, and #1251 found every such pair missing:
    ulx3s's 14 pairs gave no row. `board_context.composed_pair_rows` composes
    them through the router's own junction rule; such a row's `span_mm` is its
    longer leg's pad-centre polyline through the series pads, and `pairs[]`
    carries both legs and |P - N| (`legs_mm`, `leg_skew_mm`) as numbers, not
    as a verdict.

    Reads the PAIR-scoped rows only. A differential pair's polarity is a
    two-net question and asking it as part of the whole interface hides it:
    on the run-25 fixture one part pair AGREES over three shared nets while
    the two pair nets alone are CROSSED.

    When nothing is measured the reason names WHICH of four things happened,
    with its count: no pair was found; pairs were found but none is carried
    whole by two parts or composes through series parts (named); the pair
    branch raised (named); rows were found without a span. They used to share
    one reason, "found no differential pair", which was false for three.
    """
    unit = 'mm'
    if not isinstance(pin_order_doc, dict):
        return _skip('pin order was not measured', unit)
    if pin_order_doc.get('error'):
        return _skip(f"board_context.pin_order_rows could not measure: "
                     f"{pin_order_doc['error']}", unit)
    census = pin_order_doc.get('pairs')
    census = census if isinstance(census, dict) else None
    rows = [r for r in (pin_order_doc.get('rows') or []) if _is_pair_row(r)]
    raised = [r for r in rows if str(r.get('scope') or '') == 'pair']
    rows = [r for r in rows if r not in raised]
    if raised:
        # The population is INCOMPLETE, so no worst is reported -- a max over
        # the rows that happened to be measured before the error would read
        # as the board's worst pair.
        return _skip(
            f"the pair branch of board_context.pin_order_rows raised "
            f"({'; '.join(_raised(r) for r in raised)}), so the pair "
            f"population is incomplete and no worst span is reported"
            + (f' ({len(rows)} pair row(s) were measured before it)'
               if rows else ''),
            unit, raised=[_raised(r) for r in raised], pair_rows=len(rows))
    spans = [(r['span_mm'], r) for r in rows
             if isinstance(r.get('span_mm'), (int, float))]
    unspanned = [{'a': r.get('a'), 'b': r.get('b'), 'scope': r.get('scope'),
                  'verdict': r.get('verdict'), 'note': r.get('note')}
                 for r in rows
                 if not isinstance(r.get('span_mm'), (int, float))]
    found = len(census['found']) if census else None
    not_measured = list(census.get('not_measured') or []) if census else []
    if not spans:
        if rows:
            return _skip(
                f'{len(rows)} differential-pair row(s) were found, but none '
                f'carries a span_mm, so the pair length was not measured',
                unit, pair_rows=len(rows), rows_without_span=unspanned,
                pairs_found=found)
        if found:
            named = '; '.join(f"{p['pair']} ({p['why']})"
                              for p in not_measured[:6])
            more = (f' (and {len(not_measured) - 6} more)'
                    if len(not_measured) > 6 else '')
            return _skip(
                f'list_nets.find_differential_pairs found {found} pair(s), '
                f'but none is carried whole by two parts and none composes '
                f'end to end through per-leg series parts: {named}{more}',
                unit, pairs_found=found, not_measured=not_measured)
        if census is None:
            # A document from before the census, or a hand-built one: it
            # cannot say whether a pair was found, so this does not claim
            # that none was.
            return _skip('board_context.pin_order_rows reported no '
                         'differential-pair row, and no pair census to say '
                         'whether any pair was found', unit)
        return _skip(
            'list_nets.find_differential_pairs found no differential pair on '
            'this board (it is name-based, and 2-terminal resonators are '
            'rejected). --impedance-nets is a route.py glob list, not a pair '
            'source, and is deliberately not read here', unit, pairs_found=0)
    worst, row = max(spans, key=lambda t: t[0])

    def published(r):
        out = {'a': r['a'], 'b': r['b'], 'scope': r['scope'],
               'span_mm': r['span_mm'], 'verdict': r.get('verdict')}
        if r.get('composed'):
            out['composed'] = True
            out.update({k: r.get(k) for k in _COMPOSED_KEYS})
        return out
    # BASIS: the rule plus the measured rows. The population is pose-invariant
    # on one board (which parts carry which nets), so two laps of one board
    # share it -- and a lap that lost a row, or was scored under the old rule,
    # is refused rather than compared as if it were the same max.
    return _term(round(float(worst), 3), unit,
                 basis=[PAIR_LENGTH_BASIS] + sorted(
                     f"{r['a']}~{r['b']} {r['scope']}" for _s, r in spans),
                 pairs=[published(r)
                        for _s, r in sorted(spans, key=lambda t: -t[0])],
                 worst_pair=f"{row['a']}~{row['b']} {row['scope']}",
                 pairs_found=found,
                 composed=sum(1 for _s, r in spans if r.get('composed')),
                 rows_without_span=unspanned,
                 not_measured=not_measured)


# ----------------------------------------------------------- cluster to pin

def cluster_to_pin(pcb_data, pcb_file, *, intent=None, clearance=None) -> dict:
    """How far each passive sits from the pin it serves, worst case, in mm.

    TWO channels, because neither covers the board alone:

    * DECLARED -- #902's `proximity` claims, graded by `floorplan.grade` and
      read off `GradeResult.proximity_measured`, which reports a clause that
      PASSES as well as one that fails. This is the only channel that can
      reach a 3-pad regulator's bulk capacitors at all.
    * INFERRED -- `board_context.serves_map`, the decap tether election inside
      its own radius, for the parts nobody declared.

    NOT the raw tether election, and #894 says so explicitly: it is syntactic
    (a ref starting `C` bridging exactly two nets) and its target must carry 4
    copper pads, so a 3-pad SOT89 regulator can never be elected at any radius
    -- which is how a regulator's own bulk capacitors came to be graded
    against a USB socket.
    """
    unit = 'mm'
    rows, unresolved, declared_claims = [], [], set()
    if intent is not None:
        try:
            from placement import floorplan as fp
            res = fp.grade(intent, pcb_data, pcb_file, clearance=clearance)
            for r in (res.proximity_measured or []):
                rows.append({'ref': r['ref'], 'near': r['near'],
                             'gap_mm': r['gap_mm'], 'limit_mm': r.get('limit_mm'),
                             'passes': r.get('passes'), 'geom': r.get('basis'),
                             'source': 'declared', 'claim': r.get('claim')})
                declared_claims.add(r.get('claim'))
            # A claim the grader could NOT resolve -- a ref that is not on the
            # board, a pad number the part does not have -- measures nothing,
            # and `proximity_measured` records nothing for it. Reading only
            # the measured rows therefore turned "your intent named a part
            # that does not exist" into a clean number taken over whatever
            # else happened to resolve. The grader says so; this reads it.
            unresolved = [v.message for v in res.violations
                          if v.rule == 'proximity_unresolved']
        except Exception as exc:                             # noqa: BLE001
            return _skip(f'floorplan.grade could not measure the declared '
                         f'proximity claims: {type(exc).__name__}: {exc}', unit)
    declared_n = len(rows)
    if intent is not None and unresolved and not rows:
        return _skip(
            f'every declared proximity claim failed to resolve, so nothing '
            f'was measured: {unresolved[0]}'
            + (f' (and {len(unresolved) - 1} more)' if len(unresolved) > 1
               else ''), unit, declared=0, unresolved=len(unresolved),
            unresolved_claims=unresolved[:8])
    inferred = {}
    try:
        import board_context
        inferred = board_context.serves_map(pcb_data)
    except Exception:                                        # noqa: BLE001
        inferred = {}
    named = {(r['ref'], r['near']) for r in rows}
    for cap, (ic, dist) in sorted(inferred.items()):
        if (cap, ic) in named:
            continue
        rows.append({'ref': cap, 'near': ic, 'gap_mm': round(float(dist), 4),
                     'limit_mm': None, 'passes': None, 'geom': 'centre',
                     'source': 'inferred', 'claim': None})
    if not rows:
        return _skip(
            'the intent declares no proximity claims and no capacitor sits '
            'within the decap election radius of an elected IC, so nothing '
            'says which part serves which pin', unit,
            declared=0, inferred=0, unresolved=len(unresolved))
    # THE VALUE COMES FROM THE DECLARED ROWS when there are any, and the basis
    # is the declared CLAIM IDs -- not the measured pair list.
    #
    # WHEN A CLAIM IS DECLARED. With no intent there is nothing but the
    # inferred half, and its basis is the elected pair list -- which does move
    # with the poses, so two laps that re-elect are not comparable on this
    # term. That is a real limitation of the no-intent path and the reason
    # #902 exists: the way to make this term judge a moving board is to
    # DECLARE the claims.
    #
    # The pair list was the wrong basis and the reason is sharp: the inferred
    # half is the decap election CLIPPED at `groups.DECAP_RADIUS_MM`, so
    # pushing a capacitor past that radius -- the worst thing this term is
    # supposed to name -- drops the pair out of the population entirely. The
    # basis then "moves", the comparison is refused, and the reported max
    # FALLS. Measured on the tracked lineage: a capacitor was re-elected from
    # one IC to another by part motion alone, with no claim added. A basis
    # derived from the poses being compared cannot judge those poses.
    #
    # Declared claim ids come from the intent, which does not move when a part
    # does, so a declared clause getting worse is a VALUE change and is judged.
    scored = [r for r in rows if r['source'] == 'declared'] or rows
    basis = (sorted(c for c in declared_claims if c) if declared_n
             else sorted(f"{r['ref']}~{r['near']}" for r in rows))
    worst = max(scored, key=lambda r: r['gap_mm'])
    return _term(round(float(worst['gap_mm']), 4), unit, basis=basis,
                 scored=('declared' if declared_n else 'inferred'),
                 declared=declared_n, inferred=len(rows) - declared_n,
                 unresolved=len(unresolved),
                 unresolved_claims=unresolved[:8],
                 worst_pair=f"{worst['ref']}~{worst['near']}",
                 rows=sorted(rows, key=lambda r: -r['gap_mm']))


# ---------------------------------------------------------- plane cut proxy

def _clip_len(a, b, rect) -> float:
    """Length of segment `a`-`b` lying inside axis-aligned `rect`, in mm.

    Liang-Barsky. Replaces a boundary-crossing TEST, which was wrong three
    ways at once: it credited the whole chord rather than the part it removes,
    it discarded any segment with an endpoint inside the rect (so a bigger
    blocker obstructed LESS), and being a proper-intersection test it missed a
    chord lying exactly along an edge -- not exotic on a grid-snapped board
    whose blocker rects come from pad bounding boxes.

    The rect is CLOSED, so a segment lying exactly along an edge counts for
    its full length rather than 0. That is a choice, stated because it is one:
    a track running along a part's boundary is under its shadow as much as one
    a micron inside, and the alternative makes the answer depend on whether a
    grid-snapped pad centre landed on the boundary or just off it. A segment
    entirely inside returns its own length; one entirely outside returns 0.0.
    """
    (x0, y0), (x1, y1) = a, b
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - rect[0]), (dx, rect[2] - x0),
                 (-dy, y0 - rect[1]), (dy, rect[3] - y0)):
        if p == 0:
            if q < 0:
                return 0.0        # parallel to this edge and outside it
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return 0.0
            t0 = max(t0, t)
        else:
            if t < t0:
                return 0.0
            t1 = min(t1, t)
    if t1 <= t0:
        return 0.0
    return math.hypot(dx, dy) * (t1 - t0)


def plane_cut_proxy(pcb_data, pcb_file=None) -> dict:
    """Total straight-line length of nets forced around a locked part, in mm.

    On two layers a net whose two ends sit on opposite sides of an immovable
    part must cross under it or go around, and either way it removes reference
    copper. `routability.corridor_cut_mm`'s docstring is the authority for why
    LENGTH is the right currency there -- "the geometric lower bound on
    reference-plane copper the crossing removes, which is the quantity
    check_impedance's void-run counter later grades" -- and that argument is
    also why this term is gated on exactly two copper layers.

    It does NOT call `corridor_cut_mm`, for two reasons that function states
    itself: it needs a DECLARED corridor, which a fresh placement board does
    not have; and its chord is POSITION-INVARIANT along the lane, so it cannot
    rank two placements that differ by a nudge, which is this term's whole job.

    "Endpoints" is ill-defined for an N-pad net, so the definition used is the
    DIAMETER PAIR -- the two pads furthest apart -- and each net is counted
    once, against its worst blocker. Said here and in the payload rather than
    left for a reader to infer.

    REPORTED, never optimised: like the corridor term it is a screening
    signal, and #893 (which consumes these terms as a search cost) must not
    put a part-nudging search on it.
    """
    unit = 'mm'
    layers = list(getattr(pcb_data.board_info, 'copper_layers', []) or [])
    if len(layers) != PLANE_PROXY_LAYERS:
        return _skip(
            f'plane_cut_proxy is defined on a {PLANE_PROXY_LAYERS}-layer '
            f'board, where a crossing removes reference copper there is no '
            f'other layer to carry; this board has {len(layers)} copper '
            f'layer(s)', unit, copper_layers=len(layers))
    try:
        from placement import part_class
        from placement import body as body_mod
        from placement import floorplan as fp
        from placement import parser as kparser
    except Exception as exc:                                 # noqa: BLE001
        return _skip(f'could not import the geometry this term calls: '
                     f'{type(exc).__name__}: {exc}', unit)
    blockers = set()
    try:
        blockers |= set(part_class.mechanical_parts(pcb_data) or {})
    except Exception:                                        # noqa: BLE001
        pass
    try:
        if pcb_file:
            blockers |= set(kparser.extract_locked_refs(pcb_file) or ())
    except Exception:                                        # noqa: BLE001
        pass
    blockers |= {r for r, f in (pcb_data.footprints or {}).items()
                 if getattr(f, 'locked', False)}
    if not blockers:
        return _skip(
            'no footprint is locked and none classifies as mechanical, so '
            'there is nothing a net can be forced around', unit)
    try:
        bodies = body_mod.board_bodies(pcb_data, pcb_file)
    except Exception as exc:                                 # noqa: BLE001
        return _skip(f'placement.body could not be read: '
                     f'{type(exc).__name__}: {exc}', unit)
    rects = {}
    for ref in sorted(blockers):
        rect, src = fp.drawn_body_rect(bodies.get(ref),
                                       (pcb_data.footprints or {}).get(ref))
        if rect is not None:
            rects[ref] = (rect, src)
    if not rects:
        return _skip('every locked or mechanical part answers body source '
                     '"none", so none of them has geometry to be forced '
                     'around', unit, blockers=len(blockers))
    # The REFERENCE nets are excluded. On two layers the ground pour and the
    # rails ARE the reference copper, so they cannot "remove reference copper"
    # by passing a part -- and between them they dominate the number if left
    # in. Measured on the run-25 placed board with the filter off: 46.702mm
    # total, of which `/+3.3V` is 12.227 and `GND` 12.150 -- 52% of it, over
    # a 17-pad "diameter pair" that is an artifact of pad ordering rather
    # than any route anyone will draw. With the filter on the total is
    # 22.324mm.
    from net_queries import is_ground_net_name, is_power_net_name
    skipped_nets = []
    pads_by_net = {}
    for ref, fpo in (pcb_data.footprints or {}).items():
        for pad in (fpo.pads or ()):
            nid = getattr(pad, 'net_id', 0) or 0
            if nid:
                pads_by_net.setdefault(nid, []).append(
                    (ref, float(pad.global_x), float(pad.global_y)))
    total, hits = 0.0, []
    for nid, pads in sorted(pads_by_net.items()):
        if len(pads) < 2:
            continue
        net = pcb_data.nets.get(nid)
        name = getattr(net, 'name', '') or ''
        if is_ground_net_name(name) or is_power_net_name(name):
            skipped_nets.append(name)
            continue
        # The DIAMETER PAIR, and the whole net's obstruction is taken over
        # every blocker it clips. Blocker rects are USUALLY disjoint, so the
        # clipped lengths usually add without double-counting -- but they are
        # not guaranteed to be, and on the run-25 placed board two of the 11
        # overlap by 1.0mm2. No net's chord crosses both today, so this is
        # latent rather than live; a net that did would have that overlap
        # counted twice. Stated rather than asserted, because `check_assembly`
        # has a whole containment channel for bodies that sit inside bodies.
        best, pa, pb = -1.0, None, None
        for i, p in enumerate(pads):
            for q in pads[i + 1:]:
                d = (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
                if d > best:
                    best, pa, pb = d, p, q
        a, b = (pa[1], pa[2]), (pb[1], pb[2])
        for ref, (rect, src) in sorted(rects.items()):
            if ref in (pa[0], pb[0]):
                continue          # its own net is not forced around it
            # THE LENGTH INSIDE THE BODY, not the whole chord. The docstring's
            # authority is about "reference-plane copper the CROSSING
            # removes", and crediting the full span measured something else
            # entirely: an 80mm net grazing 0.01mm of a part scored 5.3x worse
            # than a 15mm net straight through its middle. A clip is also what
            # makes an endpoint UNDER the part count -- a pad under a locked
            # can is precisely a net forced past it, and skipping those made
            # the term NON-MONOTONIC in blocker size (measured: growing one
            # part by 1mm a side took the total DOWN 13% and then saturated).
            inside = _clip_len(a, b, rect)
            if inside > 1e-9:
                total += inside
                hits.append({'net_id': nid, 'net': name or None,
                             'blocker': ref, 'body_source': src,
                             'inside_mm': round(inside, 3),
                             'chord_mm': round(math.hypot(b[0] - a[0],
                                                          b[1] - a[1]), 3)})
    # BASIS: the blocker set. Which parts are locked is an operator decision
    # that changes between laps -- measured, 3 / 11 / 3 / 3 over the four
    # tracked laps of one board -- and the total moves with it for reasons the
    # arrangement did not cause. Two laps that froze different parts are not
    # comparable here.
    return _term(round(total, 3), unit, basis=sorted(rects),
                 nets=len({h['net_id'] for h in hits}), blockers=len(rects),
                 definition='length of each net\'s diameter chord lying INSIDE '
                            'a locked part\'s drawn body, summed over parts; '
                            'ground and power nets excluded',
                 excluded_nets=sorted(set(skipped_nets)),
                 rows=sorted(hits, key=lambda r: -r['inside_mm'])[:20])


# ------------------------------------------------------------------ balance

#: The population `pad_area_balance` weighs, as its `basis` (#1143). Before
#: #1143 the term weighed paste/mask apertures as copper and published no basis;
#: naming the population makes `term_deltas` refuse a lap scored before the fix
#: against one scored after it, instead of reporting a balance change no
#: placement caused (watchy 0.0328 vs 0.0207 on the same poses).
BALANCE_BASIS = 'copper-pads: NPTH and aperture-only pads excluded (#1143)'


def pad_area_balance(pcb_data) -> dict:
    """Pad-copper-area first moment along the board's LONG axis, as a fraction
    of the span.

    #894 asks for "mass distribution along the board's long axis ... pad-area
    first moment", and that is what this is. It deliberately does NOT call
    `check_pockets.census_scalars`, which is a different quantity three ways:
    courtyard-area weighted rather than pad-area, a hypot of BOTH axes with no
    long-axis notion, and per-side with a headline side -- and it costs a
    `congestion_bins` grid pass.

    The weight is the pad's axis-aligned BOUNDING RECT area, not its true
    copper area -- `legality.pad_rect` is a bbox, so a round pad weighs d^2
    rather than pi*d^2/4. Published as `weight: pad_bbox_area` so nobody
    quotes it as copper. The 4/pi over-weighting is uniform across round pads
    and therefore mostly cancels in a centroid; it would not on a board whose
    round pads cluster at one end.

    Never call any of these "the centroid" without its weight -- they
    disagree. Measured over the four tracked laps of one board:

                                    tracked  placed   lap3    lap5
        pad-bbox-area (this term)   0.0930  0.0723  0.0703  0.0724
        pad-count control           0.0577  0.0557  0.0550  0.0557
        footprint-count control     0.0910  0.0582  0.0578  0.0582
    """
    unit = 'fraction of span'
    bounds = getattr(pcb_data.board_info, 'board_bounds', None)
    if not bounds:
        return _skip('the board declares no Edge.Cuts outline, so it has no '
                     'span and no long axis', unit)
    x0, y0, x1, y1 = bounds
    span_x, span_y = float(x1 - x0), float(y1 - y0)
    if max(span_x, span_y) <= 0:
        return _skip('the board outline has zero extent', unit)
    if abs(span_x - span_y) / max(span_x, span_y) < SQUARE_TOL:
        return _skip(f'the board is square within {SQUARE_TOL:.0%} '
                     f'(x {span_x:.1f}mm, y {span_y:.1f}mm), so there is no '
                     f'long axis to balance along', unit,
                     span_mm=[round(span_x, 3), round(span_y, 3)])
    from placement import legality as leg
    axis = 'x' if span_x >= span_y else 'y'
    span = span_x if axis == 'x' else span_y
    centre = ((x0 + x1) / 2.0) if axis == 'x' else ((y0 + y1) / 2.0)
    from kicad_parser import pad_is_aperture_only
    num = area = 0.0
    n = 0
    npth = 0
    aperture = 0
    for fpo in (pcb_data.footprints or {}).values():
        for pad in (fpo.pads or ()):
            # NPTH pads carry NO COPPER even when `layers` lists *.Cu -- their
            # `size` is the mask opening (CLAUDE.md). Weighing them puts mask
            # where the term claims to put copper. Measured on the run-25
            # lineage: two such pads bias the result by 0.0035 of span, which
            # is 1.7x the lap3-to-lap5 signal this term is the only one able
            # to resolve. It cancels there because the part is locked in all
            # three; it will not cancel on a board that moves one.
            if getattr(pad, 'pad_type', '') == 'np_thru_hole':
                npth += 1
                continue
            # An aperture-only pad (a paste/mask window) carries no copper
            # either (#1143): a QFN's split paste windows weighed as copper
            # moved watchy 0.0207 -> 0.0328.
            if pad_is_aperture_only(pad):
                aperture += 1
                continue
            try:
                a = leg.rect_area(leg.pad_rect(pad))
            except Exception:                                # noqa: BLE001
                continue
            if a <= 0:
                continue
            u = float(pad.global_x) if axis == 'x' else float(pad.global_y)
            num += a * u
            area += a
            n += 1
    if area <= 0:
        return _skip('no pad on this board has copper area, so there is no '
                     'mass to weigh', unit)
    centroid = num / area
    return _term(round(abs(centroid - centre) / span, 4), unit,
                 basis=[BALANCE_BASIS],
                 axis=axis, span_mm=round(span, 3),
                 centroid_mm=round(centroid, 3), centre_mm=round(centre, 3),
                 weight='pad_bbox_area', pad_area_mm2=round(area, 3),
                 pads=n, npth_pads_excluded=npth,
                 aperture_pads_excluded=aperture)


def edge_facing(pcb_data, pcb_file=None, intent=None) -> dict:
    """Connected pads on a row that faces the board outline with nothing beyond
    it, summed over every part with `EDGE_FACING_MIN_PADS` or more connected
    pads that is not an edge connector. Unit: pads. Lower is better.

    The definition, and why it does not call `escape.assign_faces`, live in
    `placement.edge_facing` -- one geometry core shared with
    `floorplan.rule_pins_to_edge` and the seeder's opt-in rotation tie-break,
    so the term, the rule and the search cannot disagree about a pad.

    EDGE CONNECTORS ARE EXCLUDED, because their mating row SHOULD face the
    edge. The exclusion is the intent's `edge_claims()` when an intent is
    given -- the declaration every other consumer reads -- and otherwise
    `part_class.classify_part` (`edge_receptacle` / `edge_actuator`), and the
    payload says which (`exclusion_basis`), because the two can differ on a
    part the classifier does not recognise.

    `basis` is the counted population: a lap that changes which parts are
    counted (a different intent, a part gaining or losing a net) is
    `not-comparable` under `term_deltas`, like `plane_cut_proxy`.

    Measured, run 26 (esp_prog placed from scratch): U2, the LDO, reads
    3 of 3 -- its whole pin row 0.40 mm from the north edge -- and the
    review's criterion 4 had passed it in prose.
    """
    unit = 'pads'
    bounds = getattr(getattr(pcb_data, 'board_info', None), 'board_bounds', None)
    if not bounds:
        return _skip('the board has no outline, so no face can meet it', unit)
    try:
        from placement import edge_facing as ef
        from placement import part_class
    except Exception as exc:                                 # noqa: BLE001
        return _skip(f'could not import the geometry this term calls: '
                     f'{type(exc).__name__}: {exc}', unit)
    if intent is not None:
        excluded = {c['ref'] for c in intent.edge_claims()}
        exclusion_basis = 'intent.edge_claims'
    else:
        excluded = set()
        exclusion_basis = 'part_class'
        for ref, fp in (pcb_data.footprints or {}).items():
            try:
                if part_class.classify_part(fp, ref).name in (
                        'edge_receptacle', 'edge_actuator'):
                    excluded.add(ref)
            except Exception:                                # noqa: BLE001
                pass
    by_part = {}
    total = 0
    for ref in sorted(pcb_data.footprints or {}):
        if ref in excluded:
            continue
        inputs = ef.part_inputs(pcb_data, ref)
        if inputs is None:
            continue
        pads, rect, partners, centre, pitch = inputs
        if len(pads) < EDGE_FACING_MIN_PADS:
            continue
        r = ef.count_pads_to_edge(pads, rect, bounds, partners, centre,
                                  pitch=pitch)
        by_part[ref] = {'pads': r['pads'], 'to_edge': r['to_edge'],
                        'faces': r['faces'],
                        'gaps': {f: g for f, g in r['gaps'].items()
                                 if g <= r['edge_mm']}}
        total += r['to_edge']
    if not by_part:
        return _skip(f'no part carries {EDGE_FACING_MIN_PADS} or more connected '
                     f'pads outside the edge-connector set', unit,
                     excluded=sorted(excluded), exclusion_basis=exclusion_basis)
    n_pads = sum(v['pads'] for v in by_part.values())
    return _term(total, unit, basis=sorted(by_part),
                 share=round(total / n_pads, 4) if n_pads else 0.0,
                 by_part=by_part, edge_mm=ef.EDGE_MM,
                 excluded=sorted(excluded), exclusion_basis=exclusion_basis)


# ------------------------------------------------------------ length groups

#: What a length-floor spread cannot see, printed with every `length_groups`
#: section that was asked for (#1252). A floor spread is not a routed spread.
LENGTH_FLOOR_CANNOT_SEE = (
    'detours around obstacles: each floor is a straight 0/45/90 run between '
    'pad centres',
    'layer assignment (outer vs inner velocity), vias and via barrel length',
    'meander room near the short members: copper_to_add_mm says how much '
    'copper the group must add, not whether it fits',
    'fly-by and T topologies: a multipoint floor (farthest pad pair on '
    'different parts) bounds the total copper of any tree that spans the '
    'net, not one driver-to-receiver path',
    'pad die_length (package delay; not parsed, #1248) and where a track '
    'enters a pad (pad-edge entry)',
    '`mode: pin_pair` is echoed, not used: the floor is the net floor above, '
    'not the pads[0] -> pads[1] path board_score grades in that mode',
)


def load_length_groups(path) -> dict:
    """`board_score --length-groups`'s file: ``{"G": {"nets": [...],
    "tolerance_mm": t, "mode": "pin_pair"?}}``.

    Refuses (ValueError) only what `board_score.score_length` would crash on --
    not a JSON object of objects, or a tolerance that is not a number -- so a
    file one tool accepts the other does too. A group naming fewer than two
    nets on the board is a per-group `ran: false`, as there.
    """
    with open(path, encoding='utf-8') as fh:
        groups = json.load(fh)
    if not isinstance(groups, dict):
        raise ValueError('the file must be a JSON object of '
                         '{"group": {"nets": [...], "tolerance_mm": t}}')
    for name, spec in groups.items():
        if not isinstance(spec, dict):
            raise ValueError(f'group {name!r} is not an object')
        nets = spec.get('nets', [])
        if not isinstance(nets, list) or not all(isinstance(n, str)
                                                 for n in nets):
            raise ValueError(f'group {name!r}: "nets" must be a list of '
                             f'net names')
        try:
            float(spec.get('tolerance_mm', 0.1))
        except (TypeError, ValueError):
            raise ValueError(f'group {name!r}: "tolerance_mm" is not a '
                             f'number') from None
    return groups


def _octilinear(a, b) -> float:
    """The shortest 0/45/90 run between two pad centres."""
    dx = abs(float(a.global_x) - float(b.global_x))
    dy = abs(float(a.global_y) - float(b.global_y))
    return max(dx, dy) + (math.sqrt(2.0) - 1.0) * min(dx, dy)


def net_length_floor(pcb_data, net_id):
    """`(floor, None)` or `(None, why)`: the least copper a net can have.

    * 2 parts: the octilinear distance between the CLOSEST pad centres of the
      two parts -- a lower bound on any 0/45/90 pad-centre route joining them.
    * 3+ parts: the octilinear distance of the FARTHEST pad pair on different
      parts -- a lower bound on any tree that spans the net, since the tree
      holds a path between those two pads.

    Copper pads only (`pad_has_copper`): an NPTH hole carrying a net is not a
    connection (#328), and a paste window is not a pin (#1143).
    """
    from kicad_parser import pad_has_copper
    net = (getattr(pcb_data, 'nets', None) or {}).get(net_id)
    by_part = {}
    for pad in (getattr(net, 'pads', None) or ()):
        if pad_has_copper(pad):
            by_part.setdefault(pad.component_ref, []).append(pad)
    if len(by_part) < 2:
        return None, ('it has no copper pad' if not by_part else
                      f'its copper pads are on one part only '
                      f'({next(iter(by_part))})')
    refs = sorted(by_part, key=str)
    cross = [(_octilinear(a, b), a, b)
             for i, ra in enumerate(refs) for rb in refs[i + 1:]
             for a in by_part[ra] for b in by_part[rb]]
    two = len(refs) == 2
    pick = (min if two else max)(cross, key=lambda t: t[0])
    return {'floor_mm': round(pick[0], 3),
            'basis': '2-part' if two else 'multipoint',
            'parts': refs,
            'pads': [f'{pick[1].component_ref}.{pick[1].pad_number}',
                     f'{pick[2].component_ref}.{pick[2].pad_number}'],
            '_raw': pick[0]}, None


def _velocity_layers(pcb_data):
    """(outer layer, first inner layer or None) for the ps figures."""
    layers = list(getattr(pcb_data.board_info, 'copper_layers', None) or ())
    outer = layers[0] if layers else 'F.Cu'
    inner = layers[1] if len(layers) > 2 else None
    return outer, inner


def length_group_floors(pcb_data, groups, source=None) -> dict:
    """REPORT-ONLY: each declared length group's floor spread (#1252).

    A placement fixes the shortest copper each member of a matched group can
    have, and nothing graded that before routing: the routed graders
    (`board_score` / `check_complete --length-groups`) measure copper that
    does not exist yet on a copper-free board. Per group this reports each
    member's floor (`net_length_floor`) with its basis, the longest floor and
    its net, the spread of floors, `copper_to_add_mm` = sum(longest - own
    floor) -- the copper the group must add to match at all -- and the
    longest floor in ps on an outer and an inner layer
    (`impedance.get_layer_ps_per_mm`). `tolerance_mm` is printed beside the
    spread with NO pass/fail: meanders can always add copper, so a floor
    spread is not a routed spread.

    NOT a term. It is not in `TERM_ORDER`, so `compare_terms` and the lap
    ledger are unchanged; a search or the seeder may rank by it only after
    `tests/test_placement_ab.py` (>= 3 distinct boards, paired and
    directional).
    """
    base = {'report_only': True, 'source': source,
            'cannot_see': list(LENGTH_FLOOR_CANNOT_SEE)}
    if groups is None:
        return dict(base, ran=False, groups={},
                    reason='no --length-groups given; no matched group\'s '
                           'length floors are measured')
    from impedance import get_layer_ps_per_mm
    outer, inner = _velocity_layers(pcb_data)
    stackup = bool(getattr(pcb_data.board_info, 'stackup', None))
    ps_mm = {'outer': get_layer_ps_per_mm(pcb_data, outer),
             'inner': get_layer_ps_per_mm(pcb_data, inner) if inner else None}
    velocity = {'outer': {'layer': outer,
                          'ps_per_mm': round(ps_mm['outer'], 3)},
                'inner': ({'layer': inner,
                           'ps_per_mm': round(ps_mm['inner'], 3)}
                          if inner else None),
                'stackup': stackup}
    notes = []
    if not inner:
        notes.append('the board has no inner copper layer')
    if not stackup:
        notes.append('the board declares no stackup, so every layer gets '
                     'get_layer_ps_per_mm\'s default dielectric')
    if notes:
        velocity['note'] = '; '.join(notes)
    by_name = {n.name: nid for nid, n in (pcb_data.nets or {}).items()
               if getattr(n, 'name', None)}
    out = {}
    for gname, spec in groups.items():
        names = list(spec.get('nets', []))
        tol = float(spec.get('tolerance_mm', 0.1))
        found = [n for n in names if n in by_name]
        missing = [n for n in names if n not in by_name]
        g = {'nets_declared': len(names), 'found': found, 'missing': missing,
             'tolerance_mm': tol, 'mode': spec.get('mode')}
        if len(found) < 2:
            out[gname] = dict(g, ran=False, reason=(
                f'fewer than 2 of its {len(names)} net(s) are on the board'
                + (f" (missing: {', '.join(missing)})" if missing else '')))
            continue
        members, unmeasured = {}, {}
        for n in found:
            m, why = net_length_floor(pcb_data, by_name[n])
            if m is None:
                unmeasured[n] = why
            else:
                members[n] = m
        if len(members) < 2:
            out[gname] = dict(g, ran=False, unmeasured=unmeasured, reason=(
                f'fewer than 2 of its nets have a floor (a net needs copper '
                f'pads on two parts): {len(members)} of {len(found)} found'))
            continue
        raw = {n: m.pop('_raw') for n, m in members.items()}
        longest = max(sorted(raw), key=raw.get)
        shortest = min(sorted(raw), key=raw.get)
        hi = raw[longest]
        ps = {'outer': {'layer': outer, 'ps': round(hi * ps_mm['outer'], 1)},
              'inner': ({'layer': inner, 'ps': round(hi * ps_mm['inner'], 1)}
                        if inner else None)}
        out[gname] = dict(
            g, ran=True, reason=None, members=members, unmeasured=unmeasured,
            longest_floor_mm=round(hi, 3), longest_net=longest,
            shortest_floor_mm=round(raw[shortest], 3), shortest_net=shortest,
            spread_mm=round(hi - raw[shortest], 3),
            copper_to_add_mm=round(sum(hi - v for v in raw.values()), 3),
            longest_floor_ps=ps)
    ran = any(g['ran'] for g in out.values())
    return dict(base, ran=ran, groups=out, velocity=velocity,
                reason=None if ran else (
                    'no group had 2 or more members with a floor'
                    if out else 'the file declares no group'))


# ------------------------------------------------------------- the document

def placement_terms(pcb_data, pcb_file, *, clearance=None, intent=None,
                    length_groups=None, length_groups_source=None) -> dict:
    """All six terms for one board, and the report-only `length_groups`
    section (#1252), which is not a term: `length_groups` is the loaded
    `--length-groups` file, None when none was given."""
    if clearance is None:
        try:
            import list_nets
            clearance = list_nets.board_floor_knobs(pcb_file, clearance=None)[0]
        except Exception:                                    # noqa: BLE001
            import routing_defaults as _rd
            clearance = _rd.CLEARANCE
    po = pin_order_rows(pcb_data, pcb_file, clearance)
    terms = {
        'pair_length': pair_length(po),
        'pin_order_crossings': pin_order_crossings(po),
        'cluster_to_pin': cluster_to_pin(pcb_data, pcb_file, intent=intent,
                                         clearance=clearance),
        'plane_cut_proxy': plane_cut_proxy(pcb_data, pcb_file),
        'balance': pad_area_balance(pcb_data),
        'edge_facing': edge_facing(pcb_data, pcb_file, intent=intent),
    }
    return {'schema': SCHEMA, 'kind': 'placement-terms',
            'board': os.path.abspath(pcb_file) if pcb_file else None,
            'segments': len(getattr(pcb_data, 'segments', ()) or ()),
            'clearance': clearance,
            'term_order': list(TERM_ORDER),
            'terms': terms,
            'length_groups': length_group_floors(
                pcb_data, length_groups, source=length_groups_source)}


# --------------------------------------------------------------- comparison

def term_deltas(old_terms, new_terms) -> list:
    """The raw + delta table, as data.

    One computation shared by the printer and the judge below, so a report and
    a verdict can never describe two different comparisons.
    """
    out = []
    for name in TERM_ORDER:
        a = (old_terms or {}).get(name) or {}
        b = (new_terms or {}).get(name) or {}
        av = a.get('value') if a.get('ran') else None
        bv = b.get('value') if b.get('ran') else None
        row = {'term': name, 'old': av, 'new': bv,
               'unit': b.get('unit') or a.get('unit'),
               'direction': b.get('direction') or a.get('direction')
               or _DIRECTION}
        comparable = (isinstance(av, (int, float))
                      and isinstance(bv, (int, float)))
        if comparable and a.get('basis') != b.get('basis'):
            # The two numbers are totals over DIFFERENT populations. Same
            # argument `converge.commensurability` makes about `blocking`: a
            # drop across such a pair is not evidence the placement improved,
            # it may be evidence that less was measured. Named, not silently
            # dropped -- a reader has to be able to see WHY it was not judged.
            row['delta'] = None
            row['judgement'] = 'not-comparable'
            row['why'] = 'the basis moved'
            # MULTISET differences, not set differences. No basis this
            # module builds today can contain a repeat (declared claims are a
            # set; the other three are one row per key), so this is currently
            # equivalent to set arithmetic -- but `basis` is a published
            # contract any term may implement, and an earlier per-pad basis
            # here DID repeat: a two-legged crystal contributed its pair
            # twice, and set arithmetic then reported `added: [] removed: []`
            # when one leg became unmeasurable. The refusal fired and named
            # nothing, which is the failure the comment above forbids.
            from collections import Counter
            ca, cb = Counter(a.get('basis') or ()), Counter(b.get('basis') or ())
            row['basis_added'] = sorted((cb - ca).elements())
            row['basis_removed'] = sorted((ca - cb).elements())
        elif comparable:
            row['delta'] = round(bv - av, 6)
            row['judgement'] = ('same' if row['delta'] == 0
                                else 'better' if row['delta'] < 0 else 'worse')
        else:
            # NOT 0. A term measured on one side only is excluded from the
            # comparison entirely -- defaulting it would make an unmeasured
            # lap look better or worse than a measured one, which is the shape
            # `_score_key` already refuses for `blocking`.
            row['delta'] = None
            row['judgement'] = 'not-comparable'
            # THREE reasons, not one. "Neither lap measured it" is a fact
            # about the board or the flags; "one lap measured it" is a fact
            # about what changed between them, and a reader chasing a term
            # that vanished must not be told the wrong one.
            row['why'] = ('neither lap measured it' if av is None and bv is None
                          else 'measured on one lap only')
            row['measured_on'] = ([] if av is None and bv is None
                                  else ['old'] if bv is None else ['new'])
        out.append(row)
    return out


def compare_terms(old_terms, new_terms):
    """`(verdict, detail)` -- PARETO, never a scalar.

    `verdict` is one of `better`, `worse`, `mixed`, `same`, `no-common-terms`,
    over the terms BOTH laps measured. Two laps that improve DIFFERENT terms
    are `mixed`, and both sides are named: that is #694's inversion made
    visible instead of arbitrated by a weight nobody can defend.
    """
    detail = term_deltas(old_terms, new_terms)
    judged = [r for r in detail if r['judgement'] != 'not-comparable']
    if not judged:
        return 'no-common-terms', detail
    better = [r for r in judged if r['judgement'] == 'better']
    worse = [r for r in judged if r['judgement'] == 'worse']
    if better and worse:
        return 'mixed', detail
    if better:
        return 'better', detail
    if worse:
        return 'worse', detail
    return 'same', detail


def format_delta(detail) -> str:
    """One line per term, for a human. Never a total.

    A NOT-COMPARABLE term is printed too, with why. It used to be skipped,
    which made this -- the only human-readable channel -- say that one term
    improved and nothing else happened, on a lap whose cluster distance had
    gone 2.0 to 9.9mm behind a moved basis. `term_deltas` records the refusal
    so a reader "can see WHY it was not judged"; a printer that drops it makes
    that comment false.
    """
    bits = []
    for r in detail:
        if r['judgement'] == 'not-comparable':
            why = r.get('why') or 'not comparable'
            moved = ''
            if r.get('basis_added') or r.get('basis_removed'):
                moved = (f" [+{','.join(r.get('basis_added') or []) or '-'}"
                         f" -{','.join(r.get('basis_removed') or []) or '-'}]")
            bits.append(f"{r['term']} {r['old']} -> {r['new']} "
                        f"(NOT JUDGED: {why}{moved})")
        else:
            bits.append(f"{r['term']} {r['old']} -> {r['new']} "
                        f"({r['delta']:+g}, {r['judgement']})")
    return '; '.join(bits) or 'no term was measured on either lap'


# ---------------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('board')
    p.add_argument('--json', metavar='PATH',
                   help='write the document here (stdout otherwise)')
    p.add_argument('--intent', metavar='PATH',
                   help="a floorplan intent. Its `proximity` claims are what "
                        "`cluster_to_pin`'s DECLARED channel grades; without "
                        "one that channel reports only what the decap "
                        "election infers, and says so")
    p.add_argument('--clearance', type=float,
                   help="the board's own floor is read when this is omitted")
    p.add_argument('--length-groups', metavar='JSON',
                   help='JSON FILE of {"group": {"nets": [...], '
                        '"tolerance_mm": 0.1, "mode": "pin_pair"}} -- the '
                        'board_score / check_complete format. Fills the '
                        'REPORT-ONLY `length_groups` section (#1252): each '
                        "group's length-floor spread before routing. Not a "
                        'term: it never enters `compare_terms`. A path that '
                        'is not a readable file of that shape exits 2')
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.isfile(args.board):
        print(f'board not found: {args.board}', file=sys.stderr)
        return 3
    intent = None
    if args.intent:
        try:
            from placement import floorplan as fp
            intent = fp.load_intent(args.intent)
        except Exception as exc:                             # noqa: BLE001
            print(f'--intent: {type(exc).__name__}: {exc}', file=sys.stderr)
            return 2
    groups = None
    if args.length_groups:
        # Loud, like --intent: board_score reads a missing file as "ungraded"
        # and exits 0, and a mistyped path then looks like a measured board.
        try:
            groups = load_length_groups(args.length_groups)
        except Exception as exc:                             # noqa: BLE001
            print(f'--length-groups: {type(exc).__name__}: {exc}',
                  file=sys.stderr)
            return 2
    # STDOUT IS THE DOCUMENT. The quench and the parser both print warnings,
    # and a caller json.loads() this whole stream -- `board_context.main` does
    # the same for the same reason.
    with contextlib.redirect_stdout(sys.stderr):
        from kicad_parser import parse_kicad_pcb
        try:
            pcb = parse_kicad_pcb(args.board)
        except Exception as exc:                             # noqa: BLE001
            print(f'could not parse {args.board}: {type(exc).__name__}: {exc}',
                  file=sys.stderr)
            return 3
        doc = placement_terms(pcb, args.board, clearance=args.clearance,
                              intent=intent, length_groups=groups,
                              length_groups_source=(
                                  os.path.abspath(args.length_groups)
                                  if args.length_groups else None))
    text = json.dumps(doc, indent=2, sort_keys=True)
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as fh:
            fh.write(text + '\n')
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
