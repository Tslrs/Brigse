"""Check that the agent (MCP) and the user (dashboard buttons) cannot collide.

Runs in a throwaway state folder that borrows the installed open-source tools.
Nothing is loaded onto a board.

usage: uv run python tests/actions_client.py
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PORT = "8792"
BASE = f"http://127.0.0.1:{PORT}"
PROJECT = Path(__file__).resolve().parent.parent / "examples" / "tangnano9k_blinky"
REAL_TOOLS = Path.home() / ".brigse" / "tools"


def http(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Token"] = token
    req = urllib.request.Request(BASE + path, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}") if path != "/" else r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


async def call(session, name, **args):
    res = await session.call_tool(name, args)
    data = json.loads(res.content[0].text)
    print(f"== {name} -> {json.dumps(data)[:230]}")
    return data


async def wait_idle(s):
    while (await call(s, "activity"))["busy"]:
        await asyncio.sleep(2)


async def main():
    assert REAL_TOOLS.is_dir(), "install the gowin_oss add-on first"
    with tempfile.TemporaryDirectory() as home:
        os.symlink(REAL_TOOLS, Path(home) / "tools")
        env = {**os.environ, "BRIGSE_HOME": home, "BRIGSE_PORT": PORT}
        params = StdioServerParameters(command=sys.executable, args=["-m", "brigse.server"], env=env)
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            token = re.search(r'const TOKEN="([^"]+)"', http("/")[1]).group(1)
            args = dict(project_dir=str(PROJECT), top="blinky", part="gw1nr-lv9qn88pc6/i5", backend="gowin_oss")

            # 1. the agent builds; a second build on the same add-on is refused
            job = await call(s, "build", **args)
            assert "job_id" in job, job
            second = await call(s, "build", **args)
            assert "already running" in second.get("error", ""), second

            # 2. while it runs, the dashboard shows it and refuses the user's buttons
            state = http("/api/state")[1]
            assert state["busy"] and state["busy"][0]["by"] == "agent", state["busy"]
            code, body = http(f"/api/jobs/{job['job_id']}/rebuild", {}, token)
            assert code == 409 and "Busy" in body["error"], (code, body)
            assert http("/api/board", {"id": "tangnano9k"}, token)[0] == 409

            # 3. when it has finished, the buttons work
            await wait_idle(s)
            time.sleep(3.2)
            view = http("/api/state")[1]["jobs"][0]
            assert view["state"] == "done" and view["can_rebuild"] and view["load_target"], view
            assert http("/api/board", {"id": "tangnano9k"}, token)[0] == 200

            # 4. the user builds from the dashboard; now the agent is the one refused
            code, body = http(f"/api/jobs/{job['job_id']}/rebuild", {}, token)
            assert code == 200 and body["job_id"] != job["job_id"], (code, body)
            act = await call(s, "activity")
            assert act["busy"][0]["by"] == "user", act
            refused = await call(s, "build", **args)
            assert "you, from the dashboard" in refused.get("error", ""), refused
            await wait_idle(s)

            # 5. the programmer: one user at a time (a held lock stands in for a load in progress)
            locks = Path(home) / "locks"
            locks.mkdir(exist_ok=True)
            (locks / "program.json").write_text(json.dumps({
                "name": "program", "by": "user", "what": "loading test.fs to tangnano9k",
                "since": time.time(), "pid": os.getpid()}))
            bit = view["report"]["bitstream"]["path"]
            busy = await call(s, "program", bitstream=bit, board="tangnano9k", confirm=True)
            assert "in use" in busy.get("error", ""), busy
            assert http(f"/api/jobs/{job['job_id']}/load", {}, token)[0] == 409
            dry = await call(s, "program", bitstream=bit, board="tangnano9k")
            assert dry.get("dry_run"), "a dry run never needs the programmer"
            (locks / "program.json").unlink()
            assert not (await call(s, "activity"))["busy"]
            print("\nNO COLLISIONS OK")


asyncio.run(main())
