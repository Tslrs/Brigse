"""Runs one cocotb test with Icarus Verilog. Started by Brigse inside the cocotb environment, not imported.

argv[1] is JSON: {sources, toplevel, module, testcase, parameters, build_dir, cwd}.
Prints BRIGSE_RESULTS <path of results.xml> when the simulation ran.
"""
import json
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

a = json.loads(sys.argv[1])
runner = get_runner("icarus")
runner.build(sources=a["sources"], hdl_toplevel=a["toplevel"], build_dir=a["build_dir"], always=True,
             parameters=a["parameters"], timescale=("1ns", "1ps"), cwd=a["cwd"])
try:
    runner.test(test_module=a["module"], hdl_toplevel=a["toplevel"], testcase=a["testcase"], build_dir=a["build_dir"],
                test_dir=a["cwd"], parameters=a["parameters"], timescale=("1ns", "1ps"),
                results_xml=str(Path(a["build_dir"]) / "results.xml"), extra_env={"PYTHONDONTWRITEBYTECODE": "1"})
except BaseException as e:           # a failing test may be raised as an error: the results file is what counts
    print(f"cocotb runner: {type(e).__name__}: {e}", file=sys.stderr)
xml = Path(a["build_dir"]) / "results.xml"
if xml.is_file():
    print(f"BRIGSE_RESULTS {xml}")
