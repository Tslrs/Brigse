"""MCP server: one set of tools for every FPGA family, add-ons behind it."""
from __future__ import annotations

import json
import os
import sys
import time
import webbrowser
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import actions, boards, core, dashboard, hierarchy, installer, registry, store
from .backends.base import BackendError

PORT = int(os.environ.get("BRIGSE_PORT", dashboard.DEFAULT_PORT))

mcp = FastMCP("brigse", instructions=(
    "Build, inspect, simulate and program FPGA designs. Call list_backends and list_boards first. Run lint on the sources before build. "
    "build starts a background job and returns a job_id. Some builds are long (Xilinx ISE: about "
    "25-30 minutes, plus about 90 seconds to start the VM; Gowin: seconds). Keep calling "
    "build_status with wait_seconds=50 until state is 'done' or 'failed'; every answer has a "
    "'next' field that says what to do. Do not start the same build again while one is running, "
    "and tell the user once that a long build is running and where the dashboard is. When it is "
    "done, read the report and the diagnosis, and only then say whether it worked. program never writes to hardware by itself: with confirm=true it asks the user, who approves in the dashboard or with `brigse approvals`; call program again with the same arguments and wait_seconds=50 until it runs, and if the user rejects, stop. Installing an add-on needs "
    "the user's approval: call request_install, tell the user to open the dashboard, then poll "
    "install_status. Only the user can approve, in the dashboard. If a request is rejected, stop: "
    "do not retry it or work around it."))


def _guard(fn):
    """Turn expected problems into a readable error payload instead of a stack trace."""
    try:
        return fn()
    except BackendError as e:
        return {"error": str(e)}


def _board_view(b: dict) -> dict:
    usable = [n for n in b["backends"] if registry.by_name(n).implemented and registry.by_name(n).usable()]
    return {**b, "usable_backends": usable}


# ---- discovery ------------------------------------------------------------
@mcp.tool()
def list_backends() -> dict:
    """List the installed and planned FPGA add-ons, the part families each covers, and its state."""
    return {"backends": [
        {"name": b.name, "title": b.title, "implemented": b.implemented,
         "part_patterns": list(b.part_patterns), "disk_cost": b.size_hint, **b.status()}
        for b in registry.BACKENDS]}


@mcp.tool()
def list_boards() -> dict:
    """List known boards (part, add-on, programmer support) and the one currently selected."""
    return {"selected": store.selected_board(), "boards": [_board_view(b) for b in boards.BOARDS]}


@mcp.tool()
def select_board(board_id: str) -> dict:
    """Select the board to work with. build then uses its part when no part is given.

    The user can also pick the board in the dashboard; check list_boards for the current choice.
    """
    b = boards.get(board_id)
    if not b:
        return {"error": f"Unknown board '{board_id}'. Known: {[x['id'] for x in boards.BOARDS]}"}
    store.select_board(board_id, by="agent")
    return {"selected": _board_view(b)}


@mcp.tool()
def dashboard_info() -> dict:
    """Return the address of the local status window and whether it is running."""
    return {"url": dashboard.url(PORT), "running": dashboard.port_in_use(PORT),
            "shows": "add-ons, boards, build progress and reports, and requests awaiting approval"}


# ---- add-on installation (needs user approval) ------------------------------
@mcp.tool()
def request_install(backend: str, reason: str = "") -> dict:
    """Ask the user to approve installing an add-on. Nothing is downloaded by this call.

    The request appears in the dashboard with what would be installed, from where, the disk
    cost and the licence situation. Only the user can approve or reject it there.
    reason: one sentence for the user on why you need it.
    """
    def run():
        b = registry.by_name(backend)
        if b.status()["installed"]:
            return {"state": "already_installed", "backend": backend}
        req = store.new_request("install_backend", backend, f"Install add-on: {b.title}",
                                b.install_plan(), reason)
        return {"request_id": req["id"], "state": req["state"], "plan": req["plan"],
                "dashboard": dashboard.url(PORT),
                "next": "Ask the user to approve or reject in the dashboard, then call install_status."}
    return _guard(run)


