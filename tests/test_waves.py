"""Check the waveform recording, the text view for agents and the dashboard endpoint. Needs iverilog.

usage: uv run python tests/test_waves.py
"""
import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

HOME = tempfile.mkdtemp()
os.environ["BRIGSE_HOME"] = HOME                      # before the imports: the state folder is read at import
from brigse import actions, core, dashboard, vcd      # noqa: E402

BLINKY = Path(__file__).resolve().parent.parent / "examples" / "blinky"
failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


# 1. the reader, on a hand-written file with every awkward case
sample = Path(HOME) / "sample.vcd"
sample.write_text("""$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 8 " bus [7:0] $end
$var wire 1 # alias_of_clk $end
$var parameter 32 $ WIDTH $end
$upscope $end
$enddefinitions $end
#0
0!
b101 "
0#
b1000 $
#5
1!
1#
bx "
#10
b1x0z0000 "
0!
#12
""")
v = vcd.parse(sample)
names = [s["name"] for s in v.signals]
check("parameters are left out", names == ["tb.clk", "tb.bus", "tb.alias_of_clk"])
check("a short vector is padded with 0", v.changes[1][0] == (0, "00000101"))
check("a vector that starts with x is padded with x", v.changes[1][1] == (5, "xxxxxxxx"))
check("x and z are kept inside a vector", v.changes[1][2][1] == "1x0z0000")
check("a signal sharing an id gets its own changes", v.changes[2] == [(0, "0"), (5, "1")])
check("timescale and end are read", v.timescale == "1ns" and v.end_time == 12)
t = vcd.table(v, ["bus"], 0, None, 10)
check("the text view shows hex, and marks x", [r["changed"]["bus"] for r in t["rows"]] == ["0x5", "bxxxxxxxx", "b1x0z0000"])

# 2. a real simulation records itself with no change to the testbench
r = actions.simulate(["blinky.v", "tb_blinky.v"], None, str(BLINKY), 60, True, "agent")
check("the testbench passes", r["passed"] and "PASS" in r["output"])
check("the testbench module was found by its name", dashboard.store.get_sim(r["sim_id"])["top"] == "tb_blinky")
check("the output does not show the recording's path", "VCD info" not in r["output"])
check("the signals were recorded", "tb_blinky.dut.count" in r["waves"]["signals"] and r["waves"]["end_time"] == 216000)
rows = actions.waves(r["sim_id"], ["led", "dut.count"], 0, None, 50, "tb_blinky.clk")
last = rows["rows"][-1]["values"]
check("one row per rising edge of the clock", rows["rises"] == 22 and len(rows["rows"]) == 22)
check("the counter counts and the LEDs follow bits 5:2", last["dut.count"] == "0x14" and last["led"] == "0x5")
check("an unknown clock is reported", "error" in actions.waves(r["sim_id"], None, 0, None, 5, "nope"))

# 3. guessing the testbench, and asking for waveforms without one
many = Path(HOME) / "many"
many.mkdir()
(many / "a.v").write_text("module tb_one; endmodule\nmodule tb_two; endmodule\n")
check("two testbenches are not guessed", core._guess_top(many, ["a.v"]) is None)
check("a design alone is not taken for a testbench", core._guess_top(BLINKY, ["blinky.v"]) is None)

# 4. the dashboard serves the recording
dashboard.start_in_background(8796)
state = json.loads(urllib.request.urlopen("http://127.0.0.1:8796/api/state").read())
sim = state["sims"][0]
check("the dashboard lists the simulation with its waves", sim["has_waves"] and sim["top"] == "tb_blinky" and sim["passed"])
data = json.loads(urllib.request.urlopen(f"http://127.0.0.1:8796/api/sims/{sim['id']}/waves.json").read())
led = next(s for s in data["signals"] if s["name"] == "tb_blinky.led")
check("the dashboard gets the signals as JSON", led["width"] == 4 and led["changes"][-1][1] == "0101")
try:
    urllib.request.urlopen("http://127.0.0.1:8796/api/sims/not-a-real-id/waves.json")
    check("an unknown simulation is refused", False)
except urllib.error.HTTPError as e:
    check("an unknown simulation is refused", e.code == 404)

shutil.rmtree(HOME, ignore_errors=True)
assert not failures, f"failed: {failures}"
print("\nWAVES OK")
