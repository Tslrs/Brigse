"""The terminal commands: `brigse status` and `brigse approvals`.

Approving must work in a real terminal and must refuse when nobody is typing (a pipe, as an agent has).
Runs in a throw-away BRIGSE_HOME. usage: uv run python tests/test_cli.py
"""
import os
import pty
import select
import signal
import subprocess
import sys
import time
import tempfile

home = tempfile.mkdtemp()
env = {**os.environ, "BRIGSE_HOME": home, "BRIGSE_DASHBOARD": "0"}


def brigse(*args):
    return subprocess.run([sys.executable, "-m", "brigse.server", *args], capture_output=True, text=True, env=env)


code = ("from brigse import store; r = store.new_request('install_backend', 'x', 'Install the example add-on', "
        "{'what': 'an example', 'source': 'nowhere', 'disk': '1 MB', 'licence': 'MIT', 'automatic': False}, 'test'); print(r['id'])")
rid = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env).stdout.strip()
assert rid, "could not create a request"

r = brigse("status")
assert r.returncode == 0 and "The agent is asking (1)" in r.stdout and "Install the example add-on" in r.stdout, r.stdout + r.stderr
print("status shows the pending request")

r = brigse("approvals")                       # captured output = not a terminal
assert r.returncode == 3 and "real terminal" in r.stdout, r.stdout + r.stderr
state = subprocess.run([sys.executable, "-c", f"from brigse import store; print(store.get_request('{rid}')['state'])"],
                       capture_output=True, text=True, env=env).stdout.strip()
assert state == "pending", "a pipe must not be able to approve"
print("a pipe is refused and the request stays pending")

PROMPT = b"[approve/reject/skip]"


def read_until(fd, buf, marker, count=1, timeout=30):
    """Read from the terminal until `marker` has appeared `count` times: never guess how much arrives per read."""
    deadline = time.time() + timeout
    while buf.count(marker) < count:
        left = deadline - time.time()
        assert left > 0, f"timed out waiting for {marker!r}; got {buf!r}"
        if select.select([fd], [], [], left)[0]:
            try:
                chunk = os.read(fd, 4096)
            except OSError:          # the child closed the terminal
                break
            if not chunk:
                break
            buf += chunk
    return buf


pid, fd = pty.fork()
if pid == 0:
    os.execvpe(sys.executable, [sys.executable, "-m", "brigse.server", "approvals"], env)
try:
    out = read_until(fd, b"", PROMPT, 1)
    os.write(fd, b"maybe\n")
    out = read_until(fd, out, PROMPT, 2)         # a wrong answer is refused and asked again
    os.write(fd, b"reject\n")
    deadline = time.time() + 30
    rest = b""
    while time.time() < deadline:                # until the program ends
        if select.select([fd], [], [], 1)[0]:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            rest += chunk
    else:
        raise AssertionError(f"the program did not end after 'reject'; got {rest!r}")
    os.waitpid(pid, 0)
    pid = None
finally:
    if pid:                                        # a failure above: do not leave the child running
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
state = subprocess.run([sys.executable, "-c", f"from brigse import store; print(store.get_request('{rid}')['state'])"],
                       capture_output=True, text=True, env=env).stdout.strip()
assert state == "rejected", f"typing reject in a terminal must reject (state: {state})"
assert b"whole word" in out + rest, "a wrong answer must be refused, not guessed"
print("in a terminal: a wrong answer is refused, 'reject' rejects")
print("\nCLI OK")