@mcp.tool()
def request_uninstall(backend: str, reason: str = "") -> dict:
    """Ask the user to approve removing an add-on that Brigse installed. Nothing is deleted by this call.

    Only add-ons installed by Brigse can be removed (the downloaded open-source tools); vendor tools
    the user installed are never touched. Poll install_status with the returned request_id.
    """
    def run():
        req = actions.uninstall_request(backend, reason)
        return {"request_id": req["id"], "state": req["state"], "plan": req["plan"],
                "dashboard": dashboard.url(PORT),
                "next": "Ask the user to approve or reject in the dashboard, then call install_status."}
    return _guard(run)


@mcp.tool()
def install_status(request_id: str, wait_seconds: int = 0) -> dict:
    """Check an install or uninstall request. If the user approved it, start the work (when automatic).

    wait_seconds: wait up to this long (max 50) for the user's decision.
    """
    def run():
        deadline = time.time() + max(0, min(wait_seconds, 50))
        while True:
            req = store.get_request(request_id)
            if not req:
                raise BackendError(f"Unknown request '{request_id}'.")
            if req["state"] != "pending" or time.time() >= deadline:
                break
            time.sleep(3)
        out = {"request_id": request_id, "state": req["state"], "backend": req["subject"]}
        if req["state"] == "pending":
            out["note"] = f"Still waiting for the user at {dashboard.url(PORT)}"
        elif req["state"] == "rejected":
            out["note"] = "The user rejected this. Do not retry or work around it; ask them how to proceed."
        elif req["state"] == "approved":
            b = registry.by_name(req["subject"])
            if not req["plan"].get("automatic"):
                out["note"] = ("Approved, but this add-on has no automatic installer, so nothing "
                               "was installed. " + b.status().get("install_hint", ""))
            elif installer.start(request_id):
                out.update(state=store.get_request(request_id)["state"],
                           note="Started in the background; poll again.")
        elif req["state"] in ("installing", "removing"):
            out["progress"] = req.get("progress")
        else:
            out["result"] = req.get("result")
        return out
    return _guard(run)


@mcp.tool()
def activity() -> dict:
    """What is in progress right now (builds, installs, programmer use) and who started it.

    The user can also start actions from the dashboard, so check this before starting work
    that needs the same add-on or the programmer.
    """
    return {"busy": actions.busy(), "last_program": store.get_state().get("last_program")}


@mcp.tool()
def backend_control(backend: str, action: str) -> dict:
    """Start, stop or check an add-on's runtime (for ise_spartan3: its VM).

    action: 'start', 'stop' or 'status'. Stopping frees RAM; it is refused while a build runs.
    """
    return _guard(lambda: registry.by_name(backend).control(action))


# ---- build ----------------------------------------------------------------
TYPICAL = {"ise_spartan3": "about 25-30 minutes (the VM needs about 90 s to start first)",
           "gowin_eda": "a few seconds", "gowin_oss": "a few seconds"}


def _next_step(st: dict, backend: str) -> str:
    """Tell the agent in plain words what to do after this answer."""
    state = st.get("state")
    if state == "running":
        step = st.get("current_step") or "starting"
        return (f"Still running (step: {step}). A build on this add-on takes {TYPICAL.get(backend, 'a while')}. "
                "Call build_status again with wait_seconds=50. Do not start another build.")
    if state == "done":
        return ("Finished. Read 'report' (timing, utilization) and 'diagnosis' (problems in plain words) before "
                "you tell the user it worked. Timing not met or listed problems mean it did not fully work. "
                "To put it on a board use program with confirm=false first (it shows the command), then confirm=true: the user approves it.")
    if state == "failed":
        return "Failed. Read 'diagnosis' for the cause and the fix, correct the sources, then build again."
    return "State is unclear. Call build_status again; if it stays unclear, tell the user and point to the dashboard."


