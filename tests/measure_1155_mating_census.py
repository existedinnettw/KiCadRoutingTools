#!/usr/bin/env python3
"""#1155 census: which way does every connector open, and is that the edge it sits on?

`mating.mating_face_local` names a connector's mating face from its drawn
body's offset off its pad row. This census runs it over every tracked board and
the fa10 designer boards, through the production readers, and answers the
three questions its constants and its gate rest on:

1. **Population.** Every part `part_class` calls an edge receptacle, an edge
   actuator or a connector: each source's offset (courtyard, drawn body), the
   verdict, the board edge it faces at its pose, and the edge it is nearest.
2. **The gate.** Every edge the emitter observes on the board
   (`emit_intent(declare_classes=True)`, the widest population), read as it
   reaches `_withhold_reversed_edge` -- BEFORE that pass can drop it, so a
   strong failure is counted rather than withheld out of sight. A decisive
   face that disagrees with the edge on a DESIGNER's board is listed; one the
   rule's own predicate (`floorplan.edge_facing`) FAILS is a false positive of
   the facing conjunct. The gate is that there are none, and that the emitted
   intent carries no entry the pass withheld (`suspect`, #1155).
3. **The threshold gap**, per class: the most offset vote that names no face
   against the least offset decisive vote. `FACE_MIN_OFFSET_MM` must sit
   inside the GRADED population's gap (receptacles), and should clear the
   others' noise too, since board_context prints every part's face.

With `--write-fixture`, the tracked boards' per-part verdicts are written to
`tests/fixtures/1155_mating_census.json`, which `test_1155_mating_face`
compares against (a change detector: a move is re-recorded on purpose, not
absorbed).

    python3 -X utf8 tests/measure_1155_mating_census.py [--no-fa10] [--write-fixture] [--json OUT]

Exits 1 when the gate fails. The fa10 boards are read in place when present
and never asserted on by a test. Before #1201's fab guard exists the guard is
simulated (`measure_1201_fab_and_silk.guard`), so a pin-1 dot does not vote
here either. A measurement tool; `run_all` does not collect `measure_*`.
"""
import contextlib
import json
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, 'py_router'),
           os.path.join(ROOT_DIR, 'py_placer'), TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from kicad_parser import parse_kicad_pcb  # noqa: E402
from run_utils import corpus_boards  # noqa: E402
from measure_1201_fab_and_silk import FA10_HUMAN, guard  # noqa: E402
from placement import floorplan, mating  # noqa: E402
from placement.body import board_bodies  # noqa: E402
from placement.legality import rotate_local_bounds  # noqa: E402
from placement.part_class import classify_part  # noqa: E402

FIXTURE = os.path.join(TESTS_DIR, 'fixtures', '1155_mating_census.json')
POPULATION = ('edge_receptacle', 'edge_actuator', 'connector_affinity')
_SHORT = {'west': 'W', 'north': 'N', 'east': 'E', 'south': 'S', None: '-'}


def _board_rect(fp, local):
    x0, y0, x1, y1 = rotate_local_bounds(*local, fp.rotation or 0.0)
    return (fp.x + x0, fp.y + y0, fp.x + x1, fp.y + y1)


