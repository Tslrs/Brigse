"""Add-on: Xilinx ISE 14.7 for Spartan-3 / 3A / 3E, run inside a small headless
x86-64 Linux VM managed by Lima.

ISE has no macOS build and cannot run under user-mode x86 translators, so the
tools live in a VM (default name ``ise``). A build copies the project into the
VM, runs xst -> ngdbuild -> map -> par -> trce -> bitgen in the background, and
copies the results back when it has finished.
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from .. import diagnose, store
from .base import Backend, BackendError

VM = os.environ.get("BRIGSE_ISE_VM", "ise")


def _vm_home() -> str | None:
    """Where Lima keeps the VM: BRIGSE_VM_HOME, else the vm/ folder next to the source, else Lima's own default."""
    if os.environ.get("BRIGSE_VM_HOME"):
        return os.environ["BRIGSE_VM_HOME"]
    root = Path(__file__).resolve().parents[3]
    return str(root / "vm") if (root / "pyproject.toml").is_file() and (root / "vm").is_dir() else None


VM_HOME = _vm_home()


def _env() -> dict | None:
    return {**os.environ, "LIMA_HOME": VM_HOME} if VM_HOME else None
ISE_ROOT = "/opt/Xilinx/14.7/ISE_DS"
JOBS_LOCAL = store.JOBS
STEPS = ("xst", "ngdbuild", "map", "par", "trce", "bitgen")

# Things in a project folder that the build does not need (old outputs, media).
SKIP_DIRS = {".git", "build_out", "_ngo", "xst", "_xmsgs", "iseconfig", "xlnx_auto_0_xdb", "__pycache__"}
SKIP_EXT = {".ncd", ".bit", ".xdl", ".mcs", ".ngd", ".ngm", ".bgn", ".drc", ".xwbt", ".twr", ".twx",
            ".par", ".pad", ".html", ".log", ".xrpt", ".ngr", ".unroutes", ".xpi", ".ptwx", ".mrp",
            ".syr", ".lso", ".stx", ".cmd_log", ".tar", ".zip", ".mp4", ".pptx", ".jpg", ".png",
            ".pcf", ".map", ".psr", ".xml", ".csv", ".bin", ".lst"}
SOURCE_EXT = {".v": "verilog", ".vhd": "vhdl", ".vhdl": "vhdl"}
OUTPUT_GLOBS = ("{top}.bit", "{top}.twr", "{top}_map.mrp", "{top}.par", "{top}.syr",
                "{top}.bgn", "{top}_pad.txt", "*.log", "job.status")

RUN_SH = r"""#!/bin/bash
export XILINX_DS=%(ise)s
export XILINX=$XILINX_DS/ISE
export PATH=$XILINX/bin/lin64:$XILINX_DS/common/bin/lin64:$PATH
export LD_LIBRARY_PATH=$XILINX/lib/lin64:$XILINX_DS/common/lib/lin64
cd "$(dirname "$0")"
mkdir -p xst/projnav.tmp _ngo
step() {
  local n=$1; shift; local s=$(date +%%s)
  echo "STEP $n START $s" >> job.status
  "$@" > "$n.log" 2>&1 < /dev/null; local rc=$?
  echo "STEP $n END $rc $(( $(date +%%s) - s ))" >> job.status
  [ $rc -eq 0 ] || { echo "DONE $rc" >> job.status; exit $rc; }
}
step xst      xst -intstyle xflow -ifn %(top)s.xst -ofn %(top)s.syr
step ngdbuild ngdbuild -intstyle xflow -dd _ngo %(sd)s -nt timestamp %(uc)s -p %(part)s %(top)s.ngc %(top)s.ngd
step map      map -intstyle xflow -p %(part)s -cm area -ir off -pr off -c 100 -o %(top)s_map.ncd %(top)s.ngd %(top)s.pcf
step par      par -w -intstyle xflow -ol std -t 1 %(top)s_map.ncd %(top)s.ncd %(top)s.pcf
step trce     trce -intstyle xflow -v 3 -s %(speed)s -n 3 -fastpaths -xml %(top)s.twx %(top)s.ncd -o %(top)s.twr %(top)s.pcf %(trce_ucf)s
step bitgen   bitgen -intstyle xflow %(ut)s %(top)s.ncd
echo "DONE 0" >> job.status
"""

