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
        run = subprocess.run([str(python), str(HELPER), family.group(1), str(placed[0]), str(svg), label],
                             capture_output=True, timeout=60, env=oss_suite.env())
        summary = json.loads(run.stdout.decode().strip().splitlines()[-1])
    except (subprocess.SubprocessError, ValueError, IndexError, OSError):
        return None             # the chip view is a bonus: never fail a build over it
    cache.write_text(json.dumps(summary))
    return {**summary, "svg": str(svg)}
