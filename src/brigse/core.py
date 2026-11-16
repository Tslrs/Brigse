"""Core features that work for every board: simulation and programming."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .backends.base import BackendError

TAIL = 6000


def _need(tool: str, hint: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise BackendError(f"'{tool}' is not installed. {hint}")
    return path


def _proc(args: list[str], cwd: Path | None, timeout: int) -> tuple[int | None, str]:
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).decode(errors="replace")
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"") + (e.stderr or b"")).decode(errors="replace")
        return None, out


def _guess_top(base: Path, sources: list[str]) -> str | None:
    """The testbench: the only module whose name looks like one (tb_x, x_tb, test_x, x_test)."""
    names = []
    for src in sources:
        names += re.findall(r"^\s*module\s+(\w+)", (base / src).read_text(errors="replace"), re.M)
    bench = [n for n in names if re.search(r"^(tb_|test)|(_tb|_test)$", n, re.I)]
    return bench[0] if len(bench) == 1 else None


def simulate(sources: list[str], top: str | None, cwd: str | None, timeout_s: int,
             waves_dir: Path | None = None) -> dict:
    """Compile with Icarus Verilog and run the testbench, optionally recording every signal."""
    iverilog = _need("iverilog", "brew install icarus-verilog")
    vvp = _need("vvp", "brew install icarus-verilog")
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    if not base.is_dir():
        raise BackendError(f"Folder not found: {base}")
    missing = [s for s in sources if not (base / s).is_file()]
    if not sources or missing:
        raise BackendError(f"Source files not found in {base}: {missing or 'none given'}")

    note, record = None, None
    if waves_dir is not None:
        top = top or _guess_top(base, sources)
        if top:
            waves_dir.mkdir(parents=True, exist_ok=True)
            record = waves_dir / "waves.vcd"
        else:
            note = "pass `top` (the testbench module) to record waveforms"
    before = {p: p.stat().st_mtime for p in base.glob("*.vcd")}

    with tempfile.TemporaryDirectory() as tmp:
        image = str(Path(tmp) / "sim.vvp")
        extra = []
        if record:
            dump = Path(tmp) / "brigse_dump.v"
            dump.write_text(f"module brigse_dump;\n  initial begin\n    $dumpfile(\"{record}\");\n"
                            f"    $dumplimit(30000000);\n    $dumpvars(0, {top});\n  end\nendmodule\n")
            extra = [str(dump)]
        roots = ["-s", top, "-s", "brigse_dump"] if record else (["-s", top] if top else [])
        rc, out = _proc([iverilog, "-g2012", "-o", image] + roots + sources + extra, base, 120)
        if rc != 0:
            return {"compiled": False, "passed": False, "output": out[-TAIL:]}
        rc, out = _proc([vvp, "-n", image], base, max(1, min(timeout_s, 600)))

    result: dict
    if rc is None:
        result = {"compiled": True, "passed": False, "timed_out": True, "output": out[-TAIL:],
                  "hint": "Does the testbench call $finish?"}
    else:
        failed = bool(re.search(r"\b(FAIL(ED)?|ERROR|ASSERT(ION)? FAILED)\b", out)) or rc != 0
        result = {"compiled": True, "passed": not failed, "exit_code": rc, "output": out[-TAIL:],
                  "pass_rule": "exit code 0 and no FAIL/ERROR text in the output"}
    out = "\n".join(l for l in out.splitlines() if not l.startswith("VCD info:")) + "\n"
    if isinstance(result.get("output"), str):
        result["output"] = out[-TAIL:]
    if record and not record.is_file():
        # the testbench started its own dump first; take the file it wrote
        for vcd in base.glob("*.vcd"):
            if vcd.stat().st_mtime > before.get(vcd, 0):
                shutil.copyfile(vcd, record)
                break
    result["top"] = top
    if record and record.is_file():
        result["vcd"] = str(record)
    elif note:
        result["waves_note"] = note
    return result

VERILOG_EXT = {".v", ".sv", ".vh", ".svh"}
SYNTAX_HINT = ("A syntax error is often reported on the line after the real mistake: look at the end of the "
               "previous line (a missing ';', ')' or 'end').")


def _issue(severity, code, file, line, col, message, base):
    issue = {"severity": severity, "code": code, "file": file, "line": line, "column": col,
             "message": " ".join(message.split())}
    try:
        issue["source"] = (base / file).read_text(errors="replace").splitlines()[line - 1].strip()[:200]
    except (OSError, IndexError):
        pass
    return issue


def lint(sources: list[str] | None, top: str | None, cwd: str | None, strict: bool = False,
         timeout_s: int = 30) -> dict:
    """Check Verilog/SystemVerilog in about a second, before a build that takes minutes.

    Uses Verilator when it is installed (--lint-only), otherwise Icarus Verilog's parser, which finds
    syntax and undeclared-name errors but not width mistakes. Nothing is written to the project.
    """
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    if not base.is_dir():
        raise BackendError(f"Folder not found: {base}")
    files = sources or sorted(p.name for p in base.iterdir() if p.is_file() and p.suffix.lower() in VERILOG_EXT)
    missing = [f for f in files if not (base / f).is_file()]
    if not files or missing:
        raise BackendError(f"Source files not found in {base}: {missing or 'none given'}")

    verilator = shutil.which("verilator")
    issues: list[dict] = []
    if verilator:
        args = [verilator, "--lint-only", "--timing", "-Wno-fatal"] + (["-Wall"] if strict else [])
        args += (["--top-module", top] if top else []) + files
        rc, out = _proc(args, base, max(1, min(timeout_s, 300)))
        pat = re.compile(r"^%(Error|Warning)(?:-(\w+))?: ([^:\s]+):(\d+):(\d+): (.*)$")
        for line in out.splitlines():
            m = pat.match(line)
            if m:
                sev, code, f, ln, col, msg = m.groups()
                issues.append(_issue("error" if sev == "Error" else "warning", code or "ERROR", f, int(ln), int(col), msg, base))
        if rc is None:
            raise BackendError(f"The linter did not finish in {timeout_s} s.")
        tool = "verilator"
        if rc != 0 and not any(i["severity"] == "error" for i in issues):
            issues.append({"severity": "error", "code": "ERROR", "message": out.strip()[-500:]})
    else:
        iverilog = _need("iverilog", "brew install verilator  (or icarus-verilog)")
        args = [iverilog, "-g2012", "-t", "null"] + (["-s", top] if top else []) + (["-Wall"] if strict else []) + files
        rc, out = _proc(args, base, max(1, min(timeout_s, 300)))
        if rc is None:
            raise BackendError(f"The linter did not finish in {timeout_s} s.")
        pat = re.compile(r"^([^:\s]+):(\d+): (?:(error|warning): )?(.*)$")
        for line in out.splitlines():
            m = pat.match(line)
            if m:
                f, ln, sev, msg = m.groups()
                issues.append(_issue("warning" if sev == "warning" else "error", "IVERILOG", f, int(ln), None, msg, base))
        tool = "iverilog"
    errors = sum(i["severity"] == "error" for i in issues)
    result = {"tool": tool, "ok": errors == 0, "errors": errors, "warnings": len(issues) - errors, "issues": issues[:60]}
    if len(issues) > 60:
        result["truncated"] = len(issues) - 60
    if any("syntax" in i["message"].lower() for i in issues):
        result["hint"] = SYNTAX_HINT
    if tool == "iverilog":
        result["note"] = "Verilator is not installed, so width and latch mistakes are not checked. brew install verilator"
    elif not strict:
        result["note"] = "Default checks only; pass strict=true for style warnings (unused signals and the like)."
    return result


def cocotb(sources: list[str], toplevel: str, test_module: str, cwd: str | None, testcase: str | None = None,
           parameters: dict | None = None, timeout_s: int = 120) -> dict:
    """Run a cocotb test (a Python testbench) against Verilog with Icarus Verilog.

    cocotb lives in its own environment (`brigse install-cocotb`), so it works whatever Python Brigse runs on.
    """
    import json
    import xml.etree.ElementTree as ET
    from . import cocotb_env
    py = cocotb_env.python()
    if not py:
        raise BackendError("cocotb is not installed. Ask the user to run `brigse install-cocotb` (about 40 MB); "
                           "Brigse does not download it by itself.")
    _need("iverilog", "brew install icarus-verilog")
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    if not base.is_dir():
        raise BackendError(f"Folder not found: {base}")
    missing = [s for s in sources if not (base / s).is_file()]
    if not sources or missing:
        raise BackendError(f"Source files not found in {base}: {missing or 'none given'}")
    if not (base / f"{test_module}.py").is_file():
        raise BackendError(f"Test module not found: {base / (test_module + '.py')}. "
                           "test_module is the file name without .py, in the same folder.")
    with tempfile.TemporaryDirectory() as tmp:
        args = {"sources": [str(base / s) for s in sources], "toplevel": toplevel, "module": test_module,
                "testcase": testcase, "parameters": parameters or {}, "build_dir": str(Path(tmp) / "build"), "cwd": str(base)}
        rc, out = _proc([str(py), str(Path(__file__).with_name("cocotb_runner.py")), json.dumps(args)], base, max(1, min(timeout_s, 900)))
        out = re.sub(r"\x1b\[[0-9;]*m", "", out)
        found = re.search(r"^BRIGSE_RESULTS (.+)$", out, re.M)
        if rc is None:
            return {"ran": False, "passed": False, "timed_out": True, "output": out[-TAIL:],
                    "hint": "A cocotb test that waits for a signal forever never ends: give it a timeout, e.g. "
                            "@cocotb.test(timeout_time=1, timeout_unit='ms')."}
        if not found:
            none = "No tests were discovered" in out
            return {"ran": False, "passed": False, "output": out[-TAIL:],
                    "hint": ("No tests were found: functions need the @cocotb.test() decorator." if none else
                             "The build or the import of the test module failed; the output says where.")}
        tests = []
        for case in ET.parse(found.group(1)).getroot().iter("testcase"):
            bad = case.find("failure") if case.find("failure") is not None else case.find("error")
            status = "failed" if bad is not None else "skipped" if case.find("skipped") is not None else "passed"
            item = {"name": case.get("name"), "status": status, "seconds": round(float(case.get("time") or 0), 3)}
            if bad is not None:
                item["message"] = (bad.get("message") or "")[:300]
                item["detail"] = (bad.text or "").strip()[-1200:]
            tests.append(item)
    failed = [t for t in tests if t["status"] == "failed"]
    result = {"ran": True, "passed": bool(tests) and not failed, "tests": tests, "failed": len(failed),
              "total": len(tests), "output": out[-TAIL:]}
    if not tests:
        result["hint"] = "No tests were found: functions need the @cocotb.test() decorator."
    return result


def detect_boards(cable: str | None) -> dict:
    """List USB probes and, if a cable is named, the JTAG chain behind it."""
    ofl = _need("openFPGALoader", "brew install openfpgaloader")
    _, usb = _proc([ofl, "--scan-usb"], None, 30)
    result: dict = {"usb_probes": usb.strip()}
    rc, chain = _proc([ofl, "--detect"] + (["-c", cable] if cable else []), None, 30)
    result["jtag_detect"] = {"ok": rc == 0, "output": chain.strip()[-2000:]}
    return result


def _loader_knows(ofl: str, part: str) -> bool:
    _, table = _proc([ofl, "--list-fpga"], None, 30)
    device = part.split("-")[0].lower()
    return any(device == cols[3].lower() for cols in
               (line.split() for line in table.splitlines()) if len(cols) >= 4)


def program(bitstream: str, cable: str | None, board: str | None, part: str | None,
            write_flash: bool, confirm: bool) -> dict:
    """Load a bitstream with openFPGALoader. Nothing is written unless confirm is true."""
    ofl = _need("openFPGALoader", "brew install openfpgaloader")
    bit = Path(bitstream).expanduser().resolve()
    if not bit.is_file():
        raise BackendError(f"Bitstream not found: {bit}")
    if not cable and not board:
        raise BackendError("Give either 'cable' or 'board' (see openFPGALoader --list-cables / --list-boards).")
    if part and not _loader_knows(ofl, part):
        raise BackendError(
            f"openFPGALoader does not list device '{part.split('-')[0]}' as supported, so it cannot "
            "program it from this machine. Use the vendor programmer for this device.")

    args = [ofl] + (["-b", board] if board else []) + (["-c", cable] if cable else [])
    if write_flash:
        args.append("-f")
    args.append(str(bit))
    target = "flash (persistent)" if write_flash else "SRAM (lost at power-off)"
    if not confirm:
        return {"dry_run": True, "would_run": " ".join(args), "target": target,
                "note": "Nothing was written. Call again with confirm=true to program."}
    rc, out = _proc(args, None, 600)
    return {"dry_run": False, "ok": rc == 0, "target": target, "output": out[-TAIL:]}