XST_TEMPLATE = """set -tmpdir "xst/projnav.tmp"
set -xsthdpdir "xst"
run
-ifn {top}.prj
-ifmt mixed
-ofn {top}
-ofmt NGC
-p {xst_part}
-top {top}
-opt_mode Speed
-opt_level 1
"""


def _run(args: list[str], *, input: bytes | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, input=input, capture_output=True, timeout=timeout, env=_env())
    except FileNotFoundError:
        raise BackendError("limactl not found. Install Lima: brew install lima lima-additional-guestagents qemu")
    except subprocess.TimeoutExpired:
        raise BackendError(f"Timed out after {timeout}s running: {' '.join(args[:4])}")


def _vm_state() -> str:
    """'Running', 'Stopped', or '' when the VM does not exist."""
    p = _run(["limactl", "list", "--format", "{{.Name}} {{.Status}}"], timeout=30)
    for line in p.stdout.decode().splitlines():
        name, _, state = line.partition(" ")
        if name == VM:
            return state.strip()
    return ""


def _sh(script: str, *, timeout: int = 120) -> str:
    """Run a bash script inside the VM and return its stdout."""
    p = _run(["limactl", "shell", "--workdir", "/tmp", VM, "bash", "-s"],
             input=script.encode(), timeout=timeout)
    if p.returncode != 0:
        raise BackendError(f"Command failed in VM '{VM}': {p.stderr.decode()[-400:]}")
    return p.stdout.decode()


def _ensure_running() -> None:
    state = _vm_state()
    if not state:
        raise BackendError(f"Lima VM '{VM}' does not exist. See README, section 'Spartan-3A add-on'.")
    if state != "Running":
        p = _run(["limactl", "start", "--tty=false", VM], timeout=600)
        if p.returncode != 0:
            raise BackendError(f"Could not start VM '{VM}': {p.stderr.decode()[-400:]}")
    if "ok" not in _sh(f"test -x {ISE_ROOT}/ISE/bin/lin64/xst && echo ok || echo missing"):
        raise BackendError(f"ISE not found at {ISE_ROOT} inside VM '{VM}'.")


def parse_part(part: str) -> tuple[str, str, str]:
    """Accept xc3s700a-fg484-4 or xc3s700a-4-fg484; return (device, package, speed)."""
    m = re.fullmatch(r"(xc3s\w+?)-(?:(\d)-([a-z]{2,3}\d+)|([a-z]{2,3}\d+)-(\d))", part.strip(), re.I)
    if not m:
        raise BackendError(f"Bad part '{part}'. Expected device-package-speed, e.g. xc3s700a-fg484-4.")
    dev = m.group(1).lower()
    pkg = (m.group(3) or m.group(4)).lower()
    spd = m.group(2) or m.group(5)
    return dev, pkg, spd


def _safe_name(s: str, what: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.\-/]+", s) or ".." in s or s.startswith("/"):
        raise BackendError(f"Unsafe {what}: '{s}'")
    return s


