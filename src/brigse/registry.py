"""The list of add-ons the core knows about."""
from __future__ import annotations

from .backends.base import Backend, BackendError, PlannedBackend
from .backends.gowin import GowinEdaBackend, GowinOssBackend
from .backends.ise_spartan3 import IseSpartan3Backend

BACKENDS: list[Backend] = [
    IseSpartan3Backend(),
    GowinEdaBackend(),
    GowinOssBackend(),
    PlannedBackend("xilinx7_oss", "AMD 7-series (open-source flow)", (r"xc7",),
                   "about 1-2 GB (estimate)", "yosys + nextpnr-xilinx + openFPGALoader"),
    PlannedBackend("cyclone5_oss", "Altera Cyclone V (open-source flow)", (r"5c",),
                   "about 1 GB (estimate)", "yosys + nextpnr-mistral + openFPGALoader"),
]


def by_name(name: str) -> Backend:
    for b in BACKENDS:
        if b.name == name:
            return b
    raise BackendError(f"Unknown add-on '{name}'. Known: {[b.name for b in BACKENDS]}")


def for_part(part: str, prefer: str | None = None) -> Backend:
    """Pick the add-on for a part: the one asked for, else the usable one with the highest priority."""
    candidates = [b for b in BACKENDS if b.supports(part)]
    if not candidates:
        raise BackendError(
            f"No add-on covers part '{part}'. Call list_backends to see supported families.")
    if prefer:
        chosen = by_name(prefer)
        if chosen not in candidates:
            raise BackendError(f"Add-on '{prefer}' does not cover part '{part}'. "
                               f"Candidates: {[b.name for b in candidates]}")
        return chosen
    usable = sorted((b for b in candidates if b.implemented and b.usable()),
                    key=lambda b: b.priority, reverse=True)
    if usable:
        return usable[0]
    options = "; ".join(f"{b.name}: {b.status()['detail']} {b.status().get('install_hint', '')}".strip()
                        for b in candidates)
    raise BackendError(f"No usable add-on for part '{part}'. Options: {options}")
