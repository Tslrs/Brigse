"""program, detect_boards and chip_view over MCP, with no board: a fake openFPGALoader records what it is asked.

Checks: a dry run writes nothing, confirm=true alone writes nothing and asks the user (every load needs an approval,
for that exact file, target and memory, used once, valid ten minutes), the loader then runs with the right arguments, SRAM unless flash is
asked for, devices the loader does not list are refused, a failing loader is reported, the programmer is
used by one thing at a time. No vendor tool and no hardware. usage: uv run python tests/test_program.py
"""
import asyncio
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HOME = tempfile.mkdtemp()
FAKE = Path(tempfile.mkdtemp())
os.environ["BRIGSE_HOME"] = HOME
from brigse import core, store  # noqa: E402

(FAKE / "openFPGALoader").write_text("""#!/bin/bash
echo "$@" >> "$FAKE_DIR/log"
case "$1" in
  --scan-usb) echo "Bus 001 Device 002 0403:6010 FTDI Fake Probe" ;;
  --list-fpga) printf 'IDCode manufacturer family model\\n0x0100581b Gowin GW1N GW1NR-9\\n0x0000081b Gowin GW2A GW2A(R)-18(C)\\n0x0000001 xilinx spartan3 xc3s500e\\n' ;;
  --detect) echo "index 0: idcode 0x100581b model GW1NR-9" ;;
  *) if [ -f "$FAKE_DIR/fail" ]; then echo "fail: no device found" >&2; exit 1; fi; echo "Load SRAM: Done" ;;
esac
""")
(FAKE / "openFPGALoader").chmod(0o755 | stat.S_IEXEC)
BIT = Path(tempfile.mkdtemp()).resolve() / "design.fs"     # resolved: on macOS /var is /private/var
BIT.write_bytes(b"not a real bitstream")
failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def loader_calls():
    f = FAKE / "log"
    return f.read_text().splitlines() if f.is_file() else []


def programmed():
    return [c for c in loader_calls() if str(BIT) in c]


async def call(s, name, **args):
    return json.loads((await s.call_tool(name, args)).content[0].text)


async def approve_and_load(s, **args):
    """What an agent does with the user's help: ask, the user approves, ask again."""
    first = await call(s, "program", confirm=True, **args)
    assert first.get("state") == "pending_approval", first
    store.decide_request(first["request_id"], approve=True)
    return await call(s, "program", confirm=True, **args)