def census(pcb, path, emit=True):
    """`(parts, entries, emit_error)` for one board. `emit=False` skips the
    emitter (the slow half) and returns no entries -- what the fixture test
    needs."""
    bodies = board_bodies(pcb, path)
    faces = mating.board_faces(pcb, path, bodies=bodies)
    bounds = pcb.board_info.board_bounds
    parts = {}
    for ref, fp in sorted(pcb.footprints.items()):
        cls = classify_part(fp, ref).name
        if cls not in POPULATION or ref not in faces:
            continue
        lf = faces[ref]
        geom = bodies[ref]
        near = (floorplan._nearest_edge(_board_rect(fp, geom.occupancy_local),
                                        bounds)
                if bounds and geom.occupancy_local else None)
        parts[ref] = {
            'class': cls, 'footprint': fp.footprint_name,
            'face': lf.face, 'basis': lf.basis, 'offset_mm': lf.offset_mm,
            'strong': lf.strong, 'reason': lf.reason,
            'facing': mating.facing_edge(lf.face, fp.rotation or 0.0),
            'nearest': near,
            'votes': [[v[0], v[1], round(v[2], 4), round(v[3], 4)]
                      for v in lf.votes]}
    entries = []
    if not emit:
        return parts, entries, None
    seen = []
    try:
        with _observed_edges(seen):
            intent = floorplan.emit_intent(pcb, path, declare_classes=True)
    except Exception as exc:                                 # noqa: BLE001
        intent = {'edge_connectors': [], '_error': repr(exc)}
    err = intent.get('_error')
    emitted = intent.get('edge_connectors') or []
    if emitted and not seen and not err:
        # A gate that saw nothing would read as one that found nothing.
        err = ('the emitter wrote edge_connectors but never reached '
               '_withhold_reversed_edge: nothing was measured')
    withheld = {e.get('ref') for e in emitted
                if e.get('suspect')
                and '#1155' in str(e.get('suspect_reason') or '')}
    for e in seen:
        ref, edge = e.get('ref'), e.get('edge')
        fp = pcb.footprints.get(ref)
        if fp is None or not edge:
            continue
        # The rule's own predicate -- called, not mirrored -- on the edge the
        # emitter observed, before the withhold pass could drop it.
        ef = floorplan.edge_facing(e, fp, bodies.get(ref))
        cls = e.get('class') or classify_part(fp, ref).name
        entries.append({'ref': ref, 'edge': edge, 'class': cls,
                        'mount_mode': (e.get('context') or {}).get(
                            'mount_mode'),
                        'face': ef['local_face'], 'facing': ef['facing'],
                        'disagrees': bool(ef['facing']
                                          and ef['facing'] != edge),
                        'policy': ef['policy'], 'fails': ef['fails'],
                        'strong': ef['strong'],
                        'withheld': ref in withheld})
    return parts, entries, err


@contextlib.contextmanager
def _observed_edges(seen):
    """Record a copy of every entry `emit_intent` hands
    `floorplan._withhold_reversed_edge`, as it arrives -- before that pass
    can drop its edge -- and let the pass run as it would."""
    orig = floorplan._withhold_reversed_edge

    def spy(entry, *args, **kwargs):
        seen.append(dict(entry))
        return orig(entry, *args, **kwargs)
    floorplan._withhold_reversed_edge = spy
    try:
        yield
    finally:
        floorplan._withhold_reversed_edge = orig