@mcp.tool()
def build(project_dir: str, top: str, part: str | None = None,
          sources: list[str] | None = None, constraints: str | None = None,
          backend: str | None = None) -> dict:
    """Start a full build (synthesis to bitstream) as a background job.

    project_dir: folder with the HDL sources (copied to the build machine; old outputs are skipped).
    top: top module name. If <top>.xst exists in the folder, the project's own synthesis script is used.
    part: device-package-speed, e.g. xc3s700a-fg484-4. Defaults to the selected board's part.
          The part selects the add-on.
    sources: paths relative to project_dir; default is every .v/.vhd in the folder except testbenches.
    constraints: constraints file relative to project_dir; default is the only .ucf (ISE) or
          .cst (Gowin) in the folder.
    backend: force a specific add-on. By default the usable add-on with the highest priority is
          used (the vendor tool when the user has it, otherwise the open-source flow).
    """
    def run():
        r = actions.start_build(project_dir, top, part, sources, constraints, backend, by="agent")
        be = r.get("backend") or ""
        return {**r, "typical_duration": TYPICAL.get(be, "unknown"),
                "next": "Build started. Call build_status with this job_id and wait_seconds=50, and repeat until it is done or failed."}
    return _guard(run)


@mcp.tool()
def build_status(job_id: str, wait_seconds: int = 0) -> dict:
    """Report a build job's progress; when it has finished, fetch the outputs and return the report.

    wait_seconds: wait up to this long (max 50) for the job to finish before answering.
    The answer has a 'next' field: what to do now (ask again, or how to check the result).
    """
    def run():
        meta_path = store.JOBS / f"{job_id}.json"
        if not meta_path.is_file():
            raise BackendError(f"Unknown job '{job_id}'.")
        backend = registry.by_name(json.loads(meta_path.read_text())["backend"])
        deadline = time.time() + max(0, min(wait_seconds, 50))
        while True:
            st = backend.build_status(job_id)
            if st["state"] != "running" or time.time() >= deadline:
                return {**st, "next": _next_step(st, backend.name)}
            time.sleep(5)
    return _guard(run)


@mcp.tool()
def diagnose(outputs_dir: str, backend: str | None = None) -> dict:
    """Explain why a build failed, or what needs attention in one that finished.

    Reads the logs in a build output folder and returns the problems in plain words, each with
    where it is, why it happens and how to fix it. Messages Brigse has no explanation for are
    returned as 'unexplained', in the tool's own words. Also catches mistakes the tools let through:
    a mistyped signal name that still builds, a constraint that matches no port, a port with no pin.
    build_status already includes this for finished builds; use this tool on an older folder.

    backend: the add-on that produced the folder; detected from the files when omitted.
    """
    def run():
        folder = Path(outputs_dir).expanduser()
        names = {"ise_spartan3": "xst.log", "gowin_oss": "yosys.log", "gowin_eda": "gowin_eda.log"}
        chosen = backend or next((b for b, f in names.items() if (folder / f).is_file()), None)
        if not chosen:
            raise BackendError(f"No known build logs found in {folder}")
        b = registry.by_name(chosen)
        try:
            rep = b.report(folder)
        except BackendError:
            rep = {}
        failed = not (rep.get("bitstream"))
        from . import diagnose as engine
        d = engine.analyse(chosen, folder, rep, "failed" if failed else "done")
        return {"backend": chosen, "build_failed": failed,
                "diagnosis": d or {"verdict": "Nothing to report: no problems found in the logs.", "problems": []}}
    return _guard(run)


@mcp.tool()
def chip_view(bitstream: str, family: str = "GW1N-9C", label: str | None = None, out_dir: str | None = None) -> dict:
    """Draw where a Gowin design sits on the chip, from its bitstream (.fs), as SVG and PNG.

    Works for builds made with Gowin EDA as well as the open-source flow, because it reads the
    bitstream itself. The PNG (needs Pillow) is sized for a slide. The file is only read.
    Limits, also written on the picture: multipliers cannot be read, input pins are not shown,
    and only the GW1N-9C chip (Tang Nano 9K) has been checked. Needs the gowin_oss add-on.

    out_dir: where to write; defaults to a new folder under the data folder (chipviews).
    """
    def run():
        from . import floorplan
        target = Path(out_dir) if out_dir else store.HOME / "chipviews" / time.strftime("%Y%m%d-%H%M%S")
        return floorplan.from_bitstream(Path(bitstream), target, family, label)
    return _guard(run)


