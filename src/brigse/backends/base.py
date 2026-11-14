"""The contract every add-on (backend) implements.

The core never imports vendor-specific code: it only talks to this interface,
so a new FPGA family is added by dropping one module next to this file and
listing it in ``registry.py``.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path


class BackendError(RuntimeError):
    """A problem the user can act on (missing tool, bad argument, VM down)."""


class Backend(ABC):
    name: str = ""            # short id used in tool arguments
    title: str = ""           # human readable
    part_patterns: tuple[str, ...] = ()   # regexes matched against the part name
    size_hint: str = ""       # disk cost of installing this add-on
    implemented: bool = True
    auto_install: bool = False   # can be installed by the server after the user approves
    has_runtime: bool = False    # has something to start and stop (a VM)
    priority: int = 0         # when several add-ons cover a part, the usable one with the highest wins

    def usable(self) -> bool:
        st = self.status()
        return bool(st.get("usable", st.get("installed")))

    def supports(self, part: str) -> bool:
        return any(re.match(p, part, re.I) for p in self.part_patterns)

    @abstractmethod
    def status(self) -> dict:
        """Return {installed, ready, detail, install_hint}."""

    def build_start(self, project_dir: Path, top: str, part: str,
                    sources: list[str] | None, constraints: str | None) -> dict:
        raise BackendError(f"The '{self.name}' add-on cannot build yet.")

    def build_status(self, job_id: str) -> dict:
        raise BackendError(f"The '{self.name}' add-on cannot build yet.")

    def report(self, out_dir: Path) -> dict:
        raise BackendError(f"The '{self.name}' add-on has no report parser yet.")

    def control(self, action: str) -> dict:
        raise BackendError(f"The '{self.name}' add-on has nothing to start or stop.")

    def install_plan(self) -> dict:
        """What installing this add-on would download and change (shown to the user for approval)."""
        return {"what": self.title, "source": "", "disk": self.size_hint, "licence": "", "automatic": False}

    def uninstall_plan(self) -> dict:
        """What uninstalling would delete (shown to the user for approval)."""
        raise BackendError(f"The '{self.name}' add-on was not installed by Brigse, so Brigse does not remove it.")

    def uninstall(self, plan=None, progress=lambda text: None) -> str:
        """Delete what install() downloaded. Only ever called after the user approved."""
        raise BackendError(f"The '{self.name}' add-on was not installed by Brigse, so Brigse does not remove it.")

    def install(self, plan=None, progress=lambda text: None) -> str:
        """Install the add-on. Only ever called after the user approved the plan in the dashboard.

        progress: callback taking a short text shown to the user while the install runs.
        """
        raise BackendError(f"The '{self.name}' add-on has no automatic installer yet.")


class PlannedBackend(Backend):
    """Placeholder for an add-on that is designed but not written yet."""

    implemented = False

    def __init__(self, name: str, title: str, part_patterns: tuple[str, ...],
                 size_hint: str, tools: str):
        self.name, self.title = name, title
        self.part_patterns, self.size_hint, self._tools = part_patterns, size_hint, tools

    def status(self) -> dict:
        return {"installed": False, "ready": False,
                "detail": "Add-on not implemented yet.",
                "install_hint": f"Planned toolchain: {self._tools}"}

    def install_plan(self) -> dict:
        return {"what": f"{self.title}: {self._tools}",
                "source": "Open-source project releases (exact URLs fixed when the installer is written)",
                "disk": self.size_hint,
                "licence": "Open-source tools; no licence file or vendor account needed.",
                "automatic": False}
