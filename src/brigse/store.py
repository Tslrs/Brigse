"""Small on-disk state shared by the MCP server and the dashboard.

Both may run as separate processes, so everything is plain JSON files under
~/.brigse, written atomically.
"""
from __future__ import annotations

import contextlib
import json
import os
import secrets
import time
from pathlib import Path

HOME = Path(os.environ.get("BRIGSE_HOME", Path.home() / ".brigse"))
JOBS = HOME / "jobs"
REQUESTS = HOME / "requests"
LOCKS = HOME / "locks"
STATE = HOME / "state.json"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ---- selected board -------------------------------------------------------
def selected_board() -> str | None:
    return _read(STATE).get("board")


def select_board(board_id: str | None, by: str) -> None:
    state = _read(STATE)
    state.update(board=board_id, board_selected_by=by, board_selected_at=time.time())
    _write(STATE, state)


# ---- approval requests ----------------------------------------------------
def new_request(kind: str, subject: str, title: str, plan: dict, reason: str) -> dict:
    """Create a request that only the dashboard user can approve."""
    for r in list_requests():
        if r["kind"] == kind and r["subject"] == subject and r["state"] == "pending":
            return r
    req = {"id": time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2), "kind": kind,
           "subject": subject, "title": title, "plan": plan, "reason": reason[:500],
           "state": "pending", "created": time.time(), "decided": None, "result": None}
    _write(REQUESTS / f"{req['id']}.json", req)
    return req


def get_request(request_id: str) -> dict | None:
    if not request_id.replace("-", "").isalnum():
        return None
    return _read(REQUESTS / f"{request_id}.json") or None


def list_requests() -> list[dict]:
    if not REQUESTS.is_dir():
        return []
    reqs = [_read(p) for p in REQUESTS.glob("*.json")]
    return sorted((r for r in reqs if r), key=lambda r: r["created"], reverse=True)


def decide_request(request_id: str, approve: bool) -> dict | None:
    """Called by the dashboard only. A decision is final."""
    req = get_request(request_id)
    if not req or req["state"] != "pending":
        return req
    req.update(state="approved" if approve else "rejected", decided=time.time())
    _write(REQUESTS / f"{request_id}.json", req)
    return req


def update_request(request_id: str, **fields) -> dict | None:
    req = get_request(request_id)
    if req:
        req.update(fields)
        _write(REQUESTS / f"{request_id}.json", req)
    return req


def finish_request(request_id: str, ok: bool, result: str) -> None:
    req = get_request(request_id)
    if req and req["state"] in ("approved", "installing", "removing"):
        done = "removed" if req["kind"] == "uninstall_backend" else "installed"
        update_request(request_id, state=done if ok else "failed",
                       result=result[:1000], progress=None)


# ---- jobs -----------------------------------------------------------------
def list_jobs(limit: int = 8) -> list[dict]:
    if not JOBS.is_dir():
        return []
    jobs = [_read(p) for p in JOBS.glob("*.json")]
    return sorted((j for j in jobs if j), key=lambda j: j["started"], reverse=True)[:limit]


def get_job(job_id: str) -> dict | None:
    if not job_id.replace("-", "").isalnum():
        return None
    return _read(JOBS / f"{job_id}.json") or None


def update_job(job_id: str, **fields) -> None:
    job = get_job(job_id)
    if job:
        job.update(fields)
        _write(JOBS / f"{job_id}.json", job)


# ---- general state --------------------------------------------------------
def get_state() -> dict:
    return _read(STATE)


def set_state(**fields) -> None:
    state = _read(STATE)
    state.update(fields)
    _write(STATE, state)


# ---- locks: one user of a shared resource at a time -------------------------
def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def locks() -> list[dict]:
    """Locks currently held. A lock whose process died, or older than 15 minutes, is stale."""
    held = []
    for p in LOCKS.glob("*.json") if LOCKS.is_dir() else []:
        data = _read(p)
        if data and _alive(data["pid"]) and time.time() - data["since"] < 900:
            held.append(data)
        else:
            p.unlink(missing_ok=True)
    return held


@contextlib.contextmanager
def lock(name: str, by: str, what: str):
    """Yield None when the lock was taken, or the current holder's record when it is busy."""
    holder = next((l for l in locks() if l["name"] == name), None)
    if holder:
        yield holder
        return
    path = LOCKS / f"{name}.json"
    _write(path, {"name": name, "by": by, "what": what, "since": time.time(), "pid": os.getpid()})
    try:
        yield None
    finally:
        path.unlink(missing_ok=True)
