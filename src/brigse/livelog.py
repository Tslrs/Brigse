"""A running build's log as a stream of events, for the dashboard's live log (Server-Sent Events).

Builds that run on this machine write one log file per step: those are read as they grow.
A build that runs elsewhere (the ISE virtual machine) has no local file until it finishes; its
status already carries the last lines of the current step, so those are sent when they change.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

from . import actions

CHUNK = 65536          # most bytes sent in one event; the rest follows on the next pass


def events(meta: dict, local_poll: float = 1.0, remote_poll: float = 4.0, max_seconds: float = 7200) -> Iterator[tuple[str, dict]]:
    """Yield ('log', {step, text}), ('state', {...}) and finally ('end', {state}) for one job."""
    job_dir = Path(meta["job_dir"])
    offsets: dict[str, int] = {}
    last_tail = None
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        st = actions.job_status(meta)             # read before the files: what the files hold then is complete if it is over
        local = False
        for step in meta.get("step_order", []):
            f = job_dir / f"{step}.log"
            if not f.is_file():
                continue
            local = True
            size, off = f.stat().st_size, offsets.get(step, 0)
            if size > off:
                with f.open("rb") as fh:
                    fh.seek(off)
                    data = fh.read(min(size - off, CHUNK))
                offsets[step] = off + len(data)
                yield "log", {"step": step, "text": data.decode(errors="replace")}
        tail = st.get("log_tail")
        if not local and tail and tail != last_tail:
            last_tail = tail
            yield "log", {"step": st.get("current_step"), "text": "\n".join(tail) + "\n", "replace": True}
        more = any((job_dir / f"{s}.log").is_file() and (job_dir / f"{s}.log").stat().st_size > offsets.get(s, 0)
                   for s in meta.get("step_order", []))
        yield "state", {"state": st.get("state"), "current_step": st.get("current_step"),
                        "elapsed_seconds": st.get("elapsed_seconds")}
        if st.get("state") != "running" and not more:
            yield "end", {"state": st.get("state")}
            return
        if not more:
            time.sleep(local_poll if local else remote_poll)
    yield "end", {"state": "unknown", "note": "stopped after a very long time"}
