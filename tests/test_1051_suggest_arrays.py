#!/usr/bin/env python3
"""#1051 phase 2: the array DETECTOR -- it suggests, it is pose-blind, and
what it suggests on the real boards is pinned against their real nets.

What each case pins, and why:

* glasgow_revC: the 33R resistor arrays split into PAIRS by channel -- RN7+
  RN8 (IO_Buffer_A) and RN1+RN2 (IO_Buffer_B) each on four balls of U30,
  RN9+RN10 / RN3+RN4 on connectors J2 / J3. The issue's "RN1-4 & RN7-10
  serving U30" is NOT what the nets say: RN3/4/9/10 reach U30 only through
  the SN74LVC1T45 buffers. The 17 buffers meet `pin_run` on U30 (pin 5, the
  DAx/DBx bus) as 8 + 8 by sheet; U32 is the 17th, alone on the top sheet
  on a different net, and is suggested nowhere.
* splitflap: the seven 47k resistors, each on its own pin of U4, in U4's
  pin order (pads 3, 4, 10, 11, 12, 13, 14).
* ulx3s: the 549R resistors on U1 split by sheet into the eight LED
  resistors (blinkey), three analog and two GPDI -- 13 on U1, not the 17 a
  count of "shares any net with U1" gives (that count includes +3V3/GND).
* a negative control that cannot pass vacuously: the same synthetic parts
  give no suggestion without a common part, and one WITH it.
* pose-blindness: identical output after every footprint pose and pad
  position is randomised in memory -- and `groups.chip_refs`, which DOES read
  poses, changes on the same randomised board, so the shuffle was strong
  enough to move a pose-reading predicate.
* `array_formation` on the HUMAN glasgow for the detected rows (count
  reported), every resistor-array row formed, and one shuffled member fails.
* `emit_intent`: the default writes no `arrays`; `'auto'` writes them with a
  `why` naming the criterion and no evidence bulk, and round-trips through
  `load_intent` and `grade` with no array_unresolved / array_conflict.
* the CLI: `--suggest-arrays` stdout is bare JSON equal to the in-process
  answer, and it refuses to run with `--intent`, for its stated reason.

    python3 tests/test_1051_suggest_arrays.py
"""
import copy
import json
import os
import random
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ('py_router', 'py_placer', 'py_tools'):
    sys.path.insert(0, os.path.join(ROOT, _d))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kicad_parser import parse_kicad_pcb           # noqa: E402
from placement import arrays as arr                # noqa: E402
from placement import floorplan as fp              # noqa: E402
from placement import groups as groups_mod         # noqa: E402
from run_utils import check                        # noqa: E402

RUN_ALL_TIMEOUT = 900

GLASGOW = os.path.join(ROOT, 'kicad_files', 'glasgow_revC.kicad_pcb')
SPLITFLAP = os.path.join(ROOT, 'kicad_files', 'splitflap_driver.kicad_pcb')
ULX3S = os.path.join(ROOT, 'kicad_files', 'ulx3s.kicad_pcb')
CHECK_FLOORPLAN = os.path.join(ROOT, 'py_tools', 'check_floorplan.py')

BUFFERS_A = ['U4', 'U6', 'U9', 'U10', 'U11', 'U16', 'U17', 'U18']
BUFFERS_B = ['U22', 'U23', 'U24', 'U25', 'U26', 'U27', 'U28', 'U29']

_PCB = {}
_SUG = {}


def _pcb(path):
    if path not in _PCB:
        _PCB[path] = parse_kicad_pcb(path)
    return _PCB[path]


def _sug(path):
    if path not in _SUG:
        _SUG[path] = arr.suggest_arrays(_pcb(path))
    return _SUG[path]


def _by_members(cands, members):
    want = set(members)
    hit = [c for c in cands if set(c['members']) == want]
    assert len(hit) == 1, (f"expected one candidate with members "
                           f"{sorted(want)}, got {len(hit)}: "
                           f"{[c['members'] for c in cands]}")
    return hit[0]


def _no_part_twice(cands):
    seen = {}
    for c in cands:
        for m in c['members']:
            assert m not in seen, (f"{m} is in {seen[m]!r} AND "
                                   f"{c['name']!r}: a part is in at most "
                                   f"one candidate")
            seen[m] = c['name']
    names = [c['name'] for c in cands]
    assert len(names) == len(set(names)), f"duplicate names: {names}"


def _shape_ok(c):
    for k in ('name', 'members', 'serves', 'order', 'rotation', 'pitch_mm',
              'axis', 'criterion', 'evidence'):
        assert k in c, f"{c.get('name')}: missing key {k!r}"
    assert c['criterion'] in arr.CRITERIA
    assert c['order'] in arr.ORDERS
    assert (c['rotation'], c['pitch_mm'], c['axis']) == ('shared', 'auto',
                                                         'auto')
    assert len(c['members']) >= arr.MIN_MEMBERS


