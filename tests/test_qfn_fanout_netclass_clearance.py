#!/usr/bin/env python3
"""qfn_fanout under --no-fix-drc-settings grades stubs at the board's own
Default net-class clearance when --clearance is omitted.

Without the DRC-settings writeback nothing lowers the project floor to the
fanout's generic 0.1mm default, so stubs kept only 0.1mm from a nearby foreign
pad (a crystal or decap beside a 0.2mm-class LQFP) shipped as KiCad clearance
violations. With the writeback ON the old default stands (the writeback lowers
the floor to it), and an explicit --clearance always wins.

Cases (each runs the real CLI on a copy of qfn_diffpair_escape with a
.kicad_pro declaring Default clearance 0.2):
  1. --no-fix-drc-settings, no --clearance  -> clearance 0.2, announced
  2. writeback ON, no --clearance           -> clearance 0.1 (unchanged)
  3. --no-fix-drc-settings --clearance 0.15 -> 0.15 (explicit wins)

Run:  python3 tests/test_qfn_fanout_netclass_clearance.py [-v]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOARD = os.path.join(ROOT, "kicad_files", "qfn_diffpair_escape.kicad_pcb")
CLI = os.path.join(ROOT, "py_router", "qfn_fanout.py")
PRO = {"board": {"design_settings": {"rules": {}}},
       "net_settings": {"classes": [{"name": "Default", "clearance": 0.2, "track_width": 0.2,
                                     "via_diameter": 0.6, "via_drill": 0.3}]}}


def _run(tmp, name, extra):
    src = os.path.join(tmp, name + ".kicad_pcb")
    shutil.copyfile(BOARD, src)
    with open(os.path.join(tmp, name + ".kicad_pro"), "w") as f:
        json.dump(PRO, f)
    out = os.path.join(tmp, name + "_out.kicad_pcb")
    r = subprocess.run([sys.executable, CLI, src, "-o", out, "-c", "U1", *extra],
                       capture_output=True, text=True, timeout=600)
    summary = None
    for line in r.stdout.splitlines():
        if line.startswith("JSON_SUMMARY:"):
            summary = json.loads(line.split(":", 1)[1])
    return r, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(BOARD):
        print(f"[SKIP] board absent: {BOARD}")
        return 77
    cases = [
        ("binding", ["--no-fix-drc-settings"], 0.2, True),
        ("writeback", [], 0.1, False),
        ("explicit", ["--no-fix-drc-settings", "--clearance", "0.15"], 0.15, False),
    ]
    fails = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, extra, want, announced in cases:
            r, s = _run(tmp, name, extra)
            if r.returncode != 0 or s is None:
                fails.append(f"{name}: exit {r.returncode}, no JSON_SUMMARY\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
                continue
            if abs(s["clearance"] - want) > 1e-9:
                fails.append(f"{name}: clearance {s['clearance']}, want {want}")
            said = "using the board's Default net-class clearance" in r.stdout
            if said != announced:
                fails.append(f"{name}: announcement {'missing' if announced else 'unexpected'}")
            if not s.get("escaped"):
                fails.append(f"{name}: no pin escaped (test exercises nothing)")
            if args.verbose:
                print(f"  {name}: clearance {s['clearance']} escaped {s.get('escaped')}/{s.get('requested')}")
    if fails:
        print("FAIL:\n  " + "\n  ".join(fails))
        return 1
    print("PASS: qfn_fanout defaults to the board's net-class clearance when its rules are binding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
