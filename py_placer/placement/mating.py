"""Which way a connector OPENS: its mating face, read off its drawn body (#1155).

Before this module the toolchain could say which edge a connector's courtyard
was NEAREST, and nothing else. A connector seated backwards, its shell lying
over the board and its mating face pointing inward, is still nearest the edge
it was declared on whenever the shell's back grazes that edge -- so
`rule_edge_connector`, board_context's "Mating face", check_assembly and
board_score all passed sonde_xilinx's reversed J2 (#1155).

The fact that decides it is where the BODY sits relative to the PAD ROW. A
horizontal or edge-mount connector solders at the back and extends forward to
the opening: a DSUB edge-mount's shell runs 14 mm past its pads, a USB-C
receptacle's 7 mm, a right-angle header's plastic 9 mm. So the mating face
is the direction of the body's centre from the pad bbox's centre, in the
footprint's own frame, mapped through the pose to a board edge.

It abstains -- `face is None`, with a reason -- whenever that reading is not
a fact:

* no pads (nothing for a body to be offset FROM);
* a board-copper plug (`floorplan.is_board_copper_plug`): the BOARD is the
  mating body, and its courtyard points inward by construction (StickHub J1);
* a `Vertical` footprint name: a vertical mount mates perpendicular to the
  board, so it has no in-plane face, however its plastic is drawn (a JST-XH
  vertical courtyard sits 0.5 mm off its pad row);
* no source offset by at least `FACE_MIN_OFFSET_MM` and `FACE_DOMINANCE`
  times its cross-axis offset (a symmetric part: a box header, a terminal);
* sources that disagree (courtyard says north, fab says south: glasgow SW1);
* a pose more than 30 degrees off every quarter turn (within 15 degrees
  of a diagonal).

Two sources vote: the courtyard (when that rung answered) and the drawn body
(`drawn_local`, fab else silk -- AFTER #1201's guard, so a pin-1 dot casts no
vote: unguarded, CM5_MINIMA_3's USB-C J101/USB701 read east off their dots;
guarded, their silk shells read west, which is right). Every decisive vote
must agree.

A face is STRONG when its offset reaches `FACE_STRONG_OFFSET_MM`, and only a
strong face may fail a declared edge at ERROR (`floorplan.edge_facing`); a
weaker one is reported at WARN. The phase verifier swept KiCad's stock
library: right on all 148 footprints that draw a "PCB Edge" marker, but
confidently WRONG on three receptacles whose PAD layout, not their housing,
sets the offset -- CUI SJ1-3533NG (x2, -x at 0.90 mm; its twin SJ1-3535NG,
same housing, reads -y) and Neutrik NMJ6HCD3 (-x at 1.36 mm; the opening is
+x) -- while 60+ correct micro-USB / USB-C faces sit in the same 0.6-1.6 mm
range. Above 2 mm no wrong face is known (227 checked). A weak face that is
itself wrong is NOT caught: when the seeder's stage 1 chooses an undeclared
connector's angle (on a pile, whose input angle says nothing), it tries the
angles that point even a weak face at the edge first, so such a jack can be
seated with its opening into the board, and the facing conjunct then reads
that pose as a pass. The "PCB Edge" marker those footprints draw is stronger
evidence than the offset; reading it is #1245.

Known limit: a card-edge PLUG in a file with no `attrs` (interf_u's ISA
BUS1: 62 netted SMD fingers, no model) is not recognised as one, and reads
inward (0.64 mm, weak). Its class is none, so it is graded only when an
author declares it `edge_mount` / `through_edge`.

The board frame uses the sign `connector_geometry.rect` and
`legality.rotate_local_bounds` use, with NO extra mirror for a B-side part:
the board file stores a B-side footprint's local coordinates already mirrored,
measured on sonde_u J2, orangecrab J4 and rp2350 J3.

Measured over the tracked corpus and the fa10 human boards
(`tests/measure_1155_mating_census.py`, fixture
`tests/fixtures/1155_mating_census.json`): every decisive receptacle faces
the edge it is seated on.
"""
import math
from typing import Dict, NamedTuple, Optional, Tuple

#: A body must sit at least this far (mm) off its pad row's centre to name a
#: face. The census's gap among receptacles is 0.085 (the most offset that
#: names none) to 0.59 (orangecrab J3's courtyard); connectors and actuators
#: reach 0.50 without a face. 0.57 rather than 0.55: two stock footprints
#: (microSD_HC_Molex_47219-2001, CM5's SW501) sit at exactly 0.55, where float
#: rounding would decide them.
FACE_MIN_OFFSET_MM = 0.57
#: ...and a face this far off is STRONG: the only kind that fails a declared
#: edge at ERROR. See the module docstring for the counterexamples below it.
FACE_STRONG_OFFSET_MM = 2.0
#: Both thresholds compare with this slack: the stock SJ1-3535NG's courtyard
#: offset is 2.0 mm on paper and 1.9999999999999996 in float.
_TOL_MM = 1e-6
#: ...and by this factor more on the face axis than across it. The lowest
#: ratio among faced receptacles is 2.02 (coldfire J201's courtyard).
FACE_DOMINANCE = 1.5
#: A pose more than this far off the nearest cardinal angle names no edge.
CARDINAL_TOL_DEG = 30.0