def test_glasgow_resistor_arrays_and_buffers():
    cands = _sug(GLASGOW)
    for c in cands:
        _shape_ok(c)
    _no_part_twice(cands)

    # The two resistor arrays per channel that reach U30 directly.
    b = _by_members(cands, ['RN1', 'RN2'])
    a = _by_members(cands, ['RN7', 'RN8'])
    for c, sheet_nets in ((b, ('/IO_Banks/QB0', '/IO_Banks/QB7')),
                          (a, ('/IO_Banks/QA0', '/IO_Banks/QA7'))):
        assert c['criterion'] == 'pin_run' and c['serves'] == 'U30', c
        # U30 is a BGA: its ball names do not order a row (GRID_ORDER_NOTE).
        assert c['order'] == 'unknown', c['order']
        assert c['evidence']['host_pads'] == 'grid'
        nets = {ln['net'] for m in c['members']
                for ln in c['evidence']['links'][m]}
        assert set(sheet_nets) <= nets and len(nets) == 8, sorted(nets)
    assert b['evidence']['links']['RN1'] == [
        {'net': '/IO_Banks/QB0', 'pad': 'B11', 'member_pad': '1'},
        {'net': '/IO_Banks/QB1', 'pad': 'C11', 'member_pad': '2'},
        {'net': '/IO_Banks/QB2', 'pad': 'D10', 'member_pad': '3'},
        {'net': '/IO_Banks/QB3', 'pad': 'D11', 'member_pad': '4'}], \
        b['evidence']['links']['RN1']

    # The other two 33R arrays per channel reach U30 only THROUGH the
    # buffers; the part they each land on directly is a connector.
    for members, host in ((['RN3', 'RN4'], 'J3'), (['RN9', 'RN10'], 'J2')):
        c = _by_members(cands, members)
        assert c['criterion'] == 'pin_run' and c['serves'] == host, c
        assert c['order'] == 'pin' and c['evidence']['host_pads'] == \
            'numbered'

    # The 17 SN74LVC1T45: 8 + 8 on U30 by channel, and each bank BRIDGES
    # U30 and the channel's R-array (phase-3 fix round 1): a multi-pad run
    # whose every member also reaches a second part at the host floor by
    # another net. Declined, with the reason, never suggested -- a single
    # row at U30 measured worse on glasgow, and humans lay these as 2x4
    # blocks, which `arrays[]` cannot express. U32 nowhere.
    declined = []
    arr.suggest_arrays(_pcb(GLASGOW), declined=declined)
    for bank, far in ((BUFFERS_A, 'RN7'), (BUFFERS_B, 'RN1')):
        assert not any(sorted(c['members']) == sorted(bank) for c in cands)
        hit = [d for d in declined if sorted(d['members']) == sorted(bank)]
        assert len(hit) == 1, hit
        assert hit[0]['why'] == f"bridges U30 and {far}", hit[0]
        assert hit[0]['serves'] == 'U30' and hit[0]['criterion'] == 'pin_run'
    # The other bridges on this board, pinned deliberately: the 10k arrays
    # between a connector and a level shifter.
    bridges = sorted((d['why'], tuple(sorted(d['members'])))
                     for d in declined if d['why'].startswith('bridges'))
    assert bridges == sorted([
        ('bridges J2 and U19', ('RN11', 'RN12')),
        ('bridges J3 and U5', ('RN5', 'RN6')),
        ('bridges U30 and RN1', tuple(sorted(BUFFERS_B))),
        ('bridges U30 and RN7', tuple(sorted(BUFFERS_A)))]), bridges
    assert not any('U32' in c['members'] for c in cands), \
        "U32 is the lone buffer on the top sheet and is no row's member"
    # Nothing without a net (fiducials) nor on shared nets only (mounting
    # holes: GND) is ever a member.
    fps = _pcb(GLASGOW).footprints
    for c in cands:
        for m in c['members']:
            assert 'Fiducial' not in fps[m].footprint_name
            assert 'MountingHole' not in fps[m].footprint_name
    print(f"  PASS: glasgow {len(cands)} suggestions; RN1+RN2 and RN7+RN8 "
          f"on U30, RN3+RN4 on J3, RN9+RN10 on J2, buffers 8+8 DECLINED as "
          f"bridges of U30, U32 none")


def test_splitflap_pullups_in_u4_pin_order():
    c = _by_members(_sug(SPLITFLAP), ['R6', 'R7', 'R8', 'R9', 'R11', 'R12',
                                      'R14'])
    assert c['criterion'] == 'pin_run' and c['serves'] == 'U4', c
    assert c['order'] == 'pin'
    assert c['members'] == ['R11', 'R12', 'R14', 'R6', 'R7', 'R8', 'R9'], \
        c['members']
    pads = [c['evidence']['links'][m][0]['pad'] for m in c['members']]
    assert pads == ['3', '4', '10', '11', '12', '13', '14'], pads
    # R14 is a pull-DOWN: its GND is excluded as a rail, its signal decides.
    assert c['evidence']['links']['R14'] == [
        {'net': '/SENSOR_IN', 'pad': '10', 'member_pad': '1'}]
    # The members ARE the grader's pin order, so a suggestion and its grade
    # cannot disagree about it.
    assert arr.pin_order(_pcb(SPLITFLAP), 'U4', c['members'])[0] == \
        c['members']
    _no_part_twice(_sug(SPLITFLAP))
    print("  PASS: splitflap 47k x7 -> U4 pads 3,4,10..14 in pin order")


def test_ulx3s_549r_on_u1():
    cands = _sug(ULX3S)
    _no_part_twice(cands)
    led = _by_members(cands, ['R41', 'R42', 'R43', 'R44', 'R45', 'R46',
                              'R47', 'R48'])
    analog = _by_members(cands, ['R15', 'R19', 'R58'])
    gpdi = _by_members(cands, ['R61', 'R67'])
    on_u1 = []
    for c in (led, analog, gpdi):
        assert c['criterion'] == 'pin_run' and c['serves'] == 'U1', c
        assert c['evidence']['value'] == '549'
        on_u1 += c['members']
    assert len(on_u1) == 13
    all549 = {m for c in cands if c['evidence']['value'] == '549'
              for m in c['members']}
    assert all549 == set(on_u1), sorted(all549 - set(on_u1))
    # R36's only net no other 549R has is GND: excluded as a rail, so it is
    # not a "member" of U1's LED bank.
    assert 'R36' not in all549
    print(f"  PASS: ulx3s 549R -> U1 as 8 (blinkey) + 3 (analog) + 2 (gpdi) "
          f"= 13; {len(cands)} suggestions in all")


