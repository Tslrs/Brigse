"""Load a bitstream into a board's SRAM (not flash) through the MCP server.

usage: uv run python tests/program_client.py <bitstream> <board>
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    bit, board = sys.argv[1], sys.argv[2]
    env = {**os.environ, "BRIGSE_DASHBOARD": "0"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "brigse.server"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        for args in ({"confirm": False}, {"confirm": True}):
            res = await s.call_tool("program", {"bitstream": bit, "board": board, "write_flash": False, **args})
            print(json.dumps(json.loads(res.content[0].text), indent=1))


asyncio.run(main())
