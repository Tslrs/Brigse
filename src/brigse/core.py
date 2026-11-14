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


def simulate(sources: list[str], top: str | None, cwd: str | None, timeout_s: int) -> dict:
    """Compile with Icarus Verilog and run the testbench."""
    iverilog = _need("iverilog", "brew install icarus-verilog")
    vvp = _need("vvp", "brew install icarus-verilog")
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    if not base.is_dir():
        raise BackendError(f"Folder not found: {base}")
    missing = [s for s in sources if not (base / s).is_file()]
    if not sources or missing:
        raise BackendError(f"Source files not found in {base}: {missing or 'none given'}")

    with tempfile.TemporaryDirectory() as tmp:
        image = str(Path(tmp) / "sim.vvp")
        args = [iverilog, "-g2012", "-o", image] + (["-s", top] if top else []) + sources
        rc, out = _proc(args, base, 120)
        if rc != 0:
            return {"compiled": False, "passed": False, "output": out[-TAIL:]}
        rc, out = _proc([vvp, "-n", image], base, max(1, min(timeout_s, 600)))

    if rc is None:
        return {"compiled": True, "passed": False, "timed_out": True,
                "output": out[-TAIL:], "hint": "Does the testbench call $finish?"}
    failed = bool(re.search(r"\b(FAIL(ED)?|ERROR|ASSERT(ION)? FAILED)\b", out)) or rc != 0
    return {"compiled": True, "passed": not failed, "exit_code": rc, "output": out[-TAIL:],
            "pass_rule": "exit code 0 and no FAIL/ERROR text in the output"}


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
