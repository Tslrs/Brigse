"""The MCP protocol end to end, with a fake add-on: no vendor tool and no FPGA board needed.

Starts tests/mock_server.py over stdio and talks to it as an agent would: the tool list, a build that
passes, one that fails, "one build at a time", unknown jobs and parts, and the approval flow.
Runs in a throw-away BRIGSE_HOME.

usage: uv run python tests/test_protocol.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HOME = tempfile.mkdtemp()
PROJECT = Path(tempfile.mkdtemp())
os.environ["BRIGSE_HOME"] = HOME
from brigse import store  # noqa: E402  (decisions are made the way the dashboard makes them: through the state folder)

EXPECTED_TOOLS = {
    "list_backends", "list_boards", "select_board", "dashboard_info", "request_install", "request_uninstall",
    "install_status", "activity", "backend_control", "build", "build_status", "diagnose", "chip_view",
    "report", "simulate", "lint", "cocotb_test", "waves", "detect_boards", "program",
}
failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


async def call(s, name, **args):
    res = await s.call_tool(name, args)
    return json.loads(res.content[0].text)


async def finish(s, job_id, limit=30):
    end = time.time() + limit
    while time.time() < end:
        st = await call(s, "build_status", job_id=job_id, wait_seconds=1)
        if st.get("state") != "running":
            return st
    raise TimeoutError(job_id)


async def main():
    env = {**os.environ, "BRIGSE_HOME": HOME, "BRIGSE_DASHBOARD": "0"}
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name("mock_server.py"))], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        check("every documented tool is offered", names == EXPECTED_TOOLS)

        backends = {b["name"]: b for b in (await call(s, "list_backends"))["backends"]}
        check("the fake add-on is listed, and the planned ones are not usable", backends["mock"]["usable"] is True
              and backends["xilinx7_oss"]["implemented"] is False)

        boards = await call(s, "list_boards")
        check("boards are listed with nothing selected", boards["selected"] is None and len(boards["boards"]) >= 5)
        check("no part and no board is an error", "No part given" in (await call(s, "build", project_dir=str(PROJECT), top="x")).get("error", ""))
        check("an unknown board is an error payload", "error" in await call(s, "select_board", board_id="nope"))
        sel = await call(s, "select_board", board_id="tangnano9k")
        check("a board can be selected", sel["selected"]["id"] == "tangnano9k")

        # a build that passes
        job = await call(s, "build", project_dir=str(PROJECT), top="blinky", part="fake-1")
        check("build returns a job id and says what to do next", "job_id" in job and "build_status" in job["next"])
        st = await finish(s, job["job_id"])
        check("the build finishes as done with its report", st["state"] == "done" and "bitstream" in st["report"])
        check("the answer tells the agent to read the report first", "Finished" in st["next"] and "report" in st["next"])
        check("both steps are recorded", [x["step"] for x in st["steps"]] == ["synth", "pack"])

        # a build that fails
        bad = await call(s, "build", project_dir=str(PROJECT), top="fail", part="fake-1")
        st = await finish(s, bad["job_id"])
        check("a failing step makes the job failed", st["state"] == "failed" and "Failed" in st["next"])
        check("the failing step is named in the output", st["steps"][-1]["step"] == "pack" and st["steps"][-1]["exit_code"] != 0)

        # one build at a time per add-on
        slow = await call(s, "build", project_dir=str(PROJECT), top="slow", part="fake-1")
        second = await call(s, "build", project_dir=str(PROJECT), top="blinky", part="fake-1")
        check("a second build is refused while one runs", "already running" in second.get("error", ""))
        act = await call(s, "activity")
        check("activity shows the running build, started by the agent", [(b["kind"], b["by"]) for b in act["busy"]] == [("build", "agent")])
        running = await call(s, "build_status", job_id=slow["job_id"])
        check("a running job says to keep polling", running["state"] == "running" and "wait_seconds=50" in running["next"])
        await finish(s, slow["job_id"])
        again = await call(s, "build", project_dir=str(PROJECT), top="blinky", part="fake-1")
        check("when it ends, a new build is accepted", "job_id" in again)
        await finish(s, again["job_id"])

        # problems come back as readable errors, never as a crash
        check("an unknown job is an error", "Unknown job" in (await call(s, "build_status", job_id="nope")).get("error", ""))
        check("a part no add-on covers is an error", "No add-on covers" in (await call(s, "build", project_dir=str(PROJECT), top="x", part="zzz")).get("error", ""))
        check("a planned add-on cannot build", "error" in await call(s, "build", project_dir=str(PROJECT), top="x", part="xc7a35t-csg324-1"))
        check("an unknown add-on is an error", "Unknown add-on" in (await call(s, "backend_control", backend="nope", action="status")).get("error", ""))
        check("a missing folder is an error", "not found" in (await call(s, "build", project_dir=str(PROJECT / "no"), top="x", part="fake-1")).get("error", ""))

        # approvals: only the user decides
        req = await call(s, "request_install", backend="gowin_oss", reason="test")
        check("an install request waits for the user", req["state"] == "pending" and req["plan"]["automatic"] is True)
        again = await call(s, "request_install", backend="gowin_oss")
        check("asking twice does not create a second request", again["request_id"] == req["request_id"])
        check("install_status says it is still waiting", "Still waiting" in (await call(s, "install_status", request_id=req["request_id"]))["note"])
        store.decide_request(req["request_id"], approve=False)
        rej = await call(s, "install_status", request_id=req["request_id"])
        check("a rejection tells the agent to stop", rej["state"] == "rejected" and "Do not retry" in rej["note"])
        check("an unknown request is an error", "Unknown request" in (await call(s, "install_status", request_id="nope")).get("error", ""))
        check("a vendor tool is never removed", "error" in await call(s, "request_uninstall", backend="ise_spartan3"))

        # a failed install-time answer for a planned add-on: approved, but nothing can be installed
        pl = await call(s, "request_install", backend="cyclone5_oss")
        store.decide_request(pl["request_id"], approve=True)
        ok = await call(s, "install_status", request_id=pl["request_id"])
        check("approving a planned add-on installs nothing and says so", ok["state"] == "approved" and "nothing was installed" in ok["note"])


asyncio.run(main())
if failures:
    print("\nFAILED:", failures)
    raise SystemExit(1)
print("\nPROTOCOL OK")
