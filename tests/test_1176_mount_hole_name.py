"""#1176: a mounting hole is named `MountingHole...`; a D-sub that merely says
`...MountingHolesOffset...` is an edge receptacle.

`part_class.classify_part` tested `'mountinghole' in name` before it looked at
the edge-receptacle table, and KiCad's horizontal D-subs carry their mounting
hole offset in their name. Five tracked DSUBs (flat_hierarchy J1, interf_u P1
on two boards, coldfire UARTCAN201-203) read `mount_hole`, so the emitter
never declared their edge, `edge_connector` reported "not applicable" on
flat_hierarchy, and the checker and the quench treated them as markers.

`part_class.is_mount_hole_name` reads the ITEM name -- `MountingHole` at its
start (every hole on the tracked corpus and the fa10 designer boards is named
`MountingHole_...`, whatever its library) or the singular later on (a house
`M3_MountingHole_Pad`), never the plural `MountingHoles` a part that merely HAS
holes carries -- and the item rather than `lib:item`, because the text parser
keeps the library prefix and the pcbnew path does not.
"""

import os
import subprocess
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ('py_placer', 'py_router', 'py_tools', 'tests'):
    sys.path.insert(0, os.path.join(ROOT, _p))

from kicad_parser import parse_kicad_pcb                      # noqa: E402
from placement import floorplan                              # noqa: E402
from placement.lock_advisor import advise_locks              # noqa: E402
from placement.part_class import classify_part, is_mount_hole_name  # noqa: E402
from run_utils import corpus_boards                          # noqa: E402

KF = os.path.join(ROOT, 'kicad_files')
DSUBS = {'flat_hierarchy': ('J1',), 'interf_u_unrouted': ('P1',),
         'interf_u_unrouted_placed': ('P1',),
         'kit-dev-coldfire-xilinx_5213': ('UARTCAN201', 'UARTCAN202',
                                          'UARTCAN203')}


def _pcb(name):
    return parse_kicad_pcb(os.path.join(KF, name + '.kicad_pcb'))


class TheName(unittest.TestCase):

    def test_the_item_name_decides(self):
        for name in ('MountingHole:MountingHole_3.2mm_M3',
                     'MountingHole_3.2mm_M3',
                     'Glasgow:MountingHole_3.5mm_Pad_Via_CrtYd',
                     'Mounting_Holes:MountingHole_3.2mm_M3_ISO14580_Pad',
                     # a house hole whose item says it later on
                     'Lib:M3_MountingHole_Pad', 'M3_MountingHole_Pad',
                     'house:Hole_MountingHole_3.2mm'):
            self.assertTrue(is_mount_hole_name(name), name)
        for name in ('kit-dev-coldfire:DSUB-9_Female_Horizontal_P2.77x2.84mm'
                     '_EdgePinOffset7.70mm_Housed_MountingHolesOffset9.12mm',
                     'DSUB-9_Female_Horizontal_P2.77x2.84mm_EdgePinOffset7.70mm'
                     '_Housed_MountingHolesOffset9.12mm',
                     'MountingHole:Some_Connector',
                     # the plural: parts that HAVE mounting holes (KiCad 10's
                     # stock D-subs, modules and encoders)
                     'Connector_Dsub:DSUB-15-HD_Pins_Vertical_P2.29x1.98mm'
                     '_MountingHoles',
                     'Module:Arduino_UNO_R3_WithMountingHoles',
                     'Arduino_UNO_R3_WithMountingHoles',
                     'Rotary_Encoder:RotaryEncoder_Alps_EC11E_Vertical_H20mm'
                     '_CircularMountingHoles',
                     # near misses: a standoff is not a mounting hole
                     'Mounting_Wuerth:Mounting_Wuerth_WA-SMSI-M3_H2mm_9774020633',
                     'MountingEquipment:DINRailAdapter_3xM3_PhoenixContact',
                     '', None):
            self.assertFalse(is_mount_hole_name(name), name)

    def test_the_marker_table_carries_no_hole_substring(self):
        """`MOUNT_FP` is the marker table lock_advisor imports; a hole
        substring there would bring #1176 back through its next consumer."""
        from placement.part_class import MOUNT_FP
        self.assertFalse([k for k in MOUNT_FP if 'mounting' in k])

    def test_both_parse_fronts_agree(self):
        """The text parser keeps `lib:item`, pcbnew keeps `item`."""
        pads = [SimpleNamespace(pad_type='thru_hole', layers=['*.Cu'],
                                drill=1.0, pinfunction='', net_id=1)
                for _ in range(9)]
        for item, want in (
                ('DSUB-9_Female_Horizontal_P2.77x2.84mm_EdgePinOffset7.70'
                 'mm_Housed_MountingHolesOffset9.12mm', 'edge_receptacle'),
                ('MountingHole_3.2mm_M3_Pad', 'mount_hole'),
                ('M3_MountingHole_Pad', 'mount_hole'),
                ('Arduino_UNO_R3_WithMountingHoles', None)):
            a = classify_part(SimpleNamespace(footprint_name='Lib:' + item,
                                              pads=pads), 'J9')
            b = classify_part(SimpleNamespace(footprint_name=item,
                                              pads=pads), 'J9')
            self.assertEqual(a, b, item)
            self.assertEqual(a.name, want, item)


