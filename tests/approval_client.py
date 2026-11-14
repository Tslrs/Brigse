"""Test boards, the dashboard and the approval flow in a throwaway state folder.

The test plays both roles: the agent (through MCP) and the user (through the
dashboard's HTTP API, using the token that only the page receives).

usage: uv run python tests/approval_client.py
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PORT = "8791"
BASE = f"http://127.0.0.1:{PORT}"


def http(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Token"] = token
    req = urllib.request.Request(BASE + path, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


async def call(session, name, **args):
    res = await session.call_tool(name, args)
    data = json.loads(res.content[0].text)
    print(f"== {name}({args}) -> {json.dumps(data)[:300]}")
    return data


async def main():
    with tempfile.TemporaryDirectory() as home:
        env = {**os.environ, "BRIGSE_HOME": home, "BRIGSE_PORT": PORT}
        params = StdioServerParameters(command=sys.executable, args=["-m", "brigse.server"], env=env)
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            print("tools:", names)

            info = await call(s, "dashboard_info")
            assert info["running"], "dashboard should start with the server"
            status, page = http("/")
            assert status == 200 and "Brigse" in page
            token = re.search(r'const TOKEN="([^"]+)"', page).group(1)

            # boards: agent selects, user overrides in the dashboard
            assert (await call(s, "list_boards"))["selected"] is None
            no_part = await call(s, "build", project_dir="/tmp", top="x")
            assert "error" in no_part, "build without part or board must explain itself"
            await call(s, "select_board", board_id="spartan3a_starter")
            assert http("/api/board", {"id": "tangnano9k"}, token)[0] == 200
            assert (await call(s, "list_boards"))["selected"] == "tangnano9k"
            assert http("/api/board", {"id": "nope"}, token)[0] == 400

            # approvals: the agent can ask, only the dashboard can decide
            a = await call(s, "request_install", backend="cyclone5_oss", reason="User picked a DE10-Nano.")
            b = await call(s, "request_install", backend="xilinx7_oss")
            dup = await call(s, "request_install", backend="cyclone5_oss")
            assert dup["request_id"] == a["request_id"], "no duplicate pending requests"
            assert (await call(s, "install_status", request_id=a["request_id"]))["state"] == "pending"
            assert http(f"/api/requests/{a['request_id']}/approve", {})[0] == 403, "no token, no approval"
            assert http(f"/api/requests/{a['request_id']}/approve", {}, "wrong")[0] == 403
            assert (await call(s, "install_status", request_id=a["request_id"]))["state"] == "pending"

            assert http(f"/api/requests/{a['request_id']}/approve", {}, token)[0] == 200
            assert http(f"/api/requests/{b['request_id']}/reject", {}, token)[0] == 200
            st_a = await call(s, "install_status", request_id=a["request_id"])
            st_b = await call(s, "install_status", request_id=b["request_id"])
            assert st_a["state"] == "approved" and "no automatic installer" in st_a["note"]
            assert st_b["state"] == "rejected"
            # a decision is final
            http(f"/api/requests/{b['request_id']}/approve", {}, token)
            assert (await call(s, "install_status", request_id=b["request_id"]))["state"] == "rejected"

            done = await call(s, "request_install", backend="ise_spartan3")
            print("ise:", done.get("state"))
            state = json.loads(http("/api/state")[1])
            assert state["selected_board"] == "tangnano9k" and len(state["requests"]) == 2
            print("\nAPPROVAL FLOW OK")


asyncio.run(main())
