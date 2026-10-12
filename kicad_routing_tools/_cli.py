"""``krt <tool> [args...]``: run any repo script as its own ``__main__``."""

import os
import runpy
import sys

from kicad_routing_tools import ROOT, SEARCH_DIRS

# Script directories, in lookup order (rust_router holds no tools).
_TOOL_DIRS = [d for d in SEARCH_DIRS if not d.endswith("rust_router")]


def _tools():
    found = {}
    for d in _TOOL_DIRS:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for f in names:
            if f.endswith(".py") and not f.startswith("_"):
                found.setdefault(f[:-3], os.path.join(d, f))
    return found


def run_tool(name, argv):
    name = name[:-3] if name.endswith(".py") else name
    tools = _tools()
    path = tools.get(name) or tools.get(name.replace("-", "_"))
    if path is None:
        sys.exit(f"krt: unknown tool {name!r} (try: krt --list)")
    sys.argv = [path] + list(argv)
    # A script run as `python3 py_router/route.py` has its own dir first.
    sys.path.insert(0, os.path.dirname(path))
    runpy.run_path(path, run_name="__main__")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: krt <tool> [args...]   e.g. krt route board.kicad_pcb --nets '*'\n"
              "       krt --list             list available tools\n"
              f"tools are the scripts under {ROOT}")
        return
    if argv[0] == "--list":
        for name, path in sorted(_tools().items()):
            print(f"{name:32s} {os.path.relpath(path, ROOT)}")
        return
    if argv[0] == "--version":
        from kicad_routing_tools import __version__
        print(__version__)
        return
    run_tool(argv[0], argv[1:])


def _entry(name):
    return lambda: run_tool(name, sys.argv[1:])


route = _entry("route")
route_diff = _entry("route_diff")
route_planes = _entry("route_planes")
bga_fanout = _entry("bga_fanout")
check_drc = _entry("check_drc")
check_connected = _entry("check_connected")
