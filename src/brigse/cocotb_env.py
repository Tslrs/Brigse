"""cocotb (Python testbenches) in its own small environment, made by the user with `brigse install-cocotb`.

It is kept apart from Brigse's own Python because cocotb supports fewer Python versions. The server
never downloads it: the user runs the command, so it is the user who allows the download (about 40 MB).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import store

PYTHON_VERSION = "3.13"
ENV = store.HOME / "cocotb-env"


def python() -> Path | None:
    """The Python that has cocotb, or None. BRIGSE_COCOTB_PYTHON overrides the default place."""
    custom = os.environ.get("BRIGSE_COCOTB_PYTHON")
    candidate = Path(custom) if custom else ENV / "bin" / "python"
    return candidate if candidate.is_file() else None


def install() -> int:
    py = python()
    if py and subprocess.run([str(py), "-c", "import cocotb"], capture_output=True).returncode == 0:
        print(f"cocotb is already installed ({py}).")
        return 0
    uv = shutil.which("uv")
    if not uv:
        print("uv is needed: brew install uv")
        return 1
    print(f"Creating {ENV} (Python {PYTHON_VERSION}) and installing cocotb. About 40 MB.")
    for cmd in ([uv, "venv", "--python", PYTHON_VERSION, "--seed", str(ENV)],
                [uv, "pip", "install", "--python", str(ENV / "bin" / "python"), "cocotb"]):
        if subprocess.run(cmd).returncode != 0:
            print("That failed; nothing else was changed.")
            return 1
    ok = subprocess.run([str(ENV / "bin" / "python"), "-c", "import cocotb; print('cocotb', cocotb.__version__)"]).returncode == 0
    print("Ready. Ask the agent to run a cocotb test." if ok else "cocotb did not import.")
    return 0 if ok else 1


def remove() -> int:
    if ENV.is_dir():
        shutil.rmtree(ENV)
        print(f"Removed {ENV}")
    else:
        print("Nothing to remove.")
    return 0


if __name__ == "__main__":
    raise SystemExit(remove() if sys.argv[1:2] == ["--remove"] else install())