def _synthetic(with_host: bool):
    """Three identical 10k resistors, each between two private nets. The
    far ends reach three DIFFERENT two-pad parts, so there is no shared
    part and no repeated shape; `with_host` lands each one's first pad on
    its own pin of one 8-pin IC instead."""
    nets, fps = {}, {}

    def pad(ref, num, net_id, x=0.0):
        p = SimpleNamespace(pad_number=str(num), net_id=net_id,
                            component_ref=ref, layers=['F.Cu'],
                            local_x=x, local_y=float(num), global_x=x,
                            global_y=float(num), pintype='', pinfunction='')
        if net_id:
            nets.setdefault(net_id, SimpleNamespace(
                name=f'/N{net_id}', pads=[])).pads.append(p)
        return p

    def part(ref, fpname, value, pads):
        fps[ref] = SimpleNamespace(reference=ref, footprint_name=fpname,
                                   value=value, sheet_path='/root/' + ref,
                                   pads=pads, locked=False, x=0.0, y=0.0,
                                   rotation=0.0)

    others = [('C9', 'C_0402', '1u'), ('D9', 'D_SOD323', 'BAT'),
              ('L9', 'L_0603', '1uH')]
    for i, (oref, ofp, oval) in enumerate(others):
        a, b = 10 + 2 * i, 11 + 2 * i
        part(f'R{i + 1}', 'R_0402', '10k', [pad(f'R{i + 1}', 1, a),
                                            pad(f'R{i + 1}', 2, b)])
        # Each R's far end on a different kind of part: no repeated shape.
        part(oref, ofp, oval, [pad(oref, 1, b), pad(oref, 2, 0)])
        if not with_host:
            part(f'X{i}', f'SOT23_{i}', f'Q{i}',
                 [pad(f'X{i}', 1, a), pad(f'X{i}', 2, 0)])
    if with_host:
        part('U1', 'SOIC-8', 'MCU',
             [pad('U1', n, 10 + 2 * (n - 1) if n <= 3 else 0,
                  x=(0.0 if n <= 4 else 5.0)) for n in range(1, 9)])
    return SimpleNamespace(footprints=fps, nets=nets)


def test_negative_control_no_common_part_no_suggestion():
    neg = arr.suggest_arrays(_synthetic(False))
    assert neg == [], f"no common part, no repeated shape -> none: {neg}"
    pos = arr.suggest_arrays(_synthetic(True))
    assert len(pos) == 1 and pos[0]['serves'] == 'U1' and \
        pos[0]['members'] == ['R1', 'R2', 'R3'], pos
    print("  PASS: the same three 10k give nothing without U1, one "
          "pin_run with it (the control is not vacuous)")


def _randomise(pcb, seed):
    rng = random.Random(seed)
    for f in pcb.footprints.values():
        f.x = rng.uniform(-500, 500)
        f.y = rng.uniform(-500, 500)
        f.rotation = rng.uniform(0, 360)
        for p in f.pads:
            p.global_x = rng.uniform(-500, 500)
            p.global_y = rng.uniform(-500, 500)
            if hasattr(p, 'rect_rotation'):
                p.rect_rotation = rng.uniform(-89, 89)
    return pcb


def test_pose_blind():
    for path in (GLASGOW, SPLITFLAP, ULX3S):
        base = _sug(path)
        shuffled = _randomise(copy.deepcopy(_pcb(path)), 1051)
        # The shuffle is strong enough to move a predicate that DOES read
        # poses: `chip_refs`' row test reads global pad coordinates.
        assert groups_mod.chip_refs(shuffled) != groups_mod.chip_refs(
            _pcb(path)), f"{path}: the shuffle moved nothing pose-read"
        assert arr.pose_free_chip_refs(shuffled) == \
            arr.pose_free_chip_refs(_pcb(path))
        got = arr.suggest_arrays(shuffled)
        assert json.dumps(got, sort_keys=True) == json.dumps(
            base, sort_keys=True), f"{os.path.basename(path)}: suggestions " \
            f"moved with the poses"
    print("  PASS: identical suggestions on 3 boards with every pose "
          "randomised (and chip_refs, which reads poses, did change)")


def _grade_auto(pcb, path):
    doc = fp.emit_intent(pcb, path, derive_arrays='auto')
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'intent.json')
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh)
        intent = fp.load_intent(p)
    return doc, intent, fp.grade(intent, pcb, path)


