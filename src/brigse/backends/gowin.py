"""Two add-ons for Gowin FPGAs.

``gowin_oss``  open-source flow (yosys + nextpnr-himbaechel + apicula). Light, no
               licence, installed automatically after the user approves. It does not
               support vendor IP cores and covers fewer device features.
``gowin_eda``  the vendor's Gowin EDA, used only if the user already installed it and
               its licence is valid. Full device support. Never downloaded by this server.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .. import diagnose, floorplan, oss_suite
from . import localjob
from .base import Backend, BackendError

# part number -> (apicula family, Gowin EDA device name)
PARTS = {
    "gw1nr-lv9qn88pc6/i5": ("GW1N-9C", "GW1NR-9C"),
    "gw2ar-lv18qn88c8/i7": ("GW2A-18C", "GW2AR-18C"),
    "gw2a-lv18pg256c8/i7": ("GW2A-18", "GW2A-18"),
    "gw1n-lv1qn48c6/i5": ("GW1N-1", "GW1N-1"),
    "gw1nz-lv1qn48c6/i5": ("GW1NZ-1", "GW1NZ-1"),
    "gw1nsr-lv4cqn48pc7/i6": ("GW1NS-4", "GW1NSR-4C"),
    "gw5a-lv25mg121nc1/i0": ("GW5A-25A", "GW5A-25A"),
}
OSS_TOOLS = ("yosys", "nextpnr-himbaechel", "gowin_pack")
VERILOG_EXT = {".v", ".sv"}


def _lookup(part: str) -> tuple[str, str, str]:
    key = part.strip().lower()
    if key not in PARTS:
        raise BackendError(f"Unknown Gowin part '{part}'. Known parts: {sorted(PARTS)}")
    return key.upper(), *PARTS[key]


def _inputs(project_dir: Path, sources: list[str] | None, constraints: str | None,
            exts: set[str]) -> tuple[Path, list[str], str]:
    project_dir = project_dir.expanduser().resolve()
    if not project_dir.is_dir():
        raise BackendError(f"Project folder not found: {project_dir}")
    srcs = sources or sorted(p.name for p in project_dir.iterdir()
                             if p.suffix.lower() in exts
                             and not re.search(r"(^tb_|_tb\.|^test)", p.name, re.I))
    missing = [s for s in srcs if not (project_dir / s).is_file()]
    if not srcs or missing:
        raise BackendError(f"HDL sources not found in {project_dir}: {missing or 'none'}")
    if constraints:
        cst = constraints
        if not (project_dir / cst).is_file():
            raise BackendError(f"Constraints file not found: {cst}")
    else:
        found = sorted(p.name for p in project_dir.glob("*.cst"))
        if len(found) != 1:
            raise BackendError(f"Expected exactly one .cst pin file in the project, found {found}; "
                               "pass 'constraints' to choose.")
        cst = found[0]
    return project_dir, srcs, cst


class GowinOssBackend(Backend):
    name = "gowin_oss"
    auto_install = True
    title = "Gowin, open-source flow (yosys + nextpnr + apicula)"
    part_patterns = (r"gw[125]",)
    size_hint = "about 0.5 GB download, about 2 GB installed (shared by all open-source add-ons)"
    priority = 10

    def status(self) -> dict:
        ok = oss_suite.installed(OSS_TOOLS)
        return {"installed": ok, "ready": ok, "usable": ok,
                "detail": f"OSS CAD Suite at {oss_suite.SUITE}" if ok else "OSS CAD Suite is not installed.",
                "install_hint": "" if ok else "Call request_install('gowin_oss'); the user approves in the dashboard.",
                "limits": "No vendor IP cores; Verilog only; fewer device features than Gowin EDA."}

    def install_plan(self) -> dict:
        asset = oss_suite.latest_asset()
        return {"what": f"OSS CAD Suite {asset['release']} (yosys, nextpnr, apicula and related tools)",
                "source": asset["url"],
                "disk": f"{asset['bytes'] // 1_000_000} MB download, about 2 GB unpacked in {oss_suite.TOOLS}",
                "licence": "Open-source tools (ISC/GPL/MIT); no licence file or vendor account needed. "
                           "The download is checked against the SHA-256 published by GitHub.",
                "automatic": True, "asset": asset}

    def install(self, plan=None, progress=lambda text: None) -> str:
        if not plan or "asset" not in plan:
            raise BackendError("No approved download in the request.")
        return oss_suite.install(plan["asset"], OSS_TOOLS, progress)

    def uninstall_plan(self) -> dict:
        if not oss_suite.SUITE.exists():
            raise BackendError("The open-source tools are not installed.")
        return {"what": "Delete the OSS CAD Suite (yosys, nextpnr, apicula and related tools)",
                "source": str(oss_suite.SUITE),
                "disk": f"frees about {oss_suite.disk_bytes() / 1e9:.1f} GB",
                "licence": "Only this folder is deleted. Your projects, builds and Gowin EDA are not touched. "
                           "It can be installed again later (about 0.5 GB download).",
                "automatic": True}

    def uninstall(self, plan=None, progress=lambda text: None) -> str:
        return oss_suite.uninstall(progress)

    def build_start(self, project_dir, top, part, sources, constraints) -> dict:
        if not oss_suite.installed(OSS_TOOLS):
            raise BackendError("The open-source Gowin add-on is not installed. Call request_install('gowin_oss').")
        device, family, _ = _lookup(part)
        project_dir, srcs, cst = _inputs(project_dir, sources, constraints, VERILOG_EXT)
        job_dir = project_dir / "build_out" / localjob.new_job_id()
        read = "; ".join(f"read_verilog -sv {localjob.q(s)}" for s in srcs)
        steps = [
            ("yosys", ["yosys", "-p", f"{read}; synth_gowin -top {top} -json {localjob.q(job_dir / (top + '.json'))}"]),
            ("nextpnr", ["nextpnr-himbaechel", "--json", str(job_dir / f"{top}.json"),
                         "--write", str(job_dir / f"{top}_pnr.json"), "--device", device,
                         "--vopt", f"family={family}", "--vopt", f"cst={cst}"]),
            ("pack", ["gowin_pack", "-d", family, "-o", str(job_dir / f"{top}.fs"),
                      str(job_dir / f"{top}_pnr.json")]),
        ]
        job_id = localjob.start(self.name, project_dir, job_dir, top, device, steps, oss_suite.env())
        return {"job_id": job_id, "state": "running", "sources": srcs, "constraints": cst,
                "note": "Runs locally; small designs finish in seconds. Poll with build_status."}

    def build_status(self, job_id: str) -> dict:
        meta, st = localjob.status(job_id)
        if st["state"] != "running":
            st["report"] = self._safe_report(Path(meta["job_dir"]))
            st["diagnosis"] = diagnose.analyse(self.name, Path(meta["job_dir"]), st["report"], st["state"])
        return st

    def _safe_report(self, out_dir: Path) -> dict:
        """A failed build may have produced no report at all; that must not hide the diagnosis."""
        try:
            return self.report(out_dir)
        except BackendError:
            return {}

    def report(self, out_dir: Path) -> dict:
        out_dir = Path(out_dir).expanduser()
        rep: dict = {}
        ylog = out_dir / "yosys.log"
        if ylog.is_file():
            text = ylog.read_text(errors="replace")
            rep["synthesis"] = {"errors": len(re.findall(r"^ERROR:", text, re.M)),
                                "warnings": len(re.findall(r"^Warning:", text, re.M))}
        nlog = out_dir / "nextpnr.log"
        if nlog.is_file():
            text = nlog.read_text(errors="replace")
            util = {}
            for m in re.finditer(r"^Info:\s+([\w/]+):\s+(\d+)/\s*(\d+)\s+(\d+)%", text, re.M):
                if int(m.group(3)):
                    util[m.group(1)] = {"used": int(m.group(2)), "available": int(m.group(3)),
                                        "percent": int(m.group(4))}
            rep["utilization"] = util
            clocks = {}
            for m in re.finditer(r"Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz \((PASS|FAIL) at ([\d.]+) MHz\)", text):
                clocks[m.group(1)] = {"clock": m.group(1), "fmax_mhz": float(m.group(2)),
                                      "target_mhz": float(m.group(4)), "pass": m.group(3) == "PASS"}
            failing = [c for c in clocks.values() if not c["pass"]]
            rep["timing"] = {"met": not failing, "clocks": list(clocks.values()), "failing": failing,
                             "all_routed": "Program finished normally" in text or bool(clocks),
                             "note": "Targets are nextpnr defaults unless the design sets clock constraints."}
        bits = sorted(out_dir.glob("*.fs"))
        rep["bitstream"] = {"path": str(bits[0]), "bytes": bits[0].stat().st_size} if bits else None
        view = floorplan.make(out_dir)
        if view:
            rep["floorplan"] = view
        if not rep.get("synthesis") and not bits and "timing" not in rep:
            raise BackendError(f"No open-source Gowin build outputs found in {out_dir}")
        return rep


class GowinEdaBackend(Backend):
    name = "gowin_eda"
    title = "Gowin EDA (vendor tool, your own install)"
    part_patterns = (r"gw[125]",)
    size_hint = "none: uses the Gowin EDA you already installed"
    priority = 20          # preferred over the open-source flow when it is usable
    _lic: tuple[float, bool, str] = (0.0, False, "")

    def _home(self) -> Path | None:
        candidates = [os.environ.get("GOWIN_HOME"),
                      "/Applications/GowinIDE.app/Contents/Resources/Gowin_EDA",
                      "/opt/gowin", str(Path.home() / "gowin")]
        for c in candidates:
            if c and (Path(c) / "IDE" / "bin" / "gw_sh").is_file():
                return Path(c)
        return None

    def _env(self, home: Path) -> dict:
        lib = str(home / "IDE" / "lib")
        return {**os.environ, "DYLD_LIBRARY_PATH": lib, "DYLD_FRAMEWORK_PATH": lib, "LD_LIBRARY_PATH": lib}

    def _licence(self, home: Path) -> tuple[bool, str]:
        """Run a one-line Tcl script; the tool refuses to start it when the licence is invalid."""
        at, ok, msg = self._lic
        if time.time() - at < 120:
            return ok, msg
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "check.tcl"
            script.write_text("puts BRIGSE_OK\n")
            try:
                p = subprocess.run([str(home / "IDE" / "bin" / "gw_sh"), str(script)], cwd=tmp,
                                   capture_output=True, timeout=90, env=self._env(home))
                out = (p.stdout + p.stderr).decode(errors="replace")
            except Exception as e:
                out = f"could not run gw_sh: {e}"
        ok = "BRIGSE_OK" in out
        lines = [l for l in out.strip().splitlines() if l.strip()]
        msg = "" if ok else (lines[-1][:200] if lines else "gw_sh produced no output")
        GowinEdaBackend._lic = (time.time(), ok, msg)
        return ok, msg

    def status(self) -> dict:
        home = self._home()
        if not home:
            return {"installed": False, "ready": False, "usable": False,
                    "detail": "Gowin EDA not found (set GOWIN_HOME if it is installed elsewhere).",
                    "install_hint": "Install Gowin EDA yourself from gowinsemi.com; it needs your own account and licence."}
        ok, msg = self._licence(home)
        return {"installed": True, "ready": ok, "usable": ok,
                "detail": f"Found at {home}." + ("" if ok else f" Not usable: {msg}"),
                "install_hint": "" if ok else "Fix the Gowin licence for this machine in the Gowin IDE "
                                                "(or install the Education edition, which needs none)."}

    def install_plan(self) -> dict:
        return {"what": "Gowin EDA (vendor tool)", "source": "gowinsemi.com, downloaded by you",
                "disk": "about 1-2 GB", "automatic": False,
                "licence": "Needs your own Gowin account and a licence tied to this machine. "
                           "The server cannot download it or obtain a licence for you."}

    def build_start(self, project_dir, top, part, sources, constraints) -> dict:
        home = self._home()
        if not home:
            raise BackendError("Gowin EDA is not installed.")
        ok, msg = self._licence(home)
        if not ok:
            raise BackendError(f"Gowin EDA cannot run: {msg}")
        device, _, eda_name = _lookup(part)
        project_dir, srcs, cst = _inputs(project_dir, sources, constraints, VERILOG_EXT | {".vhd", ".vhdl"})
        job_dir = project_dir / "build_out" / localjob.new_job_id()
        job_dir.mkdir(parents=True)
        tcl = [f"set_device -name {eda_name} {device}"]
        tcl += [f"add_file {{{project_dir / f}}}" for f in srcs + [cst]]
        tcl += [f"set_option -top_module {top}", f"set_option -output_base_name {top}", "run all"]
        (job_dir / "run.tcl").write_text("\n".join(tcl) + "\n")
        # macOS strips DYLD_* variables when a command passes through /bin/bash, so they are
        # set on the gw_sh command line itself rather than in the job's environment.
        lib = localjob.q(home / "IDE" / "lib")
        steps = [("gowin_eda", ["bash", "-c", f"cd {localjob.q(job_dir)} && DYLD_LIBRARY_PATH={lib} "
                                              f"DYLD_FRAMEWORK_PATH={lib} LD_LIBRARY_PATH={lib} "
                                              f"{localjob.q(home / 'IDE' / 'bin' / 'gw_sh')} run.tcl"])]
        job_id = localjob.start(self.name, project_dir, job_dir, top, device, steps, self._env(home))
        return {"job_id": job_id, "state": "running", "sources": srcs, "constraints": cst,
                "note": "Runs locally with your Gowin EDA; small designs take under a minute. Poll with build_status."}

    def build_status(self, job_id: str) -> dict:
        meta, st = localjob.status(job_id)
        if st["state"] != "running":
            st["report"] = self._safe_report(Path(meta["job_dir"]))
            st["diagnosis"] = diagnose.analyse(self.name, Path(meta["job_dir"]), st["report"], st["state"])
        return st

    def _safe_report(self, out_dir: Path) -> dict:
        """A failed build may have produced no report at all; that must not hide the diagnosis."""
        try:
            return self.report(out_dir)
        except BackendError:
            return {}

    def report(self, out_dir: Path) -> dict:
        out_dir = Path(out_dir).expanduser()
        pnr = out_dir / "impl" / "pnr"
        rep: dict = {}
        log = out_dir / "gowin_eda.log"
        if log.is_file():
            text = log.read_text(errors="replace")
            rep["synthesis"] = {"errors": len(re.findall(r"^ERROR", text, re.M)),
                                "warnings": len(re.findall(r"^WARN", text, re.M))}
        rpt = sorted(pnr.glob("*.rpt.txt"))
        if rpt:
            util = {}
            for m in re.finditer(r"^\s{2}([A-Za-z/ ]+?)\s*\|\s*(\d+)/(\d+)\s*\|\s*<?(\d+)%",
                                 rpt[0].read_text(errors="replace"), re.M):
                util[m.group(1).strip()] = {"used": int(m.group(2)), "available": int(m.group(3)),
                                            "percent": int(m.group(4))}
            rep["utilization"] = util
        tr = sorted(pnr.glob("*.tr"))
        if tr:
            text = tr[0].read_text(errors="replace")
            clocks = [{"clock": m.group(1), "target_mhz": float(m.group(2)), "fmax_mhz": float(m.group(3)),
                       "pass": float(m.group(3)) >= float(m.group(2))}
                      for m in re.finditer(r"^\s*\d+\s+(\S+)\s+([\d.]+)\(MHz\)\s+([\d.]+)\(MHz\)", text, re.M)]
            negative = [{"clock": m.group(1), "check": m.group(2).upper(), "total_negative_slack_ns": float(m.group(3)),
                         "endpoints": int(m.group(4))}
                        for m in re.finditer(r"^\s*(\S+)\s+(setup|hold)\s+(-?[\d.]+)\s+(\d+)\s*$", text, re.M)
                        if int(m.group(4)) > 0]
            failing = [c for c in clocks if not c["pass"]]
            rep["timing"] = {"met": not failing and not negative, "clocks": clocks,
                             "failing": failing, "negative_slack": negative, "all_routed": True}
        bits = sorted(pnr.glob("*.fs"))
        rep["bitstream"] = {"path": str(bits[0]), "bytes": bits[0].stat().st_size} if bits else None
        rep["vendor_reports"] = [str(p) for p in rpt + tr]
        if not bits and not rpt and "synthesis" not in rep:
            raise BackendError(f"No Gowin EDA build outputs found in {out_dir}")
        return rep
