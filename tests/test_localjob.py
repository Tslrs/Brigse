"""A build whose folder was deleted must end as failed, not stay "running" for ever.

It used to stay running: no DONE line can arrive, and "one build at a time per add-on" then
blocked every new build. Found when an agent was blocked by a 45-minute-old ghost job.
usage: uv run python tests/test_localjob.py
"""
import json
import os
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp()
os.environ["BRIGSE_HOME"] = HOME
from brigse import store  # noqa: E402
from brigse.backends import localjob  # noqa: E402

work = Path(tempfile.mkdtemp())
job_dir = work / "build_out" / "job1"
job_dir.mkdir(parents=True)
store.JOBS.mkdir(parents=True, exist_ok=True)
(store.JOBS / "job1.json").write_text(json.dumps({
    "job_id": "job1", "backend": "gowin_eda", "project_dir": str(work), "job_dir": str(job_dir),
    "top": "top", "part": "GW1NR-LV9QN88PC6/I5", "started": time.time() - 2700, "step_order": ["gowin_eda"]}))

_, st = localjob.status("job1")
assert st["state"] == "running", st            # a folder that exists, without DONE, is still running
job_dir.rmdir()
_, st = localjob.status("job1")
assert st["state"] == "failed" and "deleted" in st["message"], st
print("LOCALJOB OK")
