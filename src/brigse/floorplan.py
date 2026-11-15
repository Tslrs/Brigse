"""Chip view: a picture of where a build's logic sits on the chip (open-source Gowin flow).

The picture is drawn by floorplan_helper.py with the OSS CAD Suite's Python, from the placement
nextpnr wrote. Gowin EDA's own placement is not readable outside its GUI, so builds made with it
have no chip view.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import oss_suite
from .backends.base import BackendError

HELPER = Path(__file__).with_name("floorplan_helper.py")
BOARD_NAMES = {"GW1N-9C": "Sipeed Tang Nano 9K (GW1N-9C)", "GW2A-18C": "Sipeed Tang Nano 20K (GW2A-18C)"}


def make(out_dir: Path) -> dict | None:
    """Return the summary and the path of the picture, drawing it first when it is missing."""
    out_dir = Path(out_dir)
    placed = sorted(out_dir.glob("*_pnr.json"))
    if not placed:
        return None
    svg = out_dir / (placed[0].name.removesuffix("_pnr.json") + ".floorplan.svg")
    cache = svg.with_suffix(".json")
    if svg.is_file() and cache.is_file() and svg.stat().st_mtime >= placed[0].stat().st_mtime:
        return {**json.loads(cache.read_text()), "svg": str(svg)}
    script = out_dir / "run.sh"
    family = re.search(r"family=([\w-]+)", script.read_text()) if script.is_file() else None
    python = oss_suite.tool("tabbypy3")
    if not family or not python.is_file():
        return None
    top = placed[0].name.removesuffix("_pnr.json")
    label = f"{BOARD_NAMES.get(family.group(1), family.group(1))}: {top}"
    try:
        run = subprocess.run([str(python), str(HELPER), "placement", family.group(1), str(placed[0]), str(svg), label],
                             capture_output=True, timeout=60, env=oss_suite.env())
        summary = json.loads(run.stdout.decode().strip().splitlines()[-1])
    except (subprocess.SubprocessError, ValueError, IndexError, OSError):
        return None             # the chip view is a bonus: never fail a build over it
    cache.write_text(json.dumps(summary))
    return {**summary, "svg": str(svg)}


def from_bitstream(bitstream: Path, out_dir: Path, family: str = "GW1N-9C", label: str | None = None) -> dict:
    """Draw the chip view of any Gowin bitstream (a build by Gowin EDA included), as SVG and, when
    Pillow is installed, as a PNG for slides. Reads the file only; it is never changed."""
    bitstream = Path(bitstream).expanduser().resolve()
    if not bitstream.is_file():
        raise BackendError(f"Bitstream not found: {bitstream}")
    python, unpack = oss_suite.tool("tabbypy3"), oss_suite.tool("gowin_unpack")
    if not python.is_file() or not unpack.is_file():
        raise BackendError("The open-source tools are not installed. Call request_install('gowin_oss').")
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    svg = out_dir / f"{bitstream.stem}.chipview.svg"
    label = label or f"{BOARD_NAMES.get(family, family)}: {bitstream.stem}"
    try:
        run = subprocess.run([str(python), str(HELPER), "bitstream", family, str(bitstream), str(svg), label, str(unpack)],
                             capture_output=True, timeout=600, env=oss_suite.env())
        summary = json.loads(run.stdout.decode().strip().splitlines()[-1])
    except (subprocess.SubprocessError, ValueError, IndexError, OSError) as e:
        raise BackendError(f"Could not read the bitstream: {e}")
    result = {**summary, "svg": str(svg)}
    try:
        from . import floorplan_png
        result["png"] = str(floorplan_png.render(json.loads(Path(str(svg) + ".model.json").read_text()),
                                                 svg.with_suffix(".png")))
    except ImportError:
        result["png"] = None
        result["png_note"] = "Install Pillow for a PNG that can go into slides: uv sync --extra png"
    result["limits"] = ["Multipliers (DSP) cannot be read from a bitstream.",
                        "Input pins cannot be told from unused ones, so only outputs are shown.",
                        "Only the GW1N-9C chip (Tang Nano 9K) has been checked."]
    return result