class TheCorpus(unittest.TestCase):

    def test_the_five_dsubs_are_edge_receptacles(self):
        for board, refs in DSUBS.items():
            pcb = _pcb(board)
            for ref in refs:
                pc = classify_part(pcb.footprints[ref], ref)
                self.assertEqual(pc.name, 'edge_receptacle', (board, ref, pc))

    def test_every_named_hole_on_the_corpus_is_still_a_hole(self):
        boards = corpus_boards()
        if not boards:
            self.skipTest('git cannot list the tracked boards')
        holes, others = 0, []
        for path in boards:
            pcb = parse_kicad_pcb(path)
            for ref, fp in sorted(pcb.footprints.items()):
                name = (fp.footprint_name or '').lower()
                if 'mountinghole' not in name:
                    continue
                cls = classify_part(fp, ref).name
                if is_mount_hole_name(name):
                    holes += 1
                    self.assertEqual(cls, 'mount_hole',
                                     (os.path.basename(path), ref))
                else:
                    others.append((os.path.basename(path), ref, cls))
        # 27 named holes on the 22 tracked boards: a change detector.
        self.assertEqual(holes, 27)
        self.assertEqual(sorted(others), sorted(
            (b + '.kicad_pcb', r, 'edge_receptacle')
            for b, refs in DSUBS.items() for r in refs))


class WhatItChanges(unittest.TestCase):

    def test_coldfire_declares_its_three_dsubs_on_the_north_edge(self):
        path = os.path.join(KF, 'kit-dev-coldfire-xilinx_5213.kicad_pcb')
        doc = floorplan.emit_intent(parse_kicad_pcb(path), path)
        edges = {c['ref']: c.get('edge') for c in doc['edge_connectors']}
        for ref in DSUBS['kit-dev-coldfire-xilinx_5213']:
            self.assertEqual(edges.get(ref), 'north', (ref, edges))

    def test_flat_hierarchy_arms_the_edge_rule(self):
        path = os.path.join(KF, 'flat_hierarchy.kicad_pcb')
        doc = floorplan.emit_intent(parse_kicad_pcb(path), path)
        self.assertEqual([(c['ref'], c.get('edge'))
                          for c in doc['edge_connectors']], [('J1', 'west')])
        r = floorplan.grade(floorplan.intent_from_dict(doc, source_path='e'),
                            parse_kicad_pcb(path), path,
                            group_sources=('kicad', 'sheet'))
        self.assertIn('edge_connector', r.rules_run)
        self.assertIn('pins_to_edge', r.rules_run)
        self.assertNotIn('edge_connector', r.rules_skipped)
        self.assertEqual(r.errors, [])

    def test_the_cli_no_longer_says_not_applicable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.run(
                [sys.executable, '-X', 'utf8', 'py_tools/check_floorplan.py',
                 os.path.join(KF, 'flat_hierarchy.kicad_pcb'),
                 '--emit-intent', os.path.join(td, 'i.json')],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', cwd=ROOT, timeout=600)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out[-800:])
        self.assertIn('1 edge connector(s)', out)
        self.assertNotIn('no part classifies as an edge receptacle', out)

    def test_lock_advisor_names_the_connector_table(self):
        path = os.path.join(KF, 'flat_hierarchy.kicad_pcb')
        adv = advise_locks(parse_kicad_pcb(path), path)
        j1 = next(f for f in adv.findings if f.ref == 'J1')
        text = ' '.join(j1.reasons)
        self.assertNotIn('mounting/fiducial/testpoint', text)
        self.assertIn('connector table', text)
        self.assertEqual(j1.part_class, 'edge_receptacle')
        hole = next(f for f in adv.findings if f.ref == 'HOLE1')
        self.assertEqual(hole.part_class, 'mount_hole')

    def test_board_context_reads_the_same_name_rule(self):
        """board_context's role table had its own `'mountinghole'` substring
        rung; a connector the classifier does not call a hole must not be
        labelled one there either."""
        from board_context import infer_role
        conn = SimpleNamespace(
            footprint_name='Lib:Molex_Socket_1x04_MountingHolesOffset3.0mm',
            value='')
        self.assertEqual(infer_role(conn, 'J5', 'connector_affinity')['role'],
                         'connector')
        hole = SimpleNamespace(footprint_name='Lib:MountingHole_3.2mm_M3_Pad',
                               value='')
        self.assertEqual(infer_role(hole, 'X9', None)['role'], 'mounting hole')


if __name__ == '__main__':
    unittest.main()
