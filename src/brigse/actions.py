"""Operations shared by the MCP tools (the agent) and the dashboard buttons (the user).

Both go through the same functions, so the same rules stop two actions from
colliding: one build at a time per add-on, and one use of the programmer at a time.
``busy()`` is what the dashboard uses to grey out its buttons.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from pathlib import Path

from . import boards, core, installer, registry, store, vcd
from .backends.base import BackendError


def job_status(meta: dict) -> dict:
    """Status of one job; a job whose add-on cannot be reached counts as not running."""
    try:
        return registry.by_name(meta["backend"]).build_status(meta["job_id"])
    except BackendError as e:
        return {"job_id": meta["job_id"], "state": "unknown", "message": str(e), "steps": []}


def busy(statuses: dict[str, dict] | None = None) -> list[dict]:
    """Everything in progress right now, with who started it."""
    items = []
    for lock in store.locks():
        items.append({"kind": lock["name"], "what": lock["what"], "by": lock["by"], "since": lock["since"]})
    for req in store.list_requests():
        if req["state"] in ("installing", "removing"):
            items.append({"kind": "install", "what": req["title"], "by": "user", "since": req["decided"],
                          "progress": req.get("progress")})
    for meta in store.list_jobs():
        st = (statuses or {}).get(meta["job_id"]) or job_status(meta)
        if st.get("state") == "running":
            items.append({"kind": "build", "backend": meta["backend"], "job_id": meta["job_id"],
                          "what": f"build of {Path(meta['project_dir']).name} ({meta['top']})",
                          "by": meta.get("by", "agent"), "since": meta["started"]})
    return items


def _who(by: str) -> str:
    return "the agent" if by == "agent" else "you, from the dashboard"


def start_build(project_dir: str, top: str, part: str | None, sources: list[str] | None,
                constraints: str | None, backend: str | None, by: str) -> dict:
    if not part:
        board = boards.get(store.selected_board() or "")
        if not board:
            raise BackendError("No part given and no board selected. Pass part or call select_board.")
        part = board["part"]
    chosen = registry.for_part(part, backend)
    for item in busy():
        if item["kind"] == "build" and item["backend"] == chosen.name:
            raise BackendError(f"A build is already running on '{chosen.name}' (job {item['job_id']}, "
                               f"started by {_who(item['by'])}). Wait for it; poll build_status.")
    result = chosen.build_start(Path(project_dir), top, part, sources, constraints)
    store.update_job(result["job_id"], by=by, args={
        "project_dir": str(Path(project_dir).expanduser().resolve()), "top": top, "part": part,
        "sources": sources, "constraints": constraints, "backend": chosen.name})
    return {"backend": chosen.name, **result}


def rebuild(job_id: str, by: str) -> dict:
    meta = store.get_job(job_id)
    if not meta or "args" not in meta:
        raise BackendError("This build cannot be repeated: its settings were not recorded.")
    return start_build(by=by, **meta["args"])


APPROVAL_SECONDS = 600     # an approved load must be used within ten minutes, once


def _program_subject(bit: Path, cable: str | None, board: str | None, write_flash: bool) -> str:
    """What an approval is for: this exact file content, this target, SRAM or flash."""
    digest = hashlib.sha256(bit.read_bytes()).hexdigest()[:16]
    return "|".join([str(bit), digest, board or "", cable or "", "flash" if write_flash else "sram"])


def _program_gate(bitstream: str, cable: str | None, board: str | None, part: str | None,
                  write_flash: bool, wait_seconds: int) -> dict | None:
    """The agent may not write to hardware by itself: every load needs the user's approval.

    Returns None when an approval for exactly this load is ready to be used (it is then marked as
    in use); otherwise returns what the agent should be told (waiting, or rejected).
    """
    dry = core.program(bitstream, cable, board, part, write_flash, False)      # checks everything first
    bit = Path(bitstream).expanduser().resolve()
    subject = _program_subject(bit, cable, board, write_flash)
    target = board or cable or "the board"
    deadline = time.time() + max(0, min(wait_seconds, 50))
    while True:
        req = next((r for r in store.list_requests() if r["kind"] == "program_board" and r["subject"] == subject), None)
        if req and req["state"] in ("approved", "loading", "rejected") and time.time() - (req["decided"] or 0) > APPROVAL_SECONDS:
            req = None                                       # an old decision (or a load that died): ask again
        if req is None or req["state"] in ("done", "failed"):
            flash = "FLASH of" if write_flash else "SRAM of"
            req = store.new_request(
                "program_board", subject, f"Load {bit.name} to {target} ({'flash, PERMANENT' if write_flash else 'SRAM, temporary'})",
                {"what": f"Write {bit.name} to the {flash} {target}. {dry['would_run']}", "source": str(bit),
                 "disk": "", "licence": "", "automatic": False, "permanent": write_flash},
                "The agent wants to write to your board.")
        if req["state"] == "approved":
            store.update_request(req["id"], state="loading")
            return None
        if req["state"] == "rejected":
            return {"ok": False, "state": "rejected", "request_id": req["id"],
                    "note": "The user rejected this load. Do not retry or work around it; ask them how to proceed."}
        if req["state"] != "pending":                        # another call is loading it right now
            return {"ok": False, "state": req["state"], "request_id": req["id"],
                    "note": "This load is already in progress."}
        if time.time() >= deadline:
            return {"ok": False, "state": "pending_approval", "request_id": req["id"], "target": dry["target"],
                    "note": "Nothing was written. The user must approve this load first (dashboard, or "
                            "`brigse approvals` in a terminal). Tell them, then call program again with the "
                            "same arguments and wait_seconds=50."}
        time.sleep(2)


def program(bitstream: str, cable: str | None, board: str | None, part: str | None,
            write_flash: bool, confirm: bool, by: str, wait_seconds: int = 0) -> dict:
    if not confirm:
        return core.program(bitstream, cable, board, part, write_flash, confirm)
    if by != "agent":                      # the user's own click in the dashboard is the approval
        return _program_now(bitstream, cable, board, part, write_flash, by)
    held = _program_gate(bitstream, cable, board, part, write_flash, wait_seconds)
    if held is not None:
        return held
    subject = _program_subject(Path(bitstream).expanduser().resolve(), cable, board, write_flash)
    req = next(r for r in store.list_requests() if r["kind"] == "program_board" and r["subject"] == subject)
    try:
        result = _program_now(bitstream, cable, board, part, write_flash, by)
    except BackendError:
        store.update_request(req["id"], state="approved")    # the approval was not used up
        raise
    store.update_request(req["id"], state="done" if result.get("ok") else "failed",
                         result=(result.get("output") or "")[-300:])
    return {**result, "request_id": req["id"]}


def _program_now(bitstream: str, cable: str | None, board: str | None, part: str | None,
                 write_flash: bool, by: str) -> dict:
    target = board or cable or "the board"
    with store.lock("program", by, f"loading {Path(bitstream).name} to {target}") as holder:
        if holder:
            raise BackendError(f"The programmer is in use ({holder['what']}, started by "
                               f"{_who(holder['by'])}). Try again in a moment.")
        result = core.program(bitstream, cable, board, part, write_flash, True)
    store.set_state(last_program={"ok": result.get("ok"), "file": Path(bitstream).name, "target": target,
                                  "flash": write_flash, "by": by, "at": time.time(),
                                  "output": (result.get("output") or "")[-300:]})
    return result


def board_for_part(part: str) -> dict | None:
    return next((b for b in boards.BOARDS if b["part"].lower() == part.lower() and b["loader_board"]), None)


def load_job(job_id: str, by: str) -> dict:
    """Load a finished build's bitstream into the board's SRAM (never flash) in the background."""
    meta = store.get_job(job_id)
    if not meta:
        raise BackendError(f"Unknown job '{job_id}'.")
    st = job_status(meta)
    bit = (st.get("report") or {}).get("bitstream")
    if st.get("state") != "done" or not bit:
        raise BackendError("This build has no bitstream to load.")
    board = board_for_part(meta["part"])
    if not board:
        raise BackendError(f"No supported programmer is known for part {meta['part']}.")
    if store.locks():
        raise BackendError("The programmer is already in use.")

    def run():
        try:
            program(bit["path"], None, board["loader_board"], None, False, True, by)
        except BackendError as e:
            store.set_state(last_program={"ok": False, "file": Path(bit["path"]).name,
                                          "target": board["loader_board"], "flash": False, "by": by,
                                          "at": time.time(), "output": str(e)})
    threading.Thread(target=run, daemon=True).start()
    return {"started": True, "target": board["loader_board"], "memory": "SRAM (temporary)"}


def install_now(backend: str) -> dict:
    """Install started by the user from the dashboard: the click is the approval."""
    b = registry.by_name(backend)
    if b.status()["installed"]:
        raise BackendError("This add-on is already installed.")
    plan = b.install_plan()
    if not plan.get("automatic"):
        raise BackendError("This add-on has no automatic installer. " + b.status().get("install_hint", ""))
    req = store.new_request("install_backend", backend, f"Install add-on: {b.title}", plan,
                            "Started by you from the dashboard.")
    store.decide_request(req["id"], approve=True)
    installer.start(req["id"])
    return {"request_id": req["id"], "state": "installing"}


def _in_use(backend: str) -> None:
    for item in busy():
        if item["kind"] == "build" and item.get("backend") == backend:
            raise BackendError(f"A build is running on '{backend}'; it cannot be removed now.")


def uninstall_request(backend: str, reason: str) -> dict:
    """Asked by the agent: creates a request the user must approve in the dashboard."""
    b = registry.by_name(backend)
    _in_use(backend)
    return store.new_request("uninstall_backend", backend, f"Uninstall add-on: {b.title}",
                             b.uninstall_plan(), reason)


def uninstall_now(backend: str) -> dict:
    """Uninstall started by the user from the dashboard: the click is the approval."""
    req = uninstall_request(backend, "Started by you from the dashboard.")
    store.decide_request(req["id"], approve=True)
    installer.start(req["id"])
    return {"request_id": req["id"], "state": "removing"}


def simulate(sources: list[str], top: str | None, cwd: str | None, timeout_s: int, waves: bool, by: str) -> dict:
    """Run a simulation and keep its recording, so the dashboard and the agent can both look at it."""
    sim_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    folder = store.SIMS / sim_id
    result = core.simulate(sources, top, cwd, timeout_s, folder / "files" if waves else None)
    meta = {"id": sim_id, "started": time.time(), "by": by, "sources": sources, "top": result.get("top") or top,
            "cwd": str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd()),
            "compiled": result["compiled"], "passed": result["passed"],
            "output": (result.get("output") or "")[-1500:], "note": result.get("waves_note"),
            "vcd": result.get("vcd")}
    if result.get("vcd"):
        recording = vcd.parse(Path(result["vcd"]))
        names = [s["name"] for s in recording.signals]
        meta.update(signals=len(names), end_time=recording.end_time, timescale=recording.timescale)
        result["waves"] = {"sim_id": sim_id, "signals": names[:40], "more_signals": max(0, len(names) - 40),
                           "end_time": recording.end_time, "timescale": recording.timescale,
                           "how_to_read": "Call the waves tool with this sim_id to see the values over time."}
        result.pop("vcd")
    store.save_sim(sim_id, meta)
    store.prune_sims()
    result["sim_id"] = sim_id
    return result


def waves(sim_id: str, signals: list[str] | None, start: int, end: int | None, max_rows: int,
          sample_on: str | None = None) -> dict:
    meta = store.get_sim(sim_id)
    if not meta or not meta.get("vcd") or not Path(meta["vcd"]).is_file():
        raise BackendError(f"No recorded waveform for simulation '{sim_id}'.")
    return vcd.table(vcd.parse(Path(meta["vcd"])), signals, start, end, max(1, min(max_rows, 200)), sample_on)
