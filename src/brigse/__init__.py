"""Modular MCP server for FPGA toolchains."""
import os as _os

# An agent started from a desktop app (not from a terminal) gets a bare PATH, without the
# Homebrew folders where openFPGALoader, Icarus Verilog and Lima live, and without the system sbin
# folders the Gowin IDE cannot work out this machine's id and rejects a valid licence. Add them.
for _extra in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/sbin", "/sbin"):
    if _os.path.isdir(_extra) and _extra not in _os.environ.get("PATH", "").split(_os.pathsep):
        _os.environ["PATH"] = _os.environ.get("PATH", "") + _os.pathsep + _extra