def test_formation_on_the_human_glasgow():
    """The human rows, graded by the ONE predicate at DEFAULT_TOLERANCES.

    Measured here (and why no tolerance moved): of the 20 rows the auto
    intent declares, 12 are formed (14 of 24 before phase-3 fix round 1
    declined the two buffer banks and the two 10k arrays as BRIDGES --
    the 10k pairs were formed rows, the banks were not; 16 of 27 before
    fix round 1 stopped
    socket pairs J6+J7 / J8+J9 being members -- both were formed rows --
    and dropped bank:100R, whose own nets are supply rails; 15 of 27
    before two-pad parts compared their rotation modulo 180). Every
    failure is a real non-row: the
    smallest failing axis offset is 0.65mm (J1:5k1, R52/R53), the rest
    fail on 90-degree rotation splits or multi-mm pitch gaps, and the
    buffer banks -- no longer suggested -- are 2x4 GRIDS (two columns
    4.0mm apart) that a row schema cannot express. No failure is within a
    tolerance of passing.
    """
    pcb = _pcb(GLASGOW)
    doc, intent, res = _grade_auto(pcb, GLASGOW)
    meas = {m['name']: m for m in res.array_measured}
    assert len(meas) == len(doc['arrays']) == 20, len(meas)
    formed = sorted(n for n, m in meas.items() if m.get('formed'))
    print(f"  human glasgow: {len(formed)} of {len(meas)} detected rows "
          f"formed")
    assert len(formed) >= 12, formed
    by = {tuple(sorted(a['members'])): a['name'] for a in doc['arrays']}
    for pair in (('RN1', 'RN2'), ('RN3', 'RN4'), ('RN7', 'RN8'),
                 ('RN10', 'RN9')):
        assert meas[by[pair]]['formed'] is True, (pair, meas[by[pair]])
    for bank in (BUFFERS_A, BUFFERS_B):
        assert tuple(sorted(bank)) not in by, bank      # declined bridges

    # One member shuffled: the row it belonged to fails, on the axis.
    moved = copy.deepcopy(pcb)
    f = moved.footprints['RN2']
    f.x += 1.5
    for p in f.pads:
        p.global_x += 1.5
    res2 = fp.grade(intent, moved, GLASGOW)
    m2 = {m['name']: m for m in res2.array_measured}[by[('RN1', 'RN2')]]
    assert m2['formed'] is False and 'axis' in m2['failed'], m2
    assert any(v.rule == 'array_formation' and v.block == by[('RN1', 'RN2')]
               for v in res2.violations)
    print(f"  PASS: every R-array row formed on the human board; RN2 moved "
          f"1.5mm -> {by[('RN1', 'RN2')]!r} fails {m2['failed']}")


def test_emit_intent_default_unchanged_and_auto_round_trips():
    pcb = _pcb(SPLITFLAP)
    d0 = fp.emit_intent(pcb, SPLITFLAP)
    d1 = fp.emit_intent(pcb, SPLITFLAP, derive_arrays='off')
    assert json.dumps(d0, sort_keys=True) == json.dumps(d1, sort_keys=True)
    assert 'arrays' not in d0 and 'arrays_note' not in d0['context']
    try:
        fp.emit_intent(pcb, SPLITFLAP, derive_arrays='on')
    except ValueError as exc:
        assert "expected 'off' or 'auto'" in str(exc), exc
    else:
        raise AssertionError("derive_arrays='on' must refuse")

    doc, intent, res = _grade_auto(pcb, SPLITFLAP)
    assert doc['min_reader'] >= 7
    assert doc['arrays'], 'auto emitted no arrays on splitflap'
    for a in doc['arrays']:
        assert 'evidence' not in a and 'criterion' not in a, a
        assert a['why'].startswith('detector ') and any(
            k in a['why'] for k in arr.CRITERIA), a['why']
    # Connectors are never members now (`not_a_member`), so splitflap's
    # motor/sensor headers no longer reach the emitter's drop step at all.
    dropped = {d['name']: d for d in doc['context'].get('arrays_dropped')
               or ()}
    assert not dropped, dropped
    edge = {c['ref'] for c in doc['edge_connectors']}
    assert not any(m in edge for a in doc['arrays'] for m in a['members'])
    assert len(intent.arrays) == len(doc['arrays'])
    bad = [(v.rule, v.ref, v.message) for v in res.violations
           if v.rule in ('array_unresolved', 'array_conflict')]
    assert not bad, bad
    assert {m['name'] for m in res.array_measured} == \
        {a['name'] for a in doc['arrays']}
    print(f"  PASS: default emits no arrays; auto emits "
          f"{len(doc['arrays'])}, loads and grades with no "
          f"array_unresolved/conflict")