async def main():
    env = {**os.environ, "BRIGSE_HOME": HOME, "BRIGSE_DASHBOARD": "0", "FAKE_DIR": str(FAKE),
           "PATH": f"{FAKE}{os.pathsep}{os.environ['PATH']}"}
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name("mock_server.py"))], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()

        d = await call(s, "detect_boards", cable="ft2232")
        check("detect_boards shows the probe and the chain", "Fake Probe" in d["usb_probes"] and d["jtag_detect"]["ok"])
        check("the cable is passed to the loader", any(c.startswith("--detect") and "-c ft2232" in c for c in loader_calls()))

        dry = await call(s, "program", bitstream=str(BIT), board="tangnano9k")
        check("without confirm it is a dry run that says what it would do", dry["dry_run"] and "-b tangnano9k" in dry["would_run"])
        check("a dry run did not call the loader", programmed() == [])
        check("it targets SRAM by default", "SRAM" in dry["target"] and " -f" not in dry["would_run"])
        check("the Tang Nano 9K part is accepted by the device check",
              (await call(s, "program", bitstream=str(BIT), board="tangnano9k", part="gw1nr-lv9qn88pc6/i5")).get("dry_run") is True)
        check("so is the Tang Nano 20K part", (await call(s, "program", bitstream=str(BIT), board="tangnano20k", part="gw2ar-lv18qn88c8/i7")).get("dry_run") is True)
        e = await call(s, "program", bitstream=str(BIT), cable="ft2232", part="xc3s700a-fg484-4")
        check("a device the loader does not list is refused with the reason", "does not list" in e.get("error", "") and programmed() == [])

        for name, args in {"a missing bitstream": dict(bitstream=str(BIT) + ".none", board="tangnano9k"),
                           "no cable and no board": dict(bitstream=str(BIT))}.items():
            check(f"{name} is a readable error", "error" in await call(s, "program", **args))

        args = dict(bitstream=str(BIT), board="tangnano9k")
        pend = await call(s, "program", confirm=True, **args)
        check("confirm alone writes nothing and asks the user", pend.get("state") == "pending_approval" and programmed() == [])
        req = store.get_request(pend["request_id"])
        check("the request names the file, says SRAM, and waits",
              req["kind"] == "program_board" and req["state"] == "pending" and "SRAM" in req["title"]
              and "design.fs" in req["plan"]["what"] and req["plan"]["permanent"] is False)
        again = await call(s, "program", confirm=True, **args)
        check("asking again does not make a second request", again["request_id"] == pend["request_id"] and programmed() == [])
        fl_pend = await call(s, "program", write_flash=True, confirm=True, **args)
        check("an SRAM request cannot be used for flash",
              fl_pend["state"] == "pending_approval" and fl_pend["request_id"] != pend["request_id"]
              and store.get_request(fl_pend["request_id"])["plan"]["permanent"] is True and programmed() == [])
        store.decide_request(fl_pend["request_id"], approve=False)
        rej = await call(s, "program", write_flash=True, confirm=True, **args)
        check("a rejected load is refused and writes nothing", rej.get("state") == "rejected" and programmed() == [])
        store.update_request(fl_pend["request_id"], decided=time.time() - 700)
        retry = await call(s, "program", write_flash=True, confirm=True, **args)
        check("a rejection stops counting after ten minutes", retry["state"] == "pending_approval" and retry["request_id"] != fl_pend["request_id"])
        store.decide_request(retry["request_id"], approve=False)                 # leave nothing pending for flash
        store.update_request(retry["request_id"], decided=time.time() - 700)

        store.decide_request(pend["request_id"], approve=True)
        ok = await call(s, "program", confirm=True, **args)
        check("after approval it loads, and the request is used up",
              ok["ok"] is True and store.get_request(pend["request_id"])["state"] == "done")
        nxt = await call(s, "program", confirm=True, **args)
        check("a second load needs a new approval", nxt.get("state") == "pending_approval" and nxt["request_id"] != pend["request_id"]
              and len(programmed()) == 1)
        check("confirm runs the loader and reports success", ok["ok"] is True and "SRAM" in ok["target"] and "Done" in ok["output"])
        check("it ran with the board and the file, without flash", programmed() == [f"-b tangnano9k {BIT}"])
        last = (await call(s, "activity"))["last_program"]
        check("the last use is remembered, with who did it", last["ok"] and last["file"] == "design.fs" and last["by"] == "agent" and last["flash"] is False)

        fl = await approve_and_load(s, write_flash=True, **args)
        check("flash only when asked, and it says it is persistent", "persistent" in fl["target"] and programmed()[-1] == f"-b tangnano9k -f {BIT}")

        (FAKE / "fail").write_text("")
        bad = await approve_and_load(s, **args)
        check("a failing loader is reported with its output", bad["ok"] is False and "no device found" in bad["output"])
        check("and remembered as a failure", (await call(s, "activity"))["last_program"]["ok"] is False)
        (FAKE / "fail").unlink()

        ready = await call(s, "program", confirm=True, **args)           # the user approves; then the programmer is busy
        store.decide_request(ready["request_id"], approve=True)
        before = len(programmed())
        with store.lock("program", "user", "loading from the dashboard") as holder:
            check("(the test holds the programmer)", holder is None)
            busy = await call(s, "program", bitstream=str(BIT), board="tangnano9k", confirm=True)
            check("the programmer is used by one thing at a time", "in use" in busy.get("error", "") and "you, from the dashboard" in busy["error"])
            check("the loader was not called meanwhile", len(programmed()) == before)
            check("a dry run is still allowed", (await call(s, "program", bitstream=str(BIT), board="tangnano9k")).get("dry_run") is True)
        check("afterwards it works again: the approval was not used up",
              (await call(s, "program", bitstream=str(BIT), board="tangnano9k", confirm=True))["ok"] is True)

        # the agent waits (wait_seconds) and the user approves meanwhile
        n = len(programmed())

        async def user_approves():
            for _ in range(200):                      # wait until the agent's request really exists (max 20 s)
                pending = [r for r in store.list_requests() if r["state"] == "pending"]
                if pending:
                    store.decide_request(pending[0]["id"], approve=True)
                    return
                await asyncio.sleep(0.1)
        waiter = asyncio.create_task(call(s, "program", confirm=True, wait_seconds=40, **args))
        approver = asyncio.create_task(user_approves())
        res = await waiter
        await approver
        check("waiting for the user works: it loads right after the approval", res.get("ok") is True and len(programmed()) == n + 1)

        # an approval is for this file content, once, and for ten minutes
        p2 = await call(s, "program", confirm=True, **args)
        store.decide_request(p2["request_id"], approve=True)
        BIT.write_bytes(b"a different bitstream")
        n = len(programmed())
        swapped = await call(s, "program", confirm=True, **args)
        check("a changed file needs a new approval", swapped.get("state") == "pending_approval"
              and swapped["request_id"] != p2["request_id"] and len(programmed()) == n)
        store.decide_request(swapped["request_id"], approve=True)
        store.update_request(swapped["request_id"], decided=time.time() - 700)
        old = await call(s, "program", confirm=True, **args)
        check("an old approval is not accepted", old.get("state") == "pending_approval" and old["request_id"] != swapped["request_id"]
              and len(programmed()) == n)

        check("chip_view of a missing file is a readable error", "Bitstream not found" in (await call(s, "chip_view", bitstream=str(BIT) + ".none")).get("error", ""))
        cv = await call(s, "chip_view", bitstream=str(BIT))
        check("chip_view without the open-source tools says how to get them", "request_install" in cv.get("error", ""))


asyncio.run(main())

# the user's own load from the dashboard is its own approval: no request, no waiting
os.environ["PATH"] = f"{FAKE}{os.pathsep}{os.environ['PATH']}"
os.environ["FAKE_DIR"] = str(FAKE)
from brigse import actions  # noqa: E402
n = len(programmed())
res = actions.program(str(BIT), None, "tangnano9k", None, False, True, "user")
check("a load the user starts is not held for approval", res["ok"] is True and len(programmed()) == n + 1)

# the device-name matching, on its own
check("Gowin part names become family-size", (core._part_name("gw1nr-lv9qn88pc6/i5"), core._part_name("gw2a-lv18pg256c8/i7"),
      core._part_name("xc3s700a-fg484-4")) == ("gw1nr-9", "gw2a-18", "xc3s700a"))
check("optional letters in a loader model are expanded", core._model_names("GW2A(R)-18(C)") == {"gw2a-18", "gw2ar-18", "gw2a-18c", "gw2ar-18c"})

assert not failures, failures
print("\nPROGRAM OK")