FACES = ('+x', '-x', '+y', '-y')
_UNIT = {'+x': (1.0, 0.0), '-x': (-1.0, 0.0),
         '+y': (0.0, 1.0), '-y': (0.0, -1.0)}


class LocalFace(NamedTuple):
    """A footprint's mating face in its own UNROTATED frame."""
    face: Optional[str]          # '+x' | '-x' | '+y' | '-y', or None
    offset_mm: float             # the decisive offset (largest vote), 0 if none
    basis: str                   # 'courtyard+fab', 'silk', ...; '' if abstained
    reason: str                  # why it abstained; '' when it did not
    votes: Tuple = ()            # ((source, face_or_None, ox, oy), ...)
    # offset_mm >= FACE_STRONG_OFFSET_MM: may fail a declared edge at ERROR.
    strong: bool = False


def _centre(b):
    return (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0


def _vote(body, pads):
    """`(face_or_None, ox, oy)` for one body bbox against the pad bbox."""
    bx, by = _centre(body)
    px, py = _centre(pads)
    ox, oy = bx - px, by - py
    major, minor = max(abs(ox), abs(oy)), min(abs(ox), abs(oy))
    if major < FACE_MIN_OFFSET_MM - _TOL_MM or major < FACE_DOMINANCE * minor:
        return None, ox, oy
    if abs(ox) >= abs(oy):
        return ('+x' if ox > 0 else '-x'), ox, oy
    return ('+y' if oy > 0 else '-y'), ox, oy


def is_vertical_name(fp) -> bool:
    """True when the footprint's ITEM name says it is a vertical mount.

    The item name, not the `lib:item` string: the text parser keeps the
    library prefix and the pcbnew path does not (`part_class` has the same
    rule for the same reason)."""
    name = str(getattr(fp, 'footprint_name', '') or '')
    item = name.rsplit(':', 1)[-1].lower()
    return 'vertical' in item and 'horizontal' not in item


def mating_face_local(fp, geom) -> LocalFace:
    """The mating face of one footprint, from its `body.BodyGeometry`."""
    if geom is None or getattr(geom, 'pads_local', None) is None:
        return LocalFace(None, 0.0, '', 'no pads to be offset from')
    if is_vertical_name(fp):
        return LocalFace(None, 0.0, '',
                         'a vertical mount (footprint name): it mates '
                         'perpendicular to the board')
    from .floorplan import is_board_copper_plug
    if is_board_copper_plug(fp):
        return LocalFace(None, 0.0, '',
                         'a board-copper plug: the board is the mating body')
    from .body import SOURCE_COURTYARD, SOURCE_FAB, SOURCE_SILK
    pads = geom.pads_local
    srcs = []
    if geom.source == SOURCE_COURTYARD and geom.body_local is not None:
        srcs.append(('courtyard', geom.body_local))
    if geom.drawn_source in (SOURCE_FAB, SOURCE_SILK) \
            and geom.drawn_local is not None:
        srcs.append((geom.drawn_source, geom.drawn_local))
    votes = tuple((name,) + _vote(b, pads) for name, b in srcs)
    decisive = [v for v in votes if v[1] is not None]
    if not decisive:
        return LocalFace(None, 0.0, '',
                         'symmetric about its pads' if votes
                         else 'no drawn body', votes)
    faces = sorted({v[1] for v in decisive})
    if len(faces) > 1:
        return LocalFace(None, 0.0, '', 'sources disagree: ' + ', '.join(
            '%s %s' % (v[0], v[1]) for v in decisive), votes)
    offset = max(max(abs(v[2]), abs(v[3])) for v in decisive)
    return LocalFace(faces[0], round(offset, 4),
                     '+'.join(v[0] for v in decisive), '', votes,
                     strong=offset >= FACE_STRONG_OFFSET_MM - _TOL_MM)


def facing_edge(face: Optional[str], rotation: float) -> Optional[str]:
    """The board edge (`west|north|east|south`) a local face points at.

    Board y grows southward. The transform is `connector_geometry.rect`'s:
    local (u, v) -> (cos r * u + sin r * v, -sin r * u + cos r * v)."""
    if face not in _UNIT:
        return None
    ux, uy = _UNIT[face]
    r = math.radians(rotation or 0.0)
    c, s = math.cos(r), math.sin(r)
    vx, vy = c * ux + s * uy, -s * ux + c * uy
    if max(abs(vx), abs(vy)) < math.cos(math.radians(CARDINAL_TOL_DEG)):
        return None
    if abs(vx) >= abs(vy):
        return 'east' if vx > 0 else 'west'
    return 'south' if vy > 0 else 'north'


def facing_rotations(face: Optional[str], edge: str) -> Tuple[float, ...]:
    """The quarter turns at which `face` points at `edge`, ascending."""
    return tuple(float(r) for r in (0, 90, 180, 270)
                 if facing_edge(face, r) == edge)


def board_faces(pcb_data, pcb_file: Optional[str] = None,
                bodies: Optional[Dict] = None) -> Dict[str, LocalFace]:
    """`{ref: LocalFace}` for every footprint `body.board_bodies` answers."""
    if bodies is None:
        from .body import board_bodies
        bodies = board_bodies(pcb_data, pcb_file)
    fps = getattr(pcb_data, 'footprints', None) or {}
    return {ref: mating_face_local(fps[ref], geom)
            for ref, geom in sorted(bodies.items()) if ref in fps}
