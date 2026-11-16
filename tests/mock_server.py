"""The Brigse MCP server with one extra, fake add-on ('mock', parts 'fake-*'), for protocol tests.

The fake add-on builds with plain shell steps, so the whole MCP path (tools, job tracking, one build
at a time, approvals) can be tested with no FPGA tool installed. A project whose top is 'fail' fails;
one whose top is 'slow' takes a few seconds.

usage: python tests/mock_server.py   (an MCP server on stdio, like `brigse`)
"""
from pathlib import Path

from brigse import registry, server
from brigse.backends import localjob
from brigse.backends.base import Backend, BackendError


class MockBackend(Backend):
    name = "mock"
    title = "Mock add-on (tests)"
    part_patterns = (r"fake",)
    priority = 100

    def status(self) -> dict:
        return {"installed": True, "ready": True, "usable": True, "detail": "fake, for tests"}

    def build_start(self, project_dir, top, part, sources, constraints) -> dict:
        project_dir = Path(project_dir).expanduser().resolve()
        if not project_dir.is_dir():
            raise BackendError(f"Project folder not found: {project_dir}")
        job_dir = project_dir / "build_out" / localjob.new_job_id()
        steps = [("synth", ["sleep", "3" if top == "slow" else "0"]),
                 ("pack", ["false" if top == "fail" else "true"])]
        job_id = localjob.start(self.name, project_dir, job_dir, top, part, steps)
        return {"job_id": job_id, "state": "running"}

    def build_status(self, job_id: str) -> dict:
        meta, st = localjob.status(job_id)
        if st["state"] == "done":
            st["report"] = {"bitstream": {"path": str(Path(meta["job_dir"]) / "fake.bit")}}
        return st

    def report(self, out_dir: Path) -> dict:
        raise BackendError("The mock add-on has no report.")


registry.BACKENDS.insert(0, MockBackend())

if __name__ == "__main__":
    server.main()
