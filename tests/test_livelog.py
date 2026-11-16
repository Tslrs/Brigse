"""The live build log: Server-Sent Events from the dashboard, while a build is still running.

Needs no vendor tools: the build is a shell script run through the fake add-on.
usage: uv run python tests/test_livelog.py
"""
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = tempfile.mkdtemp()
os.environ["BRIGSE_HOME"] = HOME
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mock_server  # noqa: E402,F401  (registers the fake add-on)
from brigse import actions, dashboard, livelog, store  # noqa: E402
from brigse.backends import localjob  # noqa: E402

failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def read_events(url, limit=30):
    """(seconds since start, event name, data) for every event until the server closes the stream."""
    t0, out, name = time.time(), [], None
    with urllib.request.urlopen(url, timeout=limit) as r:
        assert r.headers["Content-Type"] == "text/event-stream"
        for raw in r:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                out.append((time.time() - t0, name, json.loads(line[6:])))
    return out


project = Path(tempfile.mkdtemp())
job_dir = project / "build_out" / localjob.new_job_id()
job_id = localjob.start("mock", project, job_dir, "blinky", "fake-1", [
    ("synth", ["bash", "-c", "echo first line; sleep 5; echo second line"]),
    ("pack", ["bash", "-c", "echo packing"]),
])
dashboard.start_in_background(8797)
base = "http://127.0.0.1:8797"

ev = read_events(f"{base}/api/jobs/{job_id}/log")
logs = [(t, d) for t, n, d in ev if n == "log"]
text = "".join(d["text"] for _, d in logs)
check("the log arrives in order, with every step", text == "first line\nsecond line\npacking\n")
check("each piece says which step it is from", [d["step"] for _, d in logs] == ["synth", "synth", "pack"])
check("the first line arrives while the build is still running", logs[0][0] < 3.5 and ev[-1][0] > 4.5)
check("the stream ends with the final state", ev[-1][1:] == ("end", {"state": "done"}))
check("state events carry the current step", any(n == "state" and d.get("current_step") == "synth" for _, n, d in ev))

# a second viewer, after the build: the whole log is replayed, then the stream ends at once
t = time.time()
again = read_events(f"{base}/api/jobs/{job_id}/log")
check("a finished build is replayed in full and closes", "".join(d["text"] for _, n, d in again if n == "log") == text and time.time() - t < 8)

try:
    urllib.request.urlopen(f"{base}/api/jobs/nope/log")
    check("an unknown build is refused", False)
except urllib.error.HTTPError as e:
    check("an unknown build is refused", e.code == 404)
try:
    urllib.request.urlopen(urllib.request.Request(f"{base}/api/jobs/{job_id}/log", headers={"Host": "evil.example"}))
    check("another host name is refused", False)
except urllib.error.HTTPError as e:
    check("another host name is refused", e.code == 403)

# a build on another machine (the ISE VM) has no local file: the last lines of its status are used instead
tails = iter([["a", "b"], ["a", "b"], ["b", "c"], None])
states = iter(["running", "running", "running", "done"])
real = actions.job_status
actions.job_status = lambda meta: {"state": next(states), "current_step": "xst", "log_tail": next(tails)}
try:
    got = list(livelog.events({"job_dir": str(project / "nowhere"), "step_order": ["xst"]}, remote_poll=0.01))
finally:
    actions.job_status = real
pieces = [d["text"] for n, d in got if n == "log"]
check("remote log lines are sent when they change, not repeatedly", pieces == ["a\nb\n", "b\nc\n"])
check("and the stream still ends", got[-1][0] == "end")

assert not failures, failures
print("\nLIVELOG OK")
