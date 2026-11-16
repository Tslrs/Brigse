"""The cocotb_test tool: Python testbenches against Verilog. Skips itself when cocotb is not installed
(`brigse install-cocotb`) or iverilog is missing.

usage: uv run python tests/test_cocotb.py
"""
import os
import shutil
import tempfile
from pathlib import Path

from brigse import cocotb_env, core
from brigse.backends.base import BackendError

if not cocotb_env.python() or not shutil.which("iverilog"):
    print("SKIPPED: needs `brigse install-cocotb` and iverilog")
    raise SystemExit(0)

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "blinky"
work = Path(tempfile.mkdtemp())
(work / "adder.v").write_text("module adder(input [3:0] a, input [3:0] b, output [4:0] s);\n  assign s = a + b;\nendmodule\n")
(work / "t_adder.py").write_text('''import cocotb
from cocotb.triggers import Timer

@cocotb.test()
async def adds(dut):
    for a, b in ((1, 2), (15, 15)):
        dut.a.value, dut.b.value = a, b
        await Timer(1, unit="ns")
        assert int(dut.s.value) == a + b, f"{a}+{b} gave {int(dut.s.value)}"

@cocotb.test()
async def wrong_on_purpose(dut):
    dut.a.value, dut.b.value = 2, 2
    await Timer(1, unit="ns")
    assert int(dut.s.value) == 5, f"2+2 is not 5, the design said {int(dut.s.value)}"
''')
(work / "t_broken.py").write_text("import cocotb\nimport no_such_module\n")
(work / "t_empty.py").write_text("x = 1\n")
failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


r = core.cocotb(["blinky.v"], "blinky", "test_blinky", str(EXAMPLE), parameters={"WIDTH": 6})
check("the example passes with a small counter", r["passed"] and r["total"] == 2 and r["failed"] == 0)
check("nothing is written into the project", not (EXAMPLE / "results.xml").exists() and not (EXAMPLE / "sim_build").exists())

r = core.cocotb(["adder.v"], "adder", "t_adder", str(work))
by = {t["name"]: t for t in r["tests"]}
check("one failing test fails the run", r["ran"] and not r["passed"] and r["failed"] == 1 and r["total"] == 2)
check("the passing test is reported as passed", by["adds"]["status"] == "passed")
check("the failure carries the assertion message", "2+2 is not 5" in by["wrong_on_purpose"]["message"] + by["wrong_on_purpose"]["detail"])
r = core.cocotb(["adder.v"], "adder", "t_adder", str(work), testcase="adds")
check("a single test can be chosen", r["passed"] and r["total"] == 1)

r = core.cocotb(["adder.v"], "adder", "t_broken", str(work))
check("a test module that does not import says so", not r["ran"] and "no_such_module" in r["output"] and "hint" in r)
r = core.cocotb(["adder.v"], "adder", "t_empty", str(work))
check("a module with no tests is not a pass, and the hint says why", not r["passed"] and "cocotb.test" in r.get("hint", ""))

for name, fn in {"a missing test file": lambda: core.cocotb(["adder.v"], "adder", "nope", str(work)),
                 "a missing source": lambda: core.cocotb(["no.v"], "adder", "t_adder", str(work))}.items():
    try:
        fn()
        check(name + " is a readable error", False)
    except BackendError:
        check(name + " is a readable error", True)

os.environ["BRIGSE_COCOTB_PYTHON"] = str(work / "nothing")
try:
    core.cocotb(["adder.v"], "adder", "t_adder", str(work))
    check("without cocotb the answer says how to install it", False)
except BackendError as e:
    check("without cocotb the answer says how to install it", "brigse install-cocotb" in str(e))

assert not failures, failures
print("\nCOCOTB OK")
