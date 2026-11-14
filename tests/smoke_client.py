"""End-to-end smoke test: talk to the server over stdio exactly as an MCP client would.

usage: uv run python tests/smoke_client.py [--build]
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
BLINKY = ROOT / "examples" / "blinky"


async def call(session, name, **args):
    res = await session.call_tool(name, args)
    data = json.loads(res.content[0].text)
    print(f"\n== {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
    print(json.dumps(data, indent=1)[:1800])
    return data


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "brigse.server"])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = (await s.list_tools()).tools
        print("tools:", [t.name for t in tools])
        await call(s, "list_backends")
        sim = await call(s, "simulate", sources=["blinky.v", "tb_blinky.v"], cwd=str(BLINKY))
        assert sim["passed"], "simulation should pass"
        bad = await call(s, "build", project_dir=str(BLINKY), top="blinky", part="xc7a35t-csg324-1")
        assert "error" in bad, "unimplemented add-on must be reported, not crash"
        if (ROOT / "out").is_dir():
            await call(s, "report", outputs_dir=str(ROOT / "out"))
        if "--build" in sys.argv:
            job = await call(s, "build", project_dir=str(BLINKY), top="blinky", part="xc3s700a-fg484-4")
            assert "job_id" in job, job
            while True:
                st = await call(s, "build_status", job_id=job["job_id"], wait_seconds=45)
                if st.get("state") != "running":
                    break
            assert st["state"] == "done", "build should succeed"
            bit = st["report"]["bitstream"]["path"]
            dry = await call(s, "program", bitstream=bit, cable="ft2232", part="xc3s700a-fg484-4")
            print("\nprogram check:", dry.get("error") or dry.get("would_run"))
        print("\nSMOKE OK")


asyncio.run(main())
