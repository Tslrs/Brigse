"""Download and locate the OSS CAD Suite (yosys, nextpnr, apicula, ...).

One install is shared by every open-source add-on. It lives under
<data folder>/tools/oss-cad-suite and is only ever installed after the user
approved the plan in the dashboard.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from . import store
from .backends.base import BackendError

REPO_API = "https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases?per_page=8"
URL_PREFIX = "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/"
TOOLS = store.HOME / "tools"
SUITE = TOOLS / "oss-cad-suite"


def platform_tag() -> str:
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(platform.machine())
    if not system or not arch:
        raise BackendError(f"No OSS CAD Suite build for {platform.system()} {platform.machine()}.")
    return f"{system}-{arch}"


def tool(name: str) -> Path:
    return SUITE / "bin" / name


def installed(names: tuple[str, ...]) -> bool:
    return all(tool(n).is_file() for n in names)


def env() -> dict:
    return {**os.environ, "PATH": f"{SUITE / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}


def latest_asset() -> dict:
    """Newest release that has a build for this machine (the newest one may still be uploading)."""
    tag = platform_tag()
    try:
        req = urllib.request.Request(REPO_API, headers={"Accept": "application/vnd.github+json",
                                                        "User-Agent": "brigse"})
        with urllib.request.urlopen(req, timeout=30) as r:
            releases = json.load(r)
    except Exception as e:
        raise BackendError(f"Could not reach GitHub to look up the OSS CAD Suite release: {e}")
    for rel in releases:
        for a in rel.get("assets", []):
            if f"-{tag}-" in a["name"] and a["name"].endswith(".tgz"):
                url = a["browser_download_url"]
                if not url.startswith(URL_PREFIX):
                    continue
                digest = a.get("digest") or ""
                return {"name": a["name"], "url": url, "bytes": a["size"], "release": rel["tag_name"],
                        "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else None}
    raise BackendError(f"No OSS CAD Suite build for {tag} in the recent releases.")


def disk_bytes() -> int:
    if not SUITE.exists():
        return 0
    out = subprocess.run(["du", "-sk", str(SUITE)], capture_output=True, timeout=120).stdout.split()
    return int(out[0]) * 1024 if out else 0


def uninstall(progress=lambda text: None) -> str:
    """Delete the suite and any half-finished download. Only this folder is ever removed."""
    if SUITE.name != "oss-cad-suite" or SUITE.parent != TOOLS or SUITE.is_symlink() or TOOLS.is_symlink():
        raise BackendError("Refusing to delete: the tools folder is not the one Brigse created.")
    freed = disk_bytes()
    progress("Deleting files")
    shutil.rmtree(SUITE, ignore_errors=True)
    shutil.rmtree(TOOLS / "oss-cad-suite.new", ignore_errors=True)
    for part in TOOLS.glob("oss-cad-suite-*.tgz.part"):
        part.unlink(missing_ok=True)
    if SUITE.exists():
        raise BackendError(f"Could not delete {SUITE} completely.")
    return f"Removed {SUITE}; freed about {freed / 1e9:.1f} GB."


def install(asset: dict, needs: tuple[str, ...], progress=lambda text: None) -> str:
    """Download the approved asset, verify it, unpack it and check the tools run."""
    if not asset["url"].startswith(URL_PREFIX):
        raise BackendError("Refusing to download from an unexpected address.")
    TOOLS.mkdir(parents=True, exist_ok=True)
    archive = TOOLS / (asset["name"] + ".part")
    digest, done, total = hashlib.sha256(), 0, asset["bytes"]
    req = urllib.request.Request(asset["url"], headers={"User-Agent": "brigse"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(archive, "wb") as f:
            last = -1
            while chunk := r.read(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                pct = done * 100 // total if total else 0
                if pct != last:
                    last = pct
                    progress(f"Downloading {asset['name']}: {pct}% of {total // 1_000_000} MB")
        if asset.get("sha256") and digest.hexdigest() != asset["sha256"]:
            raise BackendError("Downloaded file does not match the published SHA-256; nothing was installed.")
        progress("Unpacking")
        staging = TOOLS / "oss-cad-suite.new"
        shutil.rmtree(staging, ignore_errors=True)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(staging, filter="tar")
        inner = staging / "oss-cad-suite"
        if not inner.is_dir():
            raise BackendError("Unexpected archive layout; nothing was installed.")
        shutil.rmtree(SUITE, ignore_errors=True)
        inner.rename(SUITE)
        shutil.rmtree(staging, ignore_errors=True)
    except BackendError:
        raise
    except Exception as e:
        raise BackendError(f"Install failed: {e}")
    finally:
        archive.unlink(missing_ok=True)

    progress("Checking the tools run")
    missing = [n for n in needs if not tool(n).is_file()]
    if missing:
        raise BackendError(f"Installed, but these tools are missing from the suite: {missing}")
    p = subprocess.run([str(tool("yosys")), "-V"], capture_output=True, timeout=120, env=env())
    if p.returncode != 0:
        raise BackendError(f"yosys does not run: {(p.stdout + p.stderr).decode()[-300:]}")
    (SUITE / "BRIGSE_RELEASE").write_text(asset["release"])
    return f"OSS CAD Suite {asset['release']} installed; {p.stdout.decode().strip()[:80]}"
