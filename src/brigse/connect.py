"""`brigse install-agent`: put Brigse into an AI app's MCP settings, then check it really starts.

Only the "brigse" entry is added or replaced. Other servers in the file are kept, and the old
file is saved next to it with the ending .bak. Nothing is written with --dry-run.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

HOME = Path.home()

# app name -> (label, config file). All use the {"mcpServers": {...}} layout.
APPS = {
    "antigravity": ("Google Antigravity", HOME / ".gemini" / "config" / "mcp_config.json"),
    "claude-desktop": ("Claude desktop app", HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"),
}
# Claude Code keeps its settings in a file it rewrites all the time, so we use its own command instead.
CLAUDE_CODE = "claude-code"


def entry() -> dict:
    """How an app should start Brigse: absolute paths, because desktop apps start with a bare PATH."""
    root = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    if uv and (root / "pyproject.toml").is_file():
        return {"command": uv, "args": ["--directory", str(root), "run", "brigse"]}
    return {"command": sys.executable, "args": ["-m", "brigse.server"]}


def _load(path: Path) -> dict:
    if not path.is_file() or not path.read_text().strip():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} is not valid JSON ({e}). Fix or remove it, then run this again; nothing was changed.")
    if not isinstance(data, dict):
        raise SystemExit(f"{path} does not hold a JSON object; nothing was changed.")
    return data


def install(app: str, dry_run: bool = False) -> dict:
    label, path = APPS[app]
    data = _load(path)
    servers = data.setdefault("mcpServers", {})
    new = entry()
    state = "unchanged" if servers.get("brigse") == new else ("updated" if "brigse" in servers else "added")
    result = {"app": label, "file": str(path), "state": state, "entry": new, "others_kept": sorted(k for k in servers if k != "brigse")}
    if state == "unchanged" or dry_run:
        return result
    servers["brigse"] = new
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
        result["backup"] = str(path.with_name(path.name + ".bak"))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return result


async def _handshake(cmd: dict) -> list[str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    # What a desktop app gives a server: a short PATH and no dashboard window of its own.
    env = {"HOME": str(HOME), "PATH": "/usr/bin:/bin", "BRIGSE_DASHBOARD": "0"}
    params = StdioServerParameters(command=cmd["command"], args=cmd["args"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return sorted(t.name for t in (await session.list_tools()).tools)


def check(cmd: dict | None = None, timeout: int = 90) -> dict:
    """Start Brigse the way an app would and list its tools."""
    try:
        tools = asyncio.run(asyncio.wait_for(_handshake(cmd or entry()), timeout))
    except Exception as e:  # noqa: BLE001 - anything here means "the app would not get a working server"
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "tools": tools}


def detect() -> list[str]:
    """Apps that look installed: their config folder exists."""
    found = [a for a, (_, p) in APPS.items() if p.parent.is_dir()]
    return found + ([CLAUDE_CODE] if shutil.which("claude") else [])


def _claude_code(dry_run: bool) -> bool:
    import subprocess
    cmd = ["claude", "mcp", "add", "--scope", "user", "brigse", "--", entry()["command"], *entry()["args"]]
    shown = " ".join(f"'{c}'" if " " in c else c for c in cmd)
    if dry_run or not shutil.which("claude"):
        print(("[dry run] Would run" if dry_run else "The `claude` command was not found. Run this in a terminal where it works") + f":\n  {shown}")
        return True
    subprocess.run(["claude", "mcp", "remove", "--scope", "user", "brigse"], capture_output=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("Added Brigse to Claude Code (user scope)." if r.returncode == 0 else f"Claude Code refused: {r.stdout}{r.stderr}")
    return r.returncode == 0


def run(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    names = [a for a in argv if not a.startswith("--")]
    if "--all" in argv:
        names = detect()
    if not names:
        found = detect()
        print("Usage: brigse install-agent <app> [--dry-run]\n       brigse install-agent --all\n")
        print("Apps: " + ", ".join([f"{a} ({APPS[a][0]})" for a in APPS] + [f"{CLAUDE_CODE} (Claude Code)"]))
        print("Found on this Mac: " + (", ".join(found) if found else "none"))
        return 2
    unknown = [n for n in names if n not in APPS and n != CLAUDE_CODE]
    if unknown:
        print(f"Unknown app: {', '.join(unknown)}. Choose from: {', '.join([*APPS, CLAUDE_CODE])}")
        return 2
    for n in names:
        if n == CLAUDE_CODE:
            if not _claude_code(dry):
                return 1
            continue
        r = install(n, dry)
        verb = {"added": "Added Brigse to", "updated": "Updated Brigse in", "unchanged": "Brigse is already set up in"}[r["state"]]
        print(f"{'[dry run] ' if dry else ''}{verb} {r['app']}: {r['file']}")
        if r["others_kept"]:
            print(f"  kept your other servers: {', '.join(r['others_kept'])}")
        if r.get("backup"):
            print(f"  old file saved as {r['backup']}")
    if dry:
        print("\nNothing was written (dry run).")
        return 0
    print("\nChecking that Brigse starts the way the app will start it ...")
    res = check()
    if not res["ok"]:
        print(f"  FAILED: {res['error']}")
        return 1
    print(f"  OK: the server answered with {len(res['tools'])} tools.")
    print("\nLast step: restart the app (Antigravity: Customizations > MCP Servers > Refresh), then ask it to"
          ' "use brigse and list the add-ons".')
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