def test_every_suggestion_row_loads_as_pasted():
    """Phase-5 fact-check: the skill tells a reader to ACCEPT a suggestion
    into `arrays[]`, and a whole suggestion pasted there exits 2 --
    `unknown key(s) criterion, evidence`. Each suggestion now carries a
    paste-ready `row`: exactly the intent's keys, `why` pre-filled. Every
    row of every board here must load through the intent loader, as pasted,
    all of a board's rows together -- and the whole suggestion must NOT, or
    this test stops proving the row is what makes it load."""
    loaded = 0
    for path in (GLASGOW, SPLITFLAP, ULX3S):
        cands = _sug(path)
        assert cands, f"{path}: no suggestions -- the arm tests nothing"
        rows = []
        for c in cands:
            row = c['row']
            assert set(row) <= fp._ARRAY_KEYS, (c['name'], sorted(row))
            assert 'criterion' not in row and 'evidence' not in row, row
            assert row['why'] == arr.suggestion_why(c), row
            assert row['members'] == c['members'], row
            assert row.get('serves') == (c['serves'] or None), row
            rows.append(row)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'intent.json')

            def _load(arrays):
                with open(p, 'w', encoding='utf-8') as fh:
                    json.dump({'schema': 1, 'kind': 'floorplan-intent',
                               'units': 'mm', 'min_reader': 7,
                               'arrays': arrays}, fh)
                return fp.load_intent(p)
            intent = _load(rows)
            assert len(intent.arrays) == len(rows), path
            loaded += len(rows)
            whole = {k: v for k, v in cands[0].items() if k != 'row'}
            try:
                _load([whole])
            except fp.IntentError as exc:
                assert 'criterion' in str(exc) or 'evidence' in str(exc), exc
            else:
                raise AssertionError('a whole suggestion loaded as an '
                                     'arrays[] entry; the row proves '
                                     'nothing')
    # The CLI's document carries the same rows.
    r = subprocess.run([sys.executable, '-X', 'utf8', CHECK_FLOORPLAN,
                        SPLITFLAP, '--suggest-arrays'], capture_output=True,
                       text=True, encoding='utf-8', cwd=ROOT, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    doc = json.loads(r.stdout)
    assert [s['row'] for s in doc['suggestions']] == \
        [c['row'] for c in _sug(SPLITFLAP)]
    print(f"  PASS: {loaded} suggestion rows on 3 boards load through the "
          f"intent loader as pasted; the whole suggestion does not")


WATCHY = os.path.join(ROOT, 'kicad_files', 'watchy.kicad_pcb')
COLDFIRE = os.path.join(ROOT, 'kicad_files',
                        'kit-dev-coldfire-xilinx_5213.kicad_pcb')
BOARDS = (GLASGOW, SPLITFLAP, ULX3S, COLDFIRE, WATCHY)


def test_watchy_edge_claims_on_passives_yield_to_the_row():
    """Fix-round 1, item 6: R4/R8/R11 (100K) overhang watchy's curved edge,
    so the emitter INFERS them edge connectors; they used to be dropped
    from `U4:100K` for it. The row keeps them and the inferred edge claim
    goes, disclosed with its basis keys."""
    pcb = _pcb(WATCHY)
    doc, intent, res = _grade_auto(pcb, WATCHY)
    row = [a for a in doc['arrays'] if a['name'] == 'U4:100K']
    assert row and {'R4', 'R8', 'R11'} <= set(row[0]['members']), row
    rel = {r['ref']: r for r in doc['context']['arrays_edge_released']}
    assert {'R4', 'R8', 'R11'} <= set(rel), rel
    assert all(r['array'] and r['why'] for r in rel.values())
    edge = {c['ref'] for c in doc['edge_connectors']}
    assert not (set(rel) & edge), set(rel) & edge
    assert not [k for k in doc['context']['basis']
                if any(k.startswith(f"edge_connectors[{r}].") for r in rel)]
    # The default emission still carries the inferred claims unchanged.
    base = {c['ref'] for c in fp.emit_intent(pcb, WATCHY)['edge_connectors']}
    assert {'R4', 'R8', 'R11'} <= base
    bad = [(v.rule, v.ref) for v in res.violations
           if v.rule in ('array_unresolved', 'array_conflict')]
    assert not bad, bad
    print(f"  PASS: watchy U4:100K keeps R4/R8/R11; {len(rel)} inferred "
          f"edge claim(s) released and disclosed")


def test_no_part_is_host_and_member_and_no_big_ic_member():
    """Fix-round 1, item 1. Before: glasgow RN5 hosted J6+J7 AND was a
    member of `J3:10k`; splitflap's TPL7407L/74HC595 (16 pads) were rows
    of each other on one chain net."""
    for path in BOARDS:
        cands = _sug(path)
        _no_part_twice(cands)
        hosts = {c['serves'] for c in cands if c['serves']}
        members = {m for c in cands for m in c['members']}
        assert not hosts & members, (os.path.basename(path),
                                     sorted(hosts & members))
        fps = _pcb(path).footprints
        for c in cands:
            if c['serves']:
                h = arr._copper_pad_count(fps[c['serves']])
                m = arr._copper_pad_count(fps[c['members'][0]])
                assert h >= arr.MIN_HOST_TO_MEMBER_PADS * m, (c['name'], h, m)
    chips = {'U1', 'U2', 'U5', 'U6', 'U7', 'U8', 'U9', 'U10'}
    assert not chips & {m for c in _sug(SPLITFLAP) for m in c['members']}
    # The precedence rule itself, on a synthetic board where it binds.
    # (1) equal precedence: M1 hosts K1+K2 and is a member of H's row ->
    # the K row goes. (2) lower precedence: X1+X2 are a sheet_bank and X1
    # hosts C1+C2 -> the bank goes.
    dec = []
    got = arr.suggest_arrays(_chain_board(), declined=dec)
    rows = {c['name']: c['members'] for c in got}
    assert rows.get('H:RA') == ['M1', 'M2'], rows
    assert rows.get('X1:1n') == ['C1', 'C2'], rows
    assert not any(c['serves'] == 'M1' for c in got), rows
    assert not any('X1' in c['members'] for c in got), rows
    why = {tuple(d['members']): d['why'] for d in dec}
    assert 'its host M1 is a member of the kept pin_run' in \
        why[('K1', 'K2')], why
    assert 'is the host of the kept pin_run' in why[('X1', 'X2')], why
    print("  PASS: no host is a member on 5 boards; every host clears "
          f"{arr.MIN_HOST_TO_MEMBER_PADS}x its members' pads; the refused "
          "side is the lower-precedence one, else the one whose host is a "
          "member")


def _chain_board():
    nets, fps = {}, {}
    nid = [100]

    def net(name):
        nid[0] += 1
        nets[nid[0]] = SimpleNamespace(name=name, pads=[])
        return nid[0]

    def part(ref, fpname, value, npads, sheet='s'):
        pads = [SimpleNamespace(pad_number=str(n), net_id=0, component_ref=ref,
                                layers=['F.Cu'], local_x=float(n % 2),
                                local_y=float(n), global_x=0.0, global_y=0.0,
                                pintype='', pinfunction='')
                for n in range(1, npads + 1)]
        fps[ref] = SimpleNamespace(reference=ref, footprint_name=fpname,
                                   value=value, sheet_path=f'/{sheet}/{ref}',
                                   pads=pads, locked=False, x=0.0, y=0.0,
                                   rotation=0.0)

    def wire(a, ap, b, bp, name):
        n = net(name)
        for ref, pn in ((a, ap), (b, bp)):
            p = fps[ref].pads[pn - 1]
            p.net_id = n
            nets[n].pads.append(p)

    part('H', 'QFP-20', 'MCU', 20)
    for r in ('M1', 'M2'):
        part(r, 'RA-8', 'RA', 8)
    for r in ('K1', 'K2'):
        part(r, 'R_0402', '1k', 2)
    wire('M1', 1, 'H', 1, '/A1')
    wire('M2', 1, 'H', 2, '/A2')
    wire('K1', 1, 'M1', 2, '/K1')
    wire('K2', 1, 'M1', 3, '/K2')
    for r in ('X1', 'X2'):
        part(r, 'SOIC-8', 'DRV', 8, sheet='t')
    for r in ('D1', 'D2'):
        part(r, 'LED', 'RED', 2, sheet='u')
    for r in ('C1', 'C2'):
        part(r, 'C_0402', '1n', 2, sheet='t')
    # X2 gets the same shape through caps on ANOTHER sheet, so X1 and X2
    # are one sheet_bank and C1+C2 (sheet t) serve X1 alone.
    for r in ('C3', 'C4'):
        part(r, 'C_0402', '1n', 2, sheet='v')
    wire('X1', 1, 'D1', 1, '/L1')
    wire('X2', 1, 'D2', 1, '/L2')
    wire('C1', 1, 'X1', 2, '/C1')
    wire('C2', 1, 'X1', 3, '/C2')
    wire('C3', 1, 'X2', 2, '/C3')
    wire('C4', 1, 'X2', 3, '/C4')
    return SimpleNamespace(footprints=fps, nets=nets)


def test_connectors_testpoints_jumpers_are_never_members():
    """Fix-round 1, item 2. Before: ulx3s U1:CONN_02X20 and the PTS645
    buttons, coldfire's CONN_1 test points, CONN_4X2 jumper fields and
    DB9s were members."""
    from placement.part_class import classify_part
    for path in BOARDS:
        fps = _pcb(path).footprints
        dec = []
        cands = arr.suggest_arrays(_pcb(path), declined=dec)
        for c in cands:
            for m in c['members']:
                assert classify_part(fps[m], m).name is None, (c['name'], m)
                assert 'jumper' not in fps[m].footprint_name.lower(), m
        for d in dec:
            assert d['why'], d
    names = {c['name'] for c in _sug(ULX3S)}
    assert 'U1:CONN_02X20' not in names and 'U1:PTS645' not in names
    assert not any(c['evidence']['value'] == 'DB9' for c in _sug(COLDFIRE))
    # A connector still HOSTS: glasgow's R-array rows on J2/J3.
    assert any(c['serves'] == 'J3' for c in _sug(GLASGOW))
    print("  PASS: no connector/switch/test point/mount/jumper member on 5 "
          "boards; connectors still host")


def _cap_trio(nets_named, chip=False, loads=False):
    """Three identical caps, each on its own net plus GND. `chip` puts each
    net on a `power_in` pin of one 4-pad chip U1 (so the net is on a SUPPLY
    PIN whatever its name); `loads` adds one more part per net, so the net
    reaches three parts (`arrays.RAIL_MIN_PARTS`)."""
    nets, fps = {}, {}

    def pad(ref, num, net_id, name, pintype='', x=0.0):
        p = SimpleNamespace(pad_number=str(num), net_id=net_id,
                            component_ref=ref, layers=['F.Cu'], local_x=x,
                            local_y=float(num), global_x=0.0,
                            global_y=float(num), pintype=pintype,
                            pinfunction='')
        if net_id:
            nets.setdefault(net_id, SimpleNamespace(name=name, pads=[])
                            ).pads.append(p)
        return p

    def part(ref, fpname, value, pads):
        fps[ref] = SimpleNamespace(
            reference=ref, footprint_name=fpname, value=value,
            sheet_path='/root/' + ref, locked=False, x=0.0, y=0.0,
            rotation=0.0, pads=pads)
    for i, name in enumerate(nets_named):
        part(f'C{i + 1}', 'C_0603', '10u',
             [pad(f'C{i + 1}', 1, 10 + i, name), pad(f'C{i + 1}', 2, 1,
                                                    'GND')])
        if loads:
            part(f'L{i + 1}', f'L_{i}', f'{i}uH',
                 [pad(f'L{i + 1}', 1, 10 + i, name), pad(f'L{i + 1}', 2, 0,
                                                        '')])
    if chip:
        part('U1', 'SOIC-8', 'CHIP',
             [pad('U1', n, 10 + n - 1 if n <= 3 else 0,
                  nets_named[n - 1] if n <= 3 else '', 'power_in',
                  x=0.0 if n <= 2 else 5.0) for n in range(1, 5)])
    return SimpleNamespace(footprints=fps, nets=nets)


def test_the_supply_pin_rail_path():
    """Fix-round 2, item 4: a net no NAME test calls a rail is one when it
    sits on a chip's supply pin and reaches `RAIL_MIN_PARTS` parts -- and a
    supply-pin net joining ONE chip pin to ONE cap is a local node (a
    charge-pump reservoir), not a rail."""
    names = ['/NODE_A', '/NODE_B', '/NODE_C']
    assert not any(arr._rail_by_name(n) for n in names)
    shared = arr.suggest_arrays(_cap_trio(names, chip=True, loads=True))
    assert shared == [], ("three parts on each supply-pin net: rails, so "
                          "the caps have no own signal net", shared)
    local = arr.suggest_arrays(_cap_trio(names, chip=True))
    assert [(c['criterion'], c['serves']) for c in local] == [
        ('pin_run', 'U1')], local
    # The real case: coldfire's MAX202 charge-pump caps (V+ / V- are typed
    # power_out, each net = chip pin + one cap) are four, not two.
    rows = {c['name']: c['members'] for c in _sug(COLDFIRE)}
    assert rows['U202:100nF'] == ['C205', 'C204', 'C206', 'C207'], rows
    assert rows['U203:100nF'] == ['C210', 'C209', 'C211', 'C214'], rows
    print("  PASS: supply-pin nets reaching 3 parts are rails; a chip-pin-"
          "plus-one-cap node is not (coldfire U202/U203 rows keep 4 caps)")


def test_rail_names_the_detector_reads_locally():
    """Fix-round 2, items 5 and 6. The shared `is_power_net_name` calls a
    digit-led leaf a rail, `/32K_P` included; the detector alone narrows
    that to voltage-shaped leaves, so watchy's crystal load caps form a
    row on U4's crystal pins. And the documented limitation: ulx3s's jumper-fed P1V1/P2V5/P3V3
    are not traced, so `bank:2.2uF` still forms."""
    from net_queries import is_power_net_name
    assert is_power_net_name('/32K_P'), 'the shared predicate is unchanged'
    assert not arr._rail_by_name('/32K_P')
    for v in ('3V3', '/power/3.3V', '5V', '1V8_A', '+3V3', 'VCC', 'GND'):
        assert arr._rail_by_name(v), v
    for s_ in ('12MHZ', '25M_XO', '32K_N'):
        assert not arr._rail_by_name(s_), s_
    # Their crystal nets are now signal nets, so the load caps are a
    # pin_run on U4's two crystal pins (the stronger criterion), where
    # before this fix they were at best a sheet_bank.
    rows = {c['name']: c for c in _sug(WATCHY)}
    assert rows.get('U4:18pF', {}).get('members') == ['C17', 'C18'], rows
    nets = {ln['net'] for m in ('C17', 'C18')
            for ln in rows['U4:18pF']['evidence']['links'][m]}
    assert nets == {'/32K_P', '/32K_N'}, nets
    ulx = {c['name']: c['members'] for c in _sug(ULX3S)}
    assert ulx.get('bank:2.2uF') == ['C22', 'C23', 'C24'], ulx
    print("  PASS: /32K_P is no rail to the detector (watchy U4:18pF "
          "forms), the shared predicate unchanged; ulx3s bank:2.2uF still "
          "forms, as documented")


def test_host_floor_and_cascade_on_splitflap():
    """Fix-round 2, items 1-3. What the 1.25x floor protects is splitflap's
    real `U5:220R` / `U9:220R` rows. At 1.0 the chip chain becomes
    candidates; the survivor-only refusal must still keep both rows (the
    one-pass refusal dropped them), while chips become members -- so the
    floor is what keeps them out. A host the floor rejected is disclosed."""
    rows = {c['name']: c['members'] for c in _sug(SPLITFLAP)}
    assert rows.get('U5:220R') == ['R2', 'R3', 'R4'], rows
    assert rows.get('U9:220R') == ['R1', 'R10', 'R5'], rows
    dec = []
    arr.suggest_arrays(_pcb(SPLITFLAP), declined=dec)
    fl = [d for d in dec if 'copper pads, under' in d['why']]
    assert any(d['serves'] == 'U6' and set(d['members']) == {'U1', 'U5'}
               for d in fl), fl
    saved = arr.MIN_HOST_TO_MEMBER_PADS
    try:
        arr.MIN_HOST_TO_MEMBER_PADS = 1.0
        at1 = arr.suggest_arrays(_pcb(SPLITFLAP))
    finally:
        arr.MIN_HOST_TO_MEMBER_PADS = saved
    r1 = {c['name']: c['members'] for c in at1}
    assert r1.get('U5:220R') == ['R2', 'R3', 'R4'], r1
    assert r1.get('U9:220R') == ['R1', 'R10', 'R5'], r1
    chips = {'U1', 'U2', 'U5', 'U6', 'U7', 'U8', 'U9', 'U10'}
    assert chips & {m for c in at1 for m in c['members']}, \
        "at 1.0 the chip chain must reach the candidates (the floor binds)"
    hosts = {c['serves'] for c in at1 if c['serves']}
    assert not hosts & {m for c in at1 for m in c['members']}
    print(f"  PASS: U5:220R and U9:220R kept at 1.25 AND at 1.0; the floor "
          f"keeps chips out; {len(fl)} floor rejection(s) disclosed")


def test_decline_wording():
    """Fix-round 2, item 7: the reason names the classifier, not a noun
    the part is not (coldfire's DB9s read 'a mount_hole part'). Since #1176
    the classifier reads them as what they are -- edge receptacles; before
    it their `..._MountingHolesOffset...` name made them mount holes."""
    dec = []
    arr.suggest_arrays(_pcb(COLDFIRE), declined=dec)
    db9 = [d for d in dec if 'UARTCAN201' in d['members']]
    assert db9 and db9[0]['why'].startswith(
        'part_class classifies it edge_receptacle'), db9
    print("  PASS: the DB9 decline names part_class's verdict")


def test_rails_are_not_own_nets_in_a_sheet_bank():
    """Fix-round 1, item 3: three caps each on its own named rail plus GND
    are three regulators' caps, not a repeated channel."""
    assert arr.suggest_arrays(_cap_trio(['+1V8', '+3V3', '+5V'])) == []
    pos = arr.suggest_arrays(_cap_trio(['/SIG_A', '/SIG_B', '/SIG_C']))
    assert [c['criterion'] for c in pos] == ['sheet_bank'], pos
    print("  PASS: rail-only caps form no bank; the same caps on signal nets "
          "do (the control is not vacuous)")


def test_decap_row_order_is_unknown():
    """Fix-round 1, item 4: caps on one rail pair are interchangeable."""
    rows = [c for c in _sug(GLASGOW) if c['criterion'] == 'decap_row']
    assert rows, 'glasgow has decap rows on U30 +1V2'
    for c in rows:
        assert c['order'] == 'unknown', (c['name'], c['order'])
        assert c['serves'] == 'U30' and c['evidence']['rail'] == '+1V2', c
    print(f"  PASS: {len(rows)} glasgow decap_row(s), order 'unknown'")


def test_cli_bare_json_and_refusal():
    r = subprocess.run([sys.executable, '-X', 'utf8', CHECK_FLOORPLAN,
                        SPLITFLAP, '--suggest-arrays'],
                       capture_output=True, text=True, encoding='utf-8',
                       cwd=ROOT, timeout=300)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    doc = json.loads(r.stdout)          # bare JSON: no CMD: banner line
    assert doc['pose_blind'] is True and doc['count'] == len(
        doc['suggestions'])
    assert json.dumps(doc['suggestions'], sort_keys=True) == json.dumps(
        _sug(SPLITFLAP), sort_keys=True)
    check([sys.executable, '-X', 'utf8', CHECK_FLOORPLAN, SPLITFLAP,
           '--suggest-arrays', '--intent', 'x.json'],
          refuse='--suggest-arrays runs alone', code=2, allow=('usage:',))
    # Fix-round 1, item 5: every flag the mode would silently ignore is
    # refused BY NAME, a missing --brief file included.
    for extra, name in ((['--brief', 'no_such_brief.json'], '--brief'),
                        (['--plan-only'], '--plan-only'),
                        (['--require-brief'], '--require-brief'),
                        (['--group-by', 'sheet'], '--group-by')):
        check([sys.executable, '-X', 'utf8', CHECK_FLOORPLAN, SPLITFLAP,
               '--suggest-arrays'] + extra,
              refuse=f"--suggest-arrays does not use {name}", code=2,
              allow=('usage:',))
    check([sys.executable, '-X', 'utf8', CHECK_FLOORPLAN, SPLITFLAP,
           '--suggest-arrays', '--json',
           os.path.join(ROOT, 'no_such_dir_1051', 'x', 's.json')],
          refuse='its directory does not exist', code=2, allow=('usage:',))
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 's.json')
        r = check([sys.executable, '-X', 'utf8', CHECK_FLOORPLAN, SPLITFLAP,
                   '--suggest-arrays', '--json', out], accept=True)
        assert 'array suggestion(s)' in r.stdout
        with open(out, encoding='utf-8') as fh:
            assert json.load(fh)['count'] == doc['count']
    print("  PASS: --suggest-arrays stdout is bare JSON equal to the "
          "in-process answer; --json gets the file and a summary; with "
          "--intent it refuses by reason")


