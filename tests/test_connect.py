"""`brigse install-agent` must add only its own entry, keep the rest, back up, and refuse broken files.

Runs in a throw-away HOME, so your real app settings are never touched.
usage: uv run python tests/test_connect.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

home = Path(tempfile.mkdtemp())
cfg = home / ".gemini" / "config" / "mcp_config.json"
cfg.parent.mkdir(parents=True)
cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "keep": 1}))
env = {**os.environ, "HOME": str(home)}


def brigse(*args):
    r = subprocess.run([sys.executable, "-m", "brigse.connect", *args], capture_output=True, text=True, env=env, cwd=os.getcwd())
    return r.returncode, r.stdout + r.stderr


rc, out = brigse("antigravity", "--dry-run")
assert rc == 0 and "dry run" in out
assert "brigse" not in json.loads(cfg.read_text())["mcpServers"], "dry run must not write"

rc, out = brigse("antigravity")
assert rc == 0, out
data = json.loads(cfg.read_text())
assert data["keep"] == 1 and data["mcpServers"]["other"] == {"command": "x"}, "other settings must stay"
assert data["mcpServers"]["brigse"]["command"], out
assert cfg.with_name("mcp_config.json.bak").is_file(), "old file must be saved"
assert "tools" in out, out
print("added, kept the rest, backed up, and the server answered:", [l.strip() for l in out.splitlines() if "tools" in l][0])

rc, out = brigse("antigravity", "--dry-run")
assert "already set up" in out, "a second run changes nothing"

cfg.write_text("{ not json")
rc, out = brigse("antigravity")
assert rc != 0 and cfg.read_text() == "{ not json", "a broken file must be left alone"

rc, out = brigse("nonsense")
assert rc == 2
print("\nINSTALL-AGENT OK")
