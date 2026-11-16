"""Utilisation per module. The adding-up is checked on a hand-written yosys report (no tools needed); a real
yosys run, when the open-source add-on is installed, must give the same totals as yosys' own.

usage: uv run python tests/test_hierarchy.py
"""
import json
import subprocess
import tempfile
from pathlib import Path

from brigse import hierarchy, oss_suite

failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


# 1. names
check("a parameterised module is named by its parameters",
      hierarchy.pretty("$paramod\\counter\\W=s32'00000000000000000000000000010000") == "counter (W=16)")
check("two parameters", hierarchy.pretty("$paramod\\fifo\\W=s32'00000000000000000000000000001000\\D=s32'00000000000000000000000000000100") == "fifo (W=8, D=4)")
check("a hashed parameterised name", hierarchy.pretty("$paramod$3f2a9c\\cpu\\N=s32'00000000000000000000000000000010") == "cpu (N=2)")
check("a plain module loses yosys' backslash", hierarchy.pretty("\\top") == "top")

# 2. adding up: top has 2 x core, core has 3 x cell, cell is 1 LUT4 + 2 DFF
stat = {"modules": {
    "\\top": {"num_cells_by_type": {"core": 2, "LUT4": 1, "IBUF": 1, "GND": 1}},
    "\\core": {"num_cells_by_type": {"cell": 3, "ALU": 4, "DFF": 1}},
    "\\cell": {"num_cells_by_type": {"LUT4": 1, "DFFR": 2, "VCC": 1}},
    "\\unused": {"num_cells_by_type": {"LUT4": 99}},
}}
r = hierarchy.summarise(stat, "top")
by = {m["module"]: m for m in r["modules"]}
check("instances are counted through the hierarchy", (by["top"]["instances"], by["core"]["instances"], by["cell"]["instances"]) == (1, 2, 6))
check("a module nothing instantiates is left out", "unused" not in by)
check("own use excludes the submodules", by["core"]["own"] == {"carry_alus": 4, "flip_flops": 1})
check("one instance includes what it contains", by["core"]["with_submodules"] == {"luts": 3, "carry_alus": 4, "flip_flops": 7})
check("the design total is the top with everything in it",
      r["design_total"] == {"luts": 7, "carry_alus": 8, "flip_flops": 14, "io_buffers": 1})
check("GND and VCC are not resources", "GND" not in json.dumps(r) and "VCC" not in json.dumps(r))
check("the in_design figures add up to the total", all(sum(m["in_design"].get(k, 0) for m in r["modules"]) == r["design_total"].get(k, 0)
                                                        for k in hierarchy.KINDS))
check("shares are of the logic and sorted, biggest first", [m["module"] for m in r["modules"]] == ["core", "cell", "top"]
      and by["core"]["share_of_logic_percent"] == 53.3 and abs(sum(m["share_of_logic_percent"] for m in r["modules"]) - 100) < 0.5)
check("kinds: BSRAM, multiplier, latch", (hierarchy.kind("DPX9B"), hierarchy.kind("MULT18X18"), hierarchy.kind("DLATCH"), hierarchy.kind("MUX2_LUT5"))
      == ("block_rams", "dsps", "flip_flops", None))

# 3. a real run
if not oss_suite.installed(("yosys",)):
    print("SKIPPED the yosys run: the gowin_oss add-on is not installed")
else:
    work = Path(tempfile.mkdtemp())
    (work / "top.v").write_text("""module counter #(parameter W = 16) (input clk, input rst, output [W-1:0] q);
  reg [W-1:0] c; always @(posedge clk) c <= rst ? 0 : c + 1; assign q = c;
endmodule
module ram(input clk, input [9:0] a, input [15:0] d, output reg [15:0] o);
  reg [15:0] m [0:1023]; always @(posedge clk) begin m[a] <= d; o <= m[a+1]; end
endmodule
module top(input clk, input rst, output [3:0] led);
  wire [15:0] q1, q2, o;
  counter #(16) c1(clk, rst, q1);
  counter #(16) c2(clk, rst, q2);
  ram r(clk, q1[9:0], q2, o);
  assign led = ~{^o, q1[0], q2[0], q1[1]};
endmodule
""")
    real = hierarchy.analyse(["top.v"], "top", str(work))
    by = {m["module"]: m for m in real["modules"]}
    check("the same module twice is one row with two instances", by["counter (W=16)"]["instances"] == 2)
    check("the memory is found as block RAM, in the ram module", by["ram"]["own"].get("block_rams") == 1)
    check("the counter's flip-flops are per instance and in total", by["counter (W=16)"]["own"]["flip_flops"] == 16
          and by["counter (W=16)"]["in_design"]["flip_flops"] == 32)
    stat_file = work / "s.json"
    subprocess.run([str(oss_suite.tool("yosys")), "-q", "-p", f"read_verilog -sv top.v; hierarchy -top top; synth_gowin -top top -noflatten; "
                    f"tee -q -o {stat_file} stat -json -top top"], cwd=work, env=oss_suite.env(), capture_output=True, check=True)
    design = json.loads(stat_file.read_text())["design"]["num_cells_by_type"]
    own = {k: 0 for k in hierarchy.KINDS}
    for cell, n in design.items():
        if hierarchy.kind(cell):
            own[hierarchy.kind(cell)] += n
    check("the totals equal yosys' own design totals", {k: v for k, v in own.items() if v} == real["design_total"])
    try:
        hierarchy.analyse(["top.v"], "no_such_top", str(work))
        check("an unknown top is a readable error", False)
    except Exception as e:
        check("an unknown top is a readable error", "yosys could not synthesise" in str(e))

assert not failures, failures
print("\nHIERARCHY OK")