def test_series_passives_are_rows_not_bridges():
    """Phase-3 fix round 1's bridge rule is for ACTIVE members only. The
    control: ulx3s's GPDI coupling caps C38-C45 sit in series between U1 and
    the GPDI connector -- each reaches a second big part by another net,
    the literal bridge shape -- and are a row the human board forms, so they
    stay a suggestion. Without the active-member conjunct they were
    declined as 'bridges U1 and GPDI1'."""
    declined = []
    cands = arr.suggest_arrays(_pcb(ULX3S), declined=declined)
    assert not [d for d in declined if d['why'].startswith('bridges')],         declined
    hit = [c for c in cands if 'C38' in c['members']]
    assert len(hit) == 1 and len(hit[0]['members']) == 8, hit
    assert hit[0]['serves'] == 'U1', hit[0]['serves']
    print(f"  PASS: ulx3s GPDI caps stay one row on U1 "
          f"({hit[0]['name']}); no bridge declined on ulx3s")


TESTS = [
    test_glasgow_resistor_arrays_and_buffers,
    test_splitflap_pullups_in_u4_pin_order,
    test_ulx3s_549r_on_u1,
    test_negative_control_no_common_part_no_suggestion,
    test_pose_blind,
    test_formation_on_the_human_glasgow,
    test_emit_intent_default_unchanged_and_auto_round_trips,
    test_cli_bare_json_and_refusal,
    test_watchy_edge_claims_on_passives_yield_to_the_row,
    test_no_part_is_host_and_member_and_no_big_ic_member,
    test_connectors_testpoints_jumpers_are_never_members,
    test_rails_are_not_own_nets_in_a_sheet_bank,
    test_decap_row_order_is_unknown,
    test_series_passives_are_rows_not_bridges,
    test_the_supply_pin_rail_path,
    test_rail_names_the_detector_reads_locally,
    test_host_floor_and_cascade_on_splitflap,
    test_decline_wording,
    test_every_suggestion_row_loads_as_pasted,
]


if __name__ == '__main__':
    # A name filter (substring), so a mutation battery can run one test;
    # the battery checks each name it asked for printed its `---` line.
    only = sys.argv[1:]
    for t in TESTS:
        if only and not any(o in t.__name__ for o in only):
            continue
        print(f"--- {t.__name__}")
        t()
    print('ALL PASS')
