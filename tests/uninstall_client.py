"""Test uninstall with a fake tools folder in a throwaway state folder.

Your real downloaded tools are never touched by this test.

usage: uv run python tests/uninstall_client.py
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

PORT = "8793"
BASE = f"http://127.0.0.1:{PORT}"


def http(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Token"] = token
    req = urllib.request.Request(BASE + path, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, raw if path == "/" else json.loads(raw or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def fake_suite(home):
    bin_dir = Path(home) / "tools" / "oss-cad-suite" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("yosys", "nextpnr-himbaechel", "gowin_pack"):
        (bin_dir / name).write_bytes(b"x" * 4096)
    return bin_dir.parent


async def call(session, name, **args):
    res = await session.call_tool(name, args)
    data = json.loads(res.content[0].text)
    print(f"== {name}({args}) -> {json.dumps(data)[:240]}")
    return data


async def wait_state(s, request_id, want):
    for _ in range(30):
        st = await call(s, "install_status", request_id=request_id)
        if st["state"] == want:
            return st
        await asyncio.sleep(1)
    raise AssertionError(f"request never reached {want}: {st}")


async def main():
    with tempfile.TemporaryDirectory() as home:
        suite = fake_suite(home)
        keep = Path(home) / "tools" / "something-else"
        keep.mkdir()
        env = {**os.environ, "BRIGSE_HOME": home, "BRIGSE_PORT": PORT}
        params = StdioServerParameters(command=sys.executable, args=["-m", "brigse.server"], env=env)
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            token = re.search(r'const TOKEN="([^"]+)"', http("/")[1]).group(1)

            # vendor tools and VMs the user set up are never removable
            for name in ("gowin_eda", "ise_spartan3"):
                assert "not installed by Brigse" in (await call(s, "request_uninstall", backend=name))["error"]

            # the agent asks; nothing is deleted until the user approves
            req = await call(s, "request_uninstall", backend="gowin_oss", reason="Freeing disk space.")
            assert req["state"] == "pending" and suite.is_dir()
            assert (await call(s, "install_status", request_id=req["request_id"]))["state"] == "pending"
            assert suite.is_dir(), "still there while pending"

            # a rejection keeps the files
            assert http(f"/api/requests/{req['request_id']}/reject", {}, token)[0] == 200
            assert (await call(s, "install_status", request_id=req["request_id"]))["state"] == "rejected"
            assert suite.is_dir(), "rejected: nothing deleted"

            # an approval deletes only the suite folder
            req = await call(s, "request_uninstall", backend="gowin_oss")
            assert http(f"/api/requests/{req['request_id']}/approve", {}, token)[0] == 200
            done = await wait_state(s, req["request_id"], "removed")
            assert not suite.exists() and keep.is_dir(), "only the suite folder goes"
            print("result:", done.get("result"))
            time.sleep(3.2)
            state = http("/api/state")[1]
            oss = next(b for b in state["backends"] if b["name"] == "gowin_oss")
            assert not oss["installed"] and oss["buttons"] == ["install"], oss
            assert "error" in await call(s, "request_uninstall", backend="gowin_oss"), "nothing left to remove"

            # the user's own button works the same way
            suite = fake_suite(home)
            time.sleep(3.2)
            code, body = http("/api/backends/gowin_oss/uninstall", {}, token)
            assert code == 200, (code, body)
            await wait_state(s, body["request_id"], "removed")
            assert not suite.exists()
            print("\nUNINSTALL OK")


asyncio.run(main())
