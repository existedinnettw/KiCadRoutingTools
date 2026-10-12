# Using KiCadRoutingTools as a Python package (uv / pip)

This branch (`pypi`) adds packaging on top of upstream
[drandyhaas/KiCadRoutingTools](https://github.com/drandyhaas/KiCadRoutingTools);
`.github/workflows/rebase-upstream.yml` rebases it onto upstream `main` daily.

## Install

```bash
uv add "kicad-routing-tools @ git+https://github.com/existedinnettw/KiCadRoutingTools@pypi"
# or: uv pip install "git+https://github.com/existedinnettw/KiCadRoutingTools@pypi"
```

The build runs `build_router.py`, which downloads the prebuilt Rust
`grid_router` binary for your platform from upstream's GitHub Releases
(Linux x86_64, macOS arm64/x86_64, Windows x86_64), falling back to
`cargo build` if that fails. Build-time knobs:
`KRT_ROUTER_FROM_SOURCE=1`, `KRT_ROUTER_TAG=vX.Y.Z`, `KRT_SKIP_ROUTER=1`.

## Command line

Every repo script runs as `krt <script-name> [args...]`, with the same
arguments as `python3 py_router/<script>.py`:

```bash
uv run krt --list
uv run krt route board.kicad_pcb out.kicad_pcb --nets '*'
uv run krt check_drc out.kicad_pcb
uv run krt place_seed ...
```

Shortcuts: `krt-route`, `krt-route-diff`, `krt-route-planes`,
`krt-bga-fanout`, `krt-check-drc`, `krt-check-connected`.

## Library

```python
import kicad_routing_tools            # puts py_router/py_placer/py_tools on sys.path
from kicad_parser import parse_kicad_pcb
pcb = parse_kicad_pcb("board.kicad_pcb")
```

The modules keep their flat upstream names (`kicad_parser`, `routing_config`,
...), so the API documented in `docs/python-api.md` applies unchanged.
The KiCad GUI plugin is not registered by this package; install it with
upstream's `install_plugin.py` / PCM as usual.