class IseSpartan3Backend(Backend):
    name = "ise_spartan3"
    has_runtime = True
    title = "Xilinx ISE 14.7 for Spartan-3/3A/3E (Lima VM)"
    part_patterns = (r"xc3s",)
    size_hint = "about 4 GB (small x86 VM + slim ISE taken from your own install)"

    # ---- status / control -------------------------------------------------
    def status(self) -> dict:
        if not shutil.which("limactl"):
            return {"installed": False, "ready": False, "detail": "Lima is not installed.",
                    "install_hint": "brew install lima lima-additional-guestagents qemu"}
        state = _vm_state()
        if not state:
            return {"installed": False, "ready": False, "detail": f"Lima VM '{VM}' not found.",
                    "install_hint": "Create the VM and unpack the slim ISE (see README)."}
        return {"installed": True, "ready": state == "Running",
                "detail": f"VM '{VM}' is {state}. It is started automatically by build (about 90 s).",
                "install_hint": ""}

    def install_plan(self) -> dict:
        return {"what": "A small headless x86 Debian VM (Lima/QEMU) holding a slimmed copy of ISE 14.7",
                "source": "Lima, QEMU and Debian are downloaded; ISE comes from YOUR OWN installation",
                "disk": self.size_hint,
                "licence": "ISE WebPACK is free but needs your own AMD account and licence file. "
                           "The server cannot download ISE or obtain a licence for you: you provide "
                           "the installed ISE tree and your Xilinx.lic.",
                "automatic": False}

    def install(self, plan=None, progress=lambda text: None) -> str:
        raise BackendError(
            "The ISE add-on is set up by hand for now: create the VM and unpack your own slim ISE "
            "tree and licence as described in the README, section 'Spartan-3A add-on'.")

    def control(self, action: str) -> dict:
        if action == "start":
            _ensure_running()
        elif action == "stop":
            if _vm_state() == "Running":
                busy = _sh("pgrep -f 'fpga-jobs/.*/run.sh' >/dev/null && echo busy || echo idle")
                if "busy" in busy:
                    raise BackendError("A build is still running in the VM; not stopping it.")
                _run(["limactl", "stop", VM], timeout=180)
        elif action != "status":
            raise BackendError("action must be start, stop or status")
        return {"vm": VM, "state": _vm_state()}

    # ---- build ------------------------------------------------------------
    def build_start(self, project_dir: Path, top: str, part: str,
                    sources: list[str] | None, constraints: str | None) -> dict:
        project_dir = project_dir.expanduser().resolve()
        if not project_dir.is_dir():
            raise BackendError(f"Project folder not found: {project_dir}")
        top = _safe_name(top, "top module name")
        dev, pkg, spd = parse_part(part)
        own_xst = (project_dir / f"{top}.xst").is_file()

        if sources:
            srcs = [_safe_name(s, "source path") for s in sources]
        else:
            srcs = sorted(p.name for p in project_dir.iterdir()
                          if p.suffix.lower() in SOURCE_EXT
                          and not re.search(r"(^tb_|_tb\.|^test)", p.name, re.I))
        if not own_xst:
            if not srcs:
                raise BackendError("No HDL sources found; pass 'sources' explicitly.")
            missing = [s for s in srcs if not (project_dir / s).is_file()]
            if missing:
                raise BackendError(f"Source files not found: {missing}")

        if constraints:
            ucf = _safe_name(constraints, "constraints path")
            if not (project_dir / ucf).is_file():
                raise BackendError(f"Constraints file not found: {ucf}")
        else:
            ucfs = sorted(p.name for p in project_dir.glob("*.ucf"))
            if len(ucfs) > 1:
                raise BackendError(f"Several .ucf files found {ucfs}; pass 'constraints' to choose one.")
            ucf = ucfs[0] if ucfs else ""

        _ensure_running()
        job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        run_sh = RUN_SH % {
            "ise": ISE_ROOT, "top": top, "part": f"{dev}-{pkg}-{spd}", "speed": spd,
            "sd": "-sd ipcore_dir" if (project_dir / "ipcore_dir").is_dir() else "",
            "uc": f"-uc {ucf}" if ucf else "", "trce_ucf": f"-ucf {ucf}" if ucf else "",
            "ut": f"-f {top}.ut" if (project_dir / f"{top}.ut").is_file() else "-w",
        }
        extra = {"run.sh": run_sh}
        if not own_xst:
            extra[f"{top}.prj"] = "".join(
                f'{SOURCE_EXT[Path(s).suffix.lower()]} work "{s}"\n' for s in srcs)
            extra[f"{top}.xst"] = XST_TEMPLATE.format(top=top, xst_part=f"{dev}-{spd}-{pkg}")

        sent = self._upload(project_dir, job_id, top, extra)
        _sh(f"cd ~/fpga-jobs/{job_id} && chmod +x run.sh && (setsid nohup ./run.sh > run.out 2>&1 &) ; sleep 1")

        JOBS_LOCAL.mkdir(parents=True, exist_ok=True)
        (JOBS_LOCAL / f"{job_id}.json").write_text(json.dumps({
            "job_id": job_id, "backend": self.name, "project_dir": str(project_dir),
            "top": top, "part": f"{dev}-{pkg}-{spd}", "started": time.time(), "fetched": False}))
        return {"job_id": job_id, "state": "running", "files_sent": sent,
                "flow_files": "project's own .xst" if own_xst else "generated .prj/.xst",
                "constraints": ucf or None,
                "note": "ISE runs under full x86 emulation: a small design takes a few minutes, "
                        "a large one 20-30. Poll with build_status."}

    def _upload(self, project_dir: Path, job_id: str, top: str, extra: dict[str, str]) -> int:
        proc = subprocess.Popen(
            ["limactl", "shell", "--workdir", "/tmp", VM, "bash", "-c",
             f"mkdir -p ~/fpga-jobs/{job_id} && tar xzf - -C ~/fpga-jobs/{job_id}"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env())
        count = 0
        with tarfile.open(fileobj=proc.stdin, mode="w|gz") as tar:
            for root, dirs, files in os.walk(project_dir):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in files:
                    p = Path(root) / f
                    rel = p.relative_to(project_dir).as_posix()
                    if p.suffix.lower() in SKIP_EXT or rel == f"{top}.ngc" or rel in extra or p.is_symlink():
                        continue
                    tar.add(p, arcname=rel, recursive=False)
                    count += 1
            for name, text in extra.items():
                data = text.encode()
                ti = tarfile.TarInfo(name)
                ti.size, ti.mode, ti.mtime = len(data), 0o755 if name.endswith(".sh") else 0o644, int(time.time())
                tar.addfile(ti, io.BytesIO(data))
        proc.stdin.close()
        err = proc.stderr.read().decode()
        if proc.wait(timeout=300) != 0:
            raise BackendError(f"Copying the project into the VM failed: {err[-300:]}")
        return count

    def build_status(self, job_id: str) -> dict:
        meta_path = JOBS_LOCAL / f"{_safe_name(job_id, 'job id')}.json"
        if not meta_path.is_file():
            raise BackendError(f"Unknown job '{job_id}'.")
        meta = json.loads(meta_path.read_text())
        out_dir = Path(meta["project_dir"]) / "build_out" / job_id
        if meta.get("fetched"):
            report = self.report(out_dir)
            return {"job_id": job_id, "state": meta["state"], "steps": meta["steps"],
                    "step_order": list(STEPS), "outputs_dir": str(out_dir), "report": report,
                    "diagnosis": diagnose.analyse(self.name, out_dir, report, meta["state"])}
        if _vm_state() != "Running":
            raise BackendError(f"VM '{VM}' is not running, so job '{job_id}' cannot be inspected.")

        raw = _sh(f"cd ~/fpga-jobs/{job_id} 2>/dev/null || {{ echo NOJOB; exit 0; }}\n"
                  "cat job.status 2>/dev/null\necho ---TAIL---\n"
                  "f=$(ls -t *.log 2>/dev/null | head -1); [ -n \"$f\" ] && tail -n 6 \"$f\" | cut -c1-200\n")
        if raw.startswith("NOJOB"):
            raise BackendError(f"Job folder for '{job_id}' is missing in the VM.")
        status_txt, _, tail = raw.partition("---TAIL---")
        steps, current, done_rc = [], None, None
        for line in status_txt.splitlines():
            parts = line.split()
            if parts[:1] == ["STEP"] and parts[2] == "START":
                current = {"step": parts[1], "started": int(parts[3])}
            elif parts[:1] == ["STEP"] and parts[2] == "END":
                steps.append({"step": parts[1], "exit_code": int(parts[3]), "seconds": int(parts[4])})
                current = None
            elif parts[:1] == ["DONE"]:
                done_rc = int(parts[1])

        result: dict = {"job_id": job_id, "steps": steps, "step_order": list(STEPS),
                        "elapsed_seconds": int(time.time() - meta["started"])}
        if done_rc is None:
            result["state"] = "running"
            if current:
                result["current_step"] = current["step"]
                result["current_step_seconds"] = int(time.time()) - current["started"]
            result["log_tail"] = tail.strip().splitlines()[-4:]
            result["remaining_steps"] = [s for s in STEPS if s not in {x["step"] for x in steps}
                                         and s != (current or {}).get("step")]
            return result

        self._download(job_id, meta["top"], out_dir)
        state = "done" if done_rc == 0 else "failed"
        meta.update(fetched=True, state=state, steps=steps)
        meta_path.write_text(json.dumps(meta))
        report = self.report(out_dir)
        result.update(state=state, outputs_dir=str(out_dir), report=report,
                      diagnosis=diagnose.analyse(self.name, out_dir, report, state))
        if done_rc != 0:
            result["error_tail"] = tail.strip().splitlines()[-6:]
        return result

    def _download(self, job_id: str, top: str, out_dir: Path) -> None:
        globs = " ".join(g.format(top=top) for g in OUTPUT_GLOBS)
        p = _run(["limactl", "shell", "--workdir", "/tmp", VM, "bash", "-c",
                  f"cd ~/fpga-jobs/{job_id} && tar czf - $(ls {globs} 2>/dev/null)"], timeout=300)
        if p.returncode != 0:
            raise BackendError(f"Copying results out of the VM failed: {p.stderr.decode()[-300:]}")
        out_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(p.stdout), mode="r:gz") as tar:
            for m in tar.getmembers():
                if m.isfile() and "/" not in m.name and not m.name.startswith("."):
                    (out_dir / m.name).write_bytes(tar.extractfile(m).read())

    # ---- report -----------------------------------------------------------
    def report(self, out_dir: Path) -> dict:
        out_dir = out_dir.expanduser()
        if not out_dir.is_dir():
            raise BackendError(f"Folder not found: {out_dir}")

        def read(pattern: str) -> str:
            hits = sorted(out_dir.glob(pattern))
            return hits[0].read_text(errors="replace") if hits else ""

        rep: dict = {}
        syr = read("*.syr")
        if syr:
            m = re.search(r"Number of errors\s*:\s*(\d+).*?Number of warnings\s*:\s*(\d+)", syr, re.S)
            if m:
                rep["synthesis"] = {"errors": int(m.group(1)), "warnings": int(m.group(2))}

        mrp = read("*_map.mrp")
        if mrp:
            util = {}
            for m in re.finditer(r"^\s*((?:Total )?Number of [\w ]+?):\s+([\d,]+) out of\s+([\d,]+)\s+(\d+)%",
                                 mrp, re.M):
                util[m.group(1).replace("Number of ", "").strip()] = {
                    "used": int(m.group(2).replace(",", "")),
                    "available": int(m.group(3).replace(",", "")), "percent": int(m.group(4))}
            rep["utilization"] = util

        par = read("*.par") or read("par.log")
        if par:
            timing: dict = {"all_routed": "All signals are completely routed" in par}
            m = re.search(r"Timing Score:\s*(\d+)", par)
            if m:
                timing["score"] = int(m.group(1))
            m = re.search(r"(\d+) constraints? not met", par)
            timing["constraints_not_met"] = int(m.group(1)) if m else 0
            timing["met"] = bool(re.search(r"All constraints were met", par)) or (
                timing["constraints_not_met"] == 0 and timing.get("score", 0) == 0)
            rows, name = [], ""
            for line in par.splitlines():
                m = re.match(r"^(\*?)\s*(.*?)\s*\|\s*(SETUP|HOLD|MINLOWPULSE|MINHIGHPULSE|MINPERIOD|MAXDELAY)"
                             r"\s*\|\s*(-?[\d.]+)ns\|\s*(?:([\d.]+)ns)?\s*\|\s*(\d+)\|\s*(\d+)", line)
                if not m:
                    continue
                first = re.match(r"(\w+)\s*=", m.group(2))
                if first:
                    name = first.group(1)
                rows.append({"constraint": name, "check": m.group(3), "slack_ns": float(m.group(4)),
                             "achievable_ns": float(m.group(5)) if m.group(5) else None,
                             "errors": int(m.group(6))})
            timing["failing"] = [r for r in rows if r["slack_ns"] < 0]
            timing["worst_slack_ns"] = min((r["slack_ns"] for r in rows), default=None)
            rep["timing"] = timing

        bits = sorted(out_dir.glob("*.bit"))
        rep["bitstream"] = {"path": str(bits[0]), "bytes": bits[0].stat().st_size} if bits else None
        if not (syr or mrp or par or bits):
            raise BackendError(f"No ISE reports found in {out_dir}")
        return rep
