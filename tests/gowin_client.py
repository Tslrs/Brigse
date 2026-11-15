"""Build examples/tangnano9k_blinky for a Tang Nano 9K through the MCP server.

usage: uv run python tests/gowin_client.py [gowin_eda|gowin_oss]
Uses your real Brigse data folder; needs the chosen add-on to be usable.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BLINKY = Path(__file__).resolve().parent.parent / "examples" / "tangnano9k_blinky"
BACKEND = sys.argv[1] if len(sys.argv) > 1 else None


async def call(session, name, **args):
    res = await session.call_tool(name, args)
    data = json.loads(res.content[0].text)
    print(f"== {name}({args})\n{json.dumps(data, indent=1)[:1500]}")
    return data


async def main():
    env = {**os.environ, "BRIGSE_DASHBOARD": "0"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "brigse.server"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        args = {"project_dir": str(BLINKY), "top": "blinky", "part": "gw1nr-lv9qn88pc6/i5"}
        if BACKEND:
            args["backend"] = BACKEND
        job = await call(s, "build", **args)
        assert "job_id" in job, job
        while True:
            st = await call(s, "build_status", job_id=job["job_id"], wait_seconds=30)
            if st.get("state") != "running":
                break
        assert st["state"] == "done", "build should succeed"
        assert st["report"]["bitstream"], "a bitstream should exist"
        assert st["report"]["timing"]["met"], "blinky should meet timing"
        auto = await call(s, "report", outputs_dir=st["outputs_dir"])
        assert auto["backend"] == job["backend"]
        dry = await call(s, "program", bitstream=st["report"]["bitstream"]["path"], board="tangnano9k")
        assert dry.get("dry_run"), dry
        print(f"\nGOWIN BUILD OK ({job['backend']})")


asyncio.run(main())
