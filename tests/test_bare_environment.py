"""Brigse must find its tools when a desktop app starts it with a bare PATH (as Antigravity does).

usage: uv run python tests/test_bare_environment.py
"""
import os
import shutil
import subprocess

uv = shutil.which("uv")
code = ("import shutil, brigse; "
        "print(','.join(f'{t}={bool(shutil.which(t))}' for t in ('iverilog', 'openFPGALoader', 'limactl')))")
bare = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin"}
out = subprocess.run([uv, "--directory", os.getcwd(), "run", "python", "-c", code],
                     capture_output=True, text=True, env=bare).stdout.strip().splitlines()[-1]
found = dict(item.split("=") for item in out.split(","))
print("tools found from a bare PATH:", found)
missing = [t for t, ok in found.items() if ok != "True" and shutil.which(t)]   # only complain about tools that exist on this Mac
assert not missing, f"not found from a bare PATH although installed: {missing}"

# the Gowin IDE must still accept its licence: without the sbin folders on PATH it does not
code = ("from brigse.backends.gowin import GowinEdaBackend; s = GowinEdaBackend().status(); "
        "print(s['installed'], s.get('usable'))")
normal = subprocess.run([uv, "--directory", os.getcwd(), "run", "python", "-c", code], capture_output=True, text=True).stdout.split()
barely = subprocess.run([uv, "--directory", os.getcwd(), "run", "python", "-c", code], capture_output=True, text=True, env=bare).stdout.split()
print("Gowin IDE usable from a terminal:", normal, "| from a bare PATH:", barely)
assert normal == barely, "the Gowin IDE behaves differently when started from a bare environment"
print("\nBARE ENVIRONMENT OK")
