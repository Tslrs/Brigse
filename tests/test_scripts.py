"""pytest entry point: runs the standalone check scripts that need no vendor tools.

Each script runs in its own process (they set BRIGSE_HOME and read it at import), and a non-zero
exit is a failure. Scripts that need the real FPGA tools (smoke_client, gowin_client, program_client,
test_bare_environment, ...) are not run here; they are run by hand.

usage: uv run pytest
"""
import os
import shutil
import signal
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
    ("test_lint.py", None),
    ("test_program.py", None),
    ("test_localjob.py", None),
    ("test_report_ise.py", None),
    ("test_livelog.py", None),
    ("test_dashboard_security.py", None),
    ("test_hierarchy.py", None),   # the yosys part skips itself without the gowin_oss add-on
    ("test_cocotb.py", None),     # skips itself without `brigse install-cocotb`
    ("test_waves.py", "iverilog"),
    ("test_floorplan.py", None),   # skips itself when the open-source tools are not installed
]


@pytest.mark.parametrize("script,needs", SCRIPTS, ids=[s for s, _ in SCRIPTS])
def test_script(script, needs):
    if needs and not shutil.which(needs):
        pytest.skip(f"{needs} is not installed")
    env = {**os.environ, "PYTHONFAULTHANDLER": "1"}
    proc = subprocess.Popen([sys.executable, str(TESTS / script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    try:
        out, err = proc.communicate(timeout=240)
    except subprocess.TimeoutExpired:
        # a hung script: SIGABRT makes faulthandler print where every thread is stuck
        proc.send_signal(signal.SIGABRT)
        try:
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        out, err = out or "", "TIMED OUT after 240 s\n" + (err or "")
    if proc.returncode != 0:
        # on GitHub Actions this becomes an annotation that can be read without access to the log
        tail = " | ".join(l for l in (out + err).splitlines() if l.strip())[-1500:].replace("%", "%25").replace("\r", "")
        print(f"::error title={script} failed::{tail}")
    assert proc.returncode == 0, out[-3000:] + err[-3000:]