def main(argv):
    out_json = None
    if '--json' in argv:
        i = argv.index('--json')
        out_json = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    write = '--write-fixture' in argv
    fa10 = '--no-fa10' not in argv
    tracked = list(corpus_boards())
    if not tracked:
        print('NOT RUN: git cannot list the tracked boards')
        return 2
    human = [b for b in FA10_HUMAN if fa10 and os.path.isfile(b)]
    rows = [('tracked', b) for b in tracked] + [('fa10', b) for b in human]
    fixture, report = {}, []
    disagree, fails, withheld, unmeasured = [], [], [], []
    gap = {}            # class -> [max abstained vote, min decisive vote]
    with guard(True) as how:
        for kind, path in rows:
            name = (os.path.basename(os.path.dirname(path)) + '/human'
                    if kind == 'fa10' else os.path.basename(path))
            pcb = parse_kicad_pcb(path)
            parts, entries, err = census(pcb, path)
            report.append({'board': name, 'kind': kind, 'parts': parts,
                           'entries': entries, 'emit_error': err})
            if kind == 'tracked':
                fixture[name] = {r: [p['face'], p['basis'], p['facing'],
                                     p['strong']]
                                 for r, p in parts.items()}
            for r, p in parts.items():
                g = gap.setdefault(p['class'], [None, None])
                for src, face, ox, oy in p['votes']:
                    mag = max(abs(ox), abs(oy))
                    if face is None and not (p['reason'] or '').startswith(
                            ('a vertical', 'a board-copper')):
                        if g[0] is None or mag > g[0][0]:
                            g[0] = (mag, name, r, src)
                    elif face is not None and p['face'] is not None:
                        if g[1] is None or mag < g[1][0]:
                            g[1] = (mag, name, r, src)
            if err:
                unmeasured.append((kind, name, err))
            for e in entries:
                if e['disagrees']:
                    disagree.append((kind, name, e))
                if e['fails']:
                    fails.append((kind, name, e))
                if e['withheld']:
                    withheld.append((kind, name, e))
            faced = sum(1 for p in parts.values() if p['face'])
            print('%-34s %-7s parts=%3d faced=%3d edges=%2d fails=%d '
                  'withheld=%d%s'
                  % (name[:34], kind, len(parts), faced, len(entries),
                     sum(1 for e in entries if e['fails']),
                     sum(1 for e in entries if e['withheld']),
                     ('  EMIT ERROR ' + err) if err else ''))
            for e in entries:
                if not (e['face'] or e['fails']):
                    continue
                print('    %-10s edge %-5s class %-18s mount %-12s face %-3s '
                      'facing %s  %s%s' % (
                          e['ref'], e['edge'], e['class'],
                          e['mount_mode'] or '-', e['face'] or '-',
                          _SHORT[e['facing']], e['policy'],
                          ('   <-- FAILS%s' % (
                              ' (strong: withheld)' if e['withheld']
                              else ' (strong)' if e['strong'] else ' (weak)')
                           if e['fails'] else
                           '   <-- disagrees (not graded)'
                           if e['disagrees'] else '')))
    print()
    print('fab guard: %s' % how)
    print('threshold FACE_MIN_OFFSET_MM = %.3f, FACE_DOMINANCE = %.2f, '
          'FACE_STRONG_OFFSET_MM = %.2f' % (
              mating.FACE_MIN_OFFSET_MM, mating.FACE_DOMINANCE,
              mating.FACE_STRONG_OFFSET_MM))
    for cls, (ab, de) in sorted(gap.items()):
        print('  %-18s most offset vote naming no face: %s' % (
            cls, '%.4f (%s %s %s)' % ab if ab else '-'))
        print('  %-18s least offset decisive vote:      %s' % (
            '', '%.4f (%s %s %s)' % de if de else '-'))
    print('emitted entries whose decisive face disagrees with their edge: %d'
          % len(disagree))
    for kind, name, e in disagree:
        print('  %-7s %-30s %s edge %s class %s face %s facing %s  %s' % (
            kind, name, e['ref'], e['edge'], e['class'], e['face'],
            e['facing'], e['policy']))
    print('GATE -- observed edges the facing conjunct FAILS on these boards: '
          '%d (%d strong)' % (len(fails),
                              sum(1 for _k, _n, e in fails if e['strong'])))
    for kind, name, e in fails:
        print('  %-7s %-30s %s' % (kind, name, e['ref']))
    print('GATE -- edges the emitter withheld as suspect (#1155): %d'
          % len(withheld))
    for kind, name, e in withheld:
        print('  %-7s %-30s %s' % (kind, name, e['ref']))
    print('GATE -- boards the census could not measure: %d' % len(unmeasured))
    for kind, name, err in unmeasured:
        print('  %-7s %-30s %s' % (kind, name, err))
    if write:
        with open(FIXTURE, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(fixture, fh, indent=1, sort_keys=True)
            fh.write('\n')
        print('wrote', FIXTURE)
    if out_json:
        with open(out_json, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, indent=1)
    return 1 if (fails or withheld or unmeasured) else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
