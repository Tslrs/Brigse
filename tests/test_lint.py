"""The lint tool: errors and warnings with file and line, with Verilator and with the Icarus fallback.

Needs iverilog or verilator (Verilator-only checks are skipped without it).
usage: uv run python tests/test_lint.py
"""
import shutil
import tempfile
from pathlib import Path

from brigse import core
from brigse.backends.base import BackendError

work = Path(tempfile.mkdtemp())
(work / "good.v").write_text("module good(input clk, output reg q);\n  always @(posedge clk) q <= ~q;\nendmodule\n")
(work / "semi.v").write_text("module semi(input clk, output reg q);\n  reg c\n  always @(posedge clk) q <= ~q;\nendmodule\n")
(work / "width.v").write_text("module width(input clk, output reg [3:0] q);\n  wire [7:0] w = 8'd5;\n  always @(posedge clk) q <= w;\nendmodule\n")
(work / "unused.v").write_text("module unused(input clk, output reg q);\n  wire dead;\n  always @(posedge clk) q <= ~q;\nendmodule\n")
failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def lint(*files, **kw):
    return core.lint(list(files) or None, kw.pop("top", None), str(work), **kw)


real = shutil.which
for tool in ("verilator", "iverilog"):
    if tool == "verilator" and not real("verilator"):
        print("SKIPPED the Verilator checks: not installed")
        continue
    # make only this tool visible
    shutil.which = lambda n, _t=tool: None if n == "verilator" and _t == "iverilog" else real(n)
    r = lint("good.v")
    check(f"[{tool}] a good file is ok", r["ok"] and r["tool"] == tool and r["issues"] == [])
    r = lint("semi.v")
    i = r["issues"][0]
    check(f"[{tool}] a missing ';' is an error with file and line", not r["ok"] and i["file"] == "semi.v" and i["line"] in (2, 3))  # tools blame line 2 or the one after
    check(f"[{tool}] the source line is shown", "always" in i.get("source", "") or "reg c" in i.get("source", ""))
    check(f"[{tool}] a syntax error comes with the previous-line hint", "previous line" in r.get("hint", ""))
    if tool == "verilator":
        r = lint("width.v")
        check("[verilator] a width mismatch is a warning, not an error", r["ok"] and r["warnings"] == 1 and r["issues"][0]["code"] == "WIDTHTRUNC")
        check("[verilator] style warnings only with strict", lint("unused.v")["issues"] == []
              and any(x["code"] == "UNUSEDSIGNAL" for x in lint("unused.v", strict=True)["issues"]))
        check("[verilator] with no file list, every file in the folder is checked", lint(top="good")["tool"] == "verilator")
    else:
        check("[iverilog] the answer says width mistakes are not checked", "not checked" in lint("good.v")["note"])
shutil.which = real

for bad in (lambda: core.lint(["nope.v"], None, str(work)), lambda: core.lint(None, None, str(work / "no"))):
    try:
        bad()
        check("missing files or folder raise a readable error", False)
    except BackendError:
        check("missing files or folder raise a readable error", True)

assert not failures, failures
print("\nLINT OK")
