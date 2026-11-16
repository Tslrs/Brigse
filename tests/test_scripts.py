"""pytest entry point: runs the standalone check scripts that need no vendor tools.

Each script runs in its own process (they set BRIGSE_HOME and read it at import), and a non-zero
exit is a failure. Scripts that need the real FPGA tools (smoke_client, gowin_client, program_client,
test_bare_environment, ...) are not run here; they are run by hand.

usage: uv run pytest
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

SCRIPTS = [
    ("test_diagnose.py", None),
    ("test_cli.py", None),
    ("test_connect.py", None),
    ("test_protocol.py", None),
    ("test_waves.py", "iverilog"),
    ("test_floorplan.py", None),   # skips itself when the open-source tools are not installed
]


@pytest.mark.parametrize("script,needs", SCRIPTS, ids=[s for s, _ in SCRIPTS])
def test_script(script, needs):
    if needs and not shutil.which(needs):
        pytest.skip(f"{needs} is not installed")
    r = subprocess.run([sys.executable, str(TESTS / script)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
