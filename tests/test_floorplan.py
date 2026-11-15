"""Check the chip view against a real placement. Needs the open-source tools (the gowin_oss add-on).

usage: uv run python tests/test_floorplan.py
"""
import json
import re
import shutil
import tempfile
from pathlib import Path

from brigse import floorplan, oss_suite

FX = Path(__file__).resolve().parent / "fixtures" / "floorplan_blinky"
if not oss_suite.tool("tabbypy3").is_file():
    print("SKIPPED: the open-source tools are not installed (request the gowin_oss add-on first)")
    raise SystemExit(0)

work = Path(tempfile.mkdtemp())
shutil.copytree(FX, work / "job")
view = floorplan.make(work / "job")
cells = next(iter(json.loads((FX / "blinky_pnr.json").read_text())["modules"].values()))["cells"]
logic = {re.match(r"(X\d+Y\d+)/", c["attributes"]["NEXTPNR_BEL"]).group(1) for c in cells.values()
         if c["type"].startswith(("LUT", "ALU", "DFF"))}
pins = {re.match(r"(X\d+Y\d+)/", c["attributes"]["NEXTPNR_BEL"]).group(1) for c in cells.values()
        if c["type"].endswith("BUF")}
svg = Path(view["svg"]).read_text()
checks = {
    "the chip view was drawn": view is not None and svg.startswith("<svg"),
    "every cell sits on a tile of the right kind": view["mismatch"] == 0,
    "the logic tiles used match the placement": view["logic_used"] == len(logic),
    "the pin tiles used match the placement": view["io_used"] == len(pins),
    "the chip has the known size": (view["logic_total"], view["bsram_total"], view["dsp_total"]) == (1080, 26, 5),
    "the picture names the board": "Tang Nano 9K" in svg,
    "a second request reuses the picture": floorplan.make(work / "job") == view,
}
for name, ok in checks.items():
    print(("ok   " if ok else "FAIL ") + name)
shutil.rmtree(work, ignore_errors=True)
assert all(checks.values()), "chip view test failed"
print("\nFLOORPLAN OK")
