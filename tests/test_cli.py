"""The terminal commands: `brigse status` and `brigse approvals`.

Approving must work in a real terminal and must refuse when nobody is typing (a pipe, as an agent has).
Runs in a throw-away BRIGSE_HOME. usage: uv run python tests/test_cli.py
"""
import os
import pty
import subprocess
import sys
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

pid, fd = pty.fork()
if pid == 0:
    os.execvpe(sys.executable, [sys.executable, "-m", "brigse.server", "approvals"], env)
out = b""
os.read(fd, 4096)
try:
    out += os.read(fd, 4096)
except OSError:
    pass
os.write(fd, b"maybe\n")
os.read(fd, 4096)
os.write(fd, b"reject\n")
rest = b""
while True:
    try:
        chunk = os.read(fd, 4096)
    except OSError:
        break
    if not chunk:
        break
    rest += chunk
os.waitpid(pid, 0)
state = subprocess.run([sys.executable, "-c", f"from brigse import store; print(store.get_request('{rid}')['state'])"],
                       capture_output=True, text=True, env=env).stdout.strip()
assert state == "rejected", f"typing reject in a terminal must reject (state: {state})"
assert b"whole word" in out + rest, "a wrong answer must be refused, not guessed"
print("in a terminal: a wrong answer is refused, 'reject' rejects")
print("\nCLI OK")
