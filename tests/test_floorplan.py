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
# reading a bitstream: a hand-written netlist like the one gowin_unpack writes, with the defaults it adds
SAMPLE = """module top(R1C2_IA);
input R1C2_IA;
`define something
IBUF R1C2_IBUF_A ( .O(R1C2_F6), .I(R1C2_IA) );
DFFSE R5C5_DFFE_0 ( .CLK(R5C5_CLK0), .D(R5C5_F0), .Q(R5C5_Q0) );
MUX2 R5C5_MUX2_LUT50 ( .I0(R5C5_F0), .I1(R5C5_F1), .O(R5C5_OF0), .S0(R5C5_SEL0) );
LUT4 R7C2_LUT4_2 ( .F(R7C2_F2), .I0(R7C2_A2) );
defparam R7C2_LUT4_2.INIT = 16'hff00;
ALU R7C3_ALU_0 ( .SUM(R7C3_F0) );
BSRAM BSRAM_0 ( .DO0(R10C5_F0), .DI0(R10C5_A0) );
OBUF R1C9_OBUF_A ( .I(R1C9_O), .O(R1C9_IA) );
rPLL R10C1_rPLL ( .CLKOUT(R10C1_X) );
assign R1C1_D1 = R1C1_F2;
endmodule
"""
import subprocess
code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import floorplan_helper as h; "
        "t = h.read_unpacked(sys.stdin.read()); "
        "print(json.dumps({k: sorted((c['cat'], c['n']) for c in [v]) for k, v in {f'{r},{c}': e for (r, c), e in t.items()}.items()}))")
run = subprocess.run([str(oss_suite.tool("tabbypy3")), "-c", code, str(Path(floorplan.__file__).parent)],
                      input=SAMPLE.encode(), capture_output=True, env=oss_suite.env())
found = json.loads(run.stdout.decode().strip().splitlines()[-1])
checks["a tile with a configured LUT or ALU is logic"] = found.get("6,1") == [["logic", 2]] and found.get("6,2") == [["logic", 1]]
checks["default flip-flops, multiplexers and input buffers are not counted"] = "4,4" not in found and "0,1" not in found
checks["a block RAM is placed by the tile in its connections"] = found.get("9,4") == [["bsram", 1]]
checks["an output buffer and a PLL are found"] = found.get("0,8") == [["io", 1]] and found.get("9,0") == [["pll", 1]]

# the PNG for slides is drawn from the same model
try:
    from brigse import floorplan_png
    model = json.loads(Path(view["svg"] + ".model.json").read_text())
    png = floorplan_png.render(model, work / "view.png")
    from PIL import Image
    width, height = Image.open(png).size
    checks["the PNG is twice the size of the SVG and not blank"] = (
        (width, height) == ((47 * 18 + 40) * 2, (29 * 18 + 176) * 2) and Image.open(png).getcolors(1) is None)
except ImportError:
    print("SKIPPED the PNG check: Pillow is not installed (uv sync --extra png)")

for name, ok in checks.items():
    print(("ok   " if ok else "FAIL ") + name)
shutil.rmtree(work, ignore_errors=True)
assert all(checks.values()), "chip view test failed"
print("\nFLOORPLAN OK")
