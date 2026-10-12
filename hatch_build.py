"""Hatch build hook: ship the repo as the ``kicad_routing_tools`` package.

The scripts are flat modules that locate each other (and rust_router/,
VERSION, requirements.txt, ...) relative to their own ``__file__``, so the
wheel reproduces the repo layout under ``kicad_routing_tools/`` instead of
restructuring anything. Before copying, ``build_router.py`` is run to put the
Rust ``grid_router`` extension in rust_router/ (prebuilt download, or a cargo
build when that fails).

Environment knobs:
  KRT_ROUTER_FROM_SOURCE=1   pass --from-source to build_router.py
  KRT_ROUTER_TAG=vX.Y.Z      pass --tag to build_router.py
  KRT_SKIP_ROUTER=1          do not build/require the Rust binary
"""

import os
import subprocess
import sys
import sysconfig

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

PKG = "kicad_routing_tools"

# Directories copied whole (minus the junk below).
TREE_DIRS = ["py_router", "py_placer", "py_tools", "kicad_routing_plugin", "awx"]
# Root files copied as-is. The root __init__.py (KiCad plugin registration)
# is deliberately NOT shipped: kicad_routing_tools/__init__.py replaces it.
ROOT_FILES = ["VERSION", "metadata.json", "requirements.txt", "LICENSE",
              "fab_overrides.example.txt", "opencode.json"]
RUST_FILES = ["Cargo.toml", "Cargo.lock", "README.md"]
BINARIES = ["grid_router.so", "grid_router.pyd"]

SKIP_DIRS = {"__pycache__", "target", ".git", ".pytest_cache"}
SKIP_EXT = {".pyc", ".pyo"}


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel" or version == "editable":
            # An editable install runs straight from the checkout; build the
            # router there with `python3 build_router.py` as usual.
            return
        root = self.root
        fi = build_data["force_include"]

        for d in TREE_DIRS:
            for dirpath, dirnames, filenames in os.walk(os.path.join(root, d)):
                dirnames[:] = [n for n in dirnames if n not in SKIP_DIRS]
                for f in filenames:
                    if os.path.splitext(f)[1] in SKIP_EXT:
                        continue
                    src = os.path.join(dirpath, f)
                    fi[src] = f"{PKG}/{os.path.relpath(src, root)}"

        for f in os.listdir(root):
            src = os.path.join(root, f)
            if f == "__init__.py" or f == "hatch_build.py" or not os.path.isfile(src):
                continue
            if f.endswith(".py") or f in ROOT_FILES:
                fi[src] = f"{PKG}/{f}"

        rust = os.path.join(root, "rust_router")
        for f in RUST_FILES:
            if os.path.isfile(os.path.join(rust, f)):
                fi[os.path.join(rust, f)] = f"{PKG}/rust_router/{f}"

        if os.environ.get("KRT_SKIP_ROUTER"):
            return
        binary = self._ensure_router(root, rust)
        fi[binary] = f"{PKG}/rust_router/{os.path.basename(binary)}"
        build_data["pure_python"] = False
        # pyo3 abi3-py39: one binary serves every CPython >= 3.9.
        plat = sysconfig.get_platform().replace("-", "_").replace(".", "_")
        build_data["tag"] = f"cp39-abi3-{plat}"

    def _ensure_router(self, root, rust):
        cmd = [sys.executable, os.path.join(root, "build_router.py")]
        if os.environ.get("KRT_ROUTER_FROM_SOURCE"):
            cmd.append("--from-source")
        if os.environ.get("KRT_ROUTER_TAG"):
            cmd += ["--tag", os.environ["KRT_ROUTER_TAG"]]
        # build_router.py is idempotent: it keeps a binary that already
        # matches Cargo.toml, downloads a prebuilt, or falls back to cargo.
        r = subprocess.run(cmd, cwd=root)
        for b in BINARIES:
            path = os.path.join(rust, b)
            if os.path.isfile(path):
                if r.returncode != 0:
                    self.app.display_warning(
                        f"build_router.py exited {r.returncode}; packaging the "
                        f"existing {path}")
                return path
        raise RuntimeError(
            "build_router.py did not produce rust_router/grid_router.{so,pyd} "
            f"(exit {r.returncode}). Install a Rust toolchain, or set "
            "KRT_SKIP_ROUTER=1 to build a wheel without the router.")
