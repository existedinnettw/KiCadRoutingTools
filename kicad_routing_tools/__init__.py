"""KiCadRoutingTools as an importable package.

Importing this package puts the repo's flat module directories on
``sys.path`` (exactly as the scripts do for themselves), after which the
documented API is available under its usual names::

    import kicad_routing_tools
    from kicad_parser import parse_kicad_pcb

Command-line tools run through ``krt <tool> [args...]`` (``krt --list``).
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# Installed wheel: the tree is copied beside this file. Source checkout
# (editable install): the tree is the parent directory.
ROOT = _here if os.path.isdir(os.path.join(_here, "py_router")) else os.path.dirname(_here)

#: Directories searched, in order, for importable modules and for tools.
SEARCH_DIRS = [os.path.join(ROOT, d) for d in
               ("py_router", "py_placer", "py_tools", "rust_router")] + [ROOT]


def setup_path():
    """Put the tool directories on sys.path (idempotent)."""
    for d in reversed(SEARCH_DIRS):
        if d not in sys.path:
            sys.path.insert(0, d)


def _read_version():
    try:
        with open(os.path.join(ROOT, "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "0+unknown"


__version__ = _read_version()

setup_path()