@mcp.tool()
def report(outputs_dir: str, backend: str | None = None) -> dict:
    """Summarise the reports in a build output folder: utilisation, timing, failing constraints, bitstream.

    backend: the add-on that produced the folder; detected automatically when omitted.
    """
    def run():
        if backend:
            return registry.by_name(backend).report(Path(outputs_dir))
        for b in registry.BACKENDS:
            if b.implemented:
                try:
                    return {"backend": b.name, **b.report(Path(outputs_dir))}
                except BackendError:
                    continue
        raise BackendError(f"No known build reports found in {outputs_dir}")
    return _guard(run)


# ---- simulate / program ---------------------------------------------------
@mcp.tool()
def simulate(sources: list[str], top: str | None = None, cwd: str | None = None,
             timeout_s: int = 60, waves: bool = True) -> dict:
    """Compile and run a Verilog testbench with Icarus Verilog.

    sources: files relative to cwd, testbench included. top: testbench module (guessed when exactly
    one module is named like a testbench). The run passes when the simulator exits 0 and prints no
    FAIL/ERROR text. With waves=true every signal is recorded, with no change to the testbench: the
    user sees it as a diagram in the dashboard, and you read it with the waves tool.
    """
    return _guard(lambda: actions.simulate(sources, top, cwd, timeout_s, waves, by="agent"))


@mcp.tool()
def utilization_by_module(cwd: str, top: str, sources: list[str] | None = None, family: str = "gw1n") -> dict:
    """Which module of a Verilog design uses how much logic, flip-flops, block RAM and multipliers.

    The build's own reports cannot tell: the tools flatten the design. This runs a quick separate
    synthesis (yosys, a few seconds) that keeps the hierarchy and returns one row per module: what it
    uses itself ('own'), with everything inside it ('with_submodules'), all its instances together
    ('in_design'), and its share of the design's logic. Rows are sorted by share, biggest first, so the
    first one is where to look when a design is too big. Parameterised modules are named 'counter (W=16)'.
    It is an estimate for the Gowin architecture (also a fair guide for 4-input-LUT parts such as
    Spartan-3); the real build differs by a few percent. Changes nothing. Needs the gowin_oss add-on.
    cwd: project folder. top: top module. sources: default every .v/.sv except testbenches.
    family: gw1n, gw2a or gw5a (decides how memories and multipliers are mapped).
    """
    return _guard(lambda: hierarchy.analyse(sources or [], top, cwd, family))


@mcp.tool()
def lint(cwd: str, sources: list[str] | None = None, top: str | None = None, strict: bool = False) -> dict:
    """Check Verilog/SystemVerilog in about a second. Run this before build: a build takes minutes.

    Finds syntax errors, undeclared names, width mismatches and unintended latches, each with file,
    line, a code and the source line. Uses Verilator when installed, otherwise a weaker syntax-only
    check (the answer says which). Reads only; writes nothing.
    cwd: the project folder. sources: files relative to it (default: every .v/.sv/.vh in it).
    top: top module, to ignore modules nothing uses. strict: also report style warnings (unused signals).
    Fix every error, and read the warnings, before you start the build.
    """
    return _guard(lambda: core.lint(sources, top, cwd, strict))


