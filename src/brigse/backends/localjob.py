"""Run a build as a detached local shell script and track its steps.

The script appends lines to <job_dir>/job.status:
    STEP <name> START <epoch>
    STEP <name> END <exit_code> <seconds>
    DONE <exit_code>
"""
from __future__ import annotations

import json
import secrets
import shlex
import subprocess
import time
from pathlib import Path

from .. import store
from .base import BackendError

HEADER = r"""#!/bin/bash
cd %(cwd)s
J=%(job)s
step() {
  local n=$1; shift; local s=$(date +%%s)
  echo "STEP $n START $s" >> "$J/job.status"
  "$@" > "$J/$n.log" 2>&1 < /dev/null; local rc=$?
  echo "STEP $n END $rc $(( $(date +%%s) - s ))" >> "$J/job.status"
  [ $rc -eq 0 ] || { echo "DONE $rc" >> "$J/job.status"; exit $rc; }
}
"""


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def q(value) -> str:
    return shlex.quote(str(value))


def start(backend: str, project_dir: Path, job_dir: Path, top: str, part: str,
          steps: list[tuple[str, list[str]]], env: dict | None = None) -> str:
    """steps: [(name, argv)]. Returns the job id."""
    job_id = job_dir.name
    job_dir.mkdir(parents=True, exist_ok=True)
    script = HEADER % {"cwd": q(project_dir), "job": q(job_dir)}
    script += "".join(f"step {name} {' '.join(q(a) for a in argv)}\n" for name, argv in steps)
    script += 'echo "DONE 0" >> "$J/job.status"\n'
    (job_dir / "run.sh").write_text(script)
    subprocess.Popen(["bash", str(job_dir / "run.sh")], env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    store.JOBS.mkdir(parents=True, exist_ok=True)
    (store.JOBS / f"{job_id}.json").write_text(json.dumps({
        "job_id": job_id, "backend": backend, "project_dir": str(project_dir), "job_dir": str(job_dir),
        "top": top, "part": part, "started": time.time(), "step_order": [s[0] for s in steps]}))
    return job_id


def status(job_id: str) -> tuple[dict, dict]:
    """Return (meta, status) where status has state, steps, current step and log tail."""
    meta_path = store.JOBS / f"{job_id}.json"
    if not meta_path.is_file():
        raise BackendError(f"Unknown job '{job_id}'.")
    meta = json.loads(meta_path.read_text())
    job_dir = Path(meta["job_dir"])
    if not job_dir.is_dir():
        # Someone deleted the build folder: no DONE line can ever arrive, so do not report "running"
        # for ever (that would also block every new build on this add-on).
        return meta, {"job_id": job_id, "steps": [], "step_order": meta["step_order"], "state": "failed",
                      "elapsed_seconds": int(time.time() - meta["started"]), "outputs_dir": str(job_dir),
                      "message": "The build folder was deleted, so this build cannot finish. Build again."}
    steps, current, done_rc = [], None, None
    status_file = job_dir / "job.status"
    for line in status_file.read_text().splitlines() if status_file.is_file() else []:
        parts = line.split()
        if parts[:1] == ["STEP"] and parts[2] == "START":
            current = {"step": parts[1], "started": int(parts[3])}
        elif parts[:1] == ["STEP"] and parts[2] == "END":
            steps.append({"step": parts[1], "exit_code": int(parts[3]), "seconds": int(parts[4])})
            current = None
        elif parts[:1] == ["DONE"]:
            done_rc = int(parts[1])

    def tail(name: str, n: int) -> list[str]:
        f = job_dir / f"{name}.log"
        return [l[:200] for l in f.read_text(errors="replace").splitlines()[-n:]] if f.is_file() else []

    st: dict = {"job_id": job_id, "steps": steps, "step_order": meta["step_order"],
                "elapsed_seconds": int(time.time() - meta["started"]), "outputs_dir": str(job_dir)}
    if done_rc is None:
        st["state"] = "running"
        if current:
            st.update(current_step=current["step"],
                      current_step_seconds=int(time.time()) - current["started"],
                      log_tail=tail(current["step"], 4))
        finished = {s["step"] for s in steps} | {(current or {}).get("step")}
        st["remaining_steps"] = [s for s in meta["step_order"] if s not in finished]
    else:
        st["state"] = "done" if done_rc == 0 else "failed"
        if done_rc != 0 and steps:
            st["error_tail"] = tail(steps[-1]["step"], 12)
    return meta, st