@mcp.tool()
def cocotb_test(cwd: str, sources: list[str], toplevel: str, test_module: str, testcase: str | None = None,
                parameters: dict | None = None, timeout_s: int = 120) -> dict:
    """Test Verilog with a Python testbench (cocotb) and Icarus Verilog.

    Write the test as Python: an `async def` decorated with @cocotb.test(), which drives the design's
    signals (dut.clk, dut.rst, dut.led ...) with `await RisingEdge(dut.clk)` and checks them with assert.
    cwd: project folder. sources: Verilog files relative to it (the design, no testbench needed).
    toplevel: the design module. test_module: the Python file name without .py, in cwd.
    testcase: run only this test function. parameters: override module parameters, e.g. {"WIDTH": 6}
    to make a slow counter finish in a few cycles. Answers with one row per test (passed, failed, with
    the assertion message). Needs cocotb, which the user installs with `brigse install-cocotb`.
    """
    return _guard(lambda: core.cocotb(sources, toplevel, test_module, cwd, testcase, parameters, timeout_s))


@mcp.tool()
def waves(sim_id: str, signals: list[str] | None = None, start: int = 0, end: int | None = None,
          max_rows: int = 40, sample_on: str | None = None) -> dict:
    """Read the recorded signals of a simulation as text: what changed, and when.

    sim_id: from simulate. signals: names to include (a name matches the end of the full path, e.g.
    'led' or 'dut.count'); all signals when omitted, up to 24. start/end: time range in the
    simulation's own unit (see timescale). Each row lists only the signals that changed at that time.
    sample_on: name of the clock; gives one row per rising edge with the value of every signal just
    after it, which is the natural way to read a synchronous design.
    Use it to find where a failing testbench diverges from what you expect.
    """
    return _guard(lambda: actions.waves(sim_id, signals, start, end, max_rows, sample_on))


@mcp.tool()
def detect_boards(cable: str | None = None) -> dict:
    """List connected USB programming probes and, for a given cable, the FPGAs on its JTAG chain."""
    return _guard(lambda: core.detect_boards(cable))


@mcp.tool()
def program(bitstream: str, cable: str | None = None, board: str | None = None,
            part: str | None = None, write_flash: bool = False, confirm: bool = False,
            wait_seconds: int = 0) -> dict:
    """Load a bitstream onto a board with openFPGALoader. The user must approve every load.

    Prefer the temporary SRAM load (write_flash=false): it leaves the design stored on the board
    untouched. Use write_flash=true only when the user explicitly asks for a permanent write.

    Without confirm=true this is a dry run that only shows the command. With confirm=true nothing is
    written yet: a request appears in the dashboard (and in `brigse approvals`) and only the user can
    approve it. Tell the user, then call program again with the same arguments and wait_seconds=50
    (max) until it loads. An approval is for that exact file, target and memory, and for one load.
    If the user rejects it, stop and ask them. Pass part to check first that the loader supports the device.
    """
    def run():
        out = actions.program(bitstream, cable, board, part, write_flash, confirm, by="agent", wait_seconds=wait_seconds)
        if out.get("state") == "pending_approval":
            out["where"] = dashboard.url(PORT)
        return out
    return _guard(run)


def main() -> None:
    if sys.argv[1:2] in (["status"], ["approvals"]):
        from . import cli
        raise SystemExit(cli.run(sys.argv[1], sys.argv[2:]))
    if sys.argv[1:2] in (["install-cocotb"], ["remove-cocotb"]):
        from . import cocotb_env
        raise SystemExit(cocotb_env.remove() if sys.argv[1] == "remove-cocotb" else cocotb_env.install())
    if sys.argv[1:2] == ["install-agent"]:
        from . import connect
        raise SystemExit(connect.run(sys.argv[2:]))
    if sys.argv[1:2] == ["dashboard"]:
        if dashboard.port_in_use(PORT):
            print(f"Dashboard already running at {dashboard.url(PORT)}")
        else:
            print(f"Dashboard at {dashboard.url(PORT)}  (Ctrl-C to stop)")
            if "--no-open" not in sys.argv:
                webbrowser.open(dashboard.url(PORT))
            try:
                dashboard.serve(PORT)
            except KeyboardInterrupt:
                pass
        return
    if os.environ.get("BRIGSE_DASHBOARD", "1") != "0":
        dashboard.start_in_background(PORT)
    mcp.run()


if __name__ == "__main__":
    main()
