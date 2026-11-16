"""The terminal view of Brigse: `brigse status [--watch]` and `brigse approvals`.

Shows what the dashboard shows, as text, so it works inside any terminal (Ghostty, iTerm, ssh).
Approving needs a real terminal and a typed answer: a script or an agent's pipe cannot do it.
"""
from __future__ import annotations

import shutil
import sys
import textwrap
import time

from . import dashboard, installer, store

COLOR = sys.stdout.isatty()


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


ok, bad, warn, dim, bold = (lambda t: c("32", t)), (lambda t: c("31", t)), (lambda t: c("33", t)), (lambda t: c("2", t)), (lambda t: c("1", t))


def when(t: float | None) -> str:
    if not t:
        return ""
    lt, now = time.localtime(t), time.localtime()
    return ("today " if lt[:3] == now[:3] else time.strftime("%Y-%m-%d ", lt)) + time.strftime("%H:%M", lt)


def _timing(j: dict) -> str:
    t = (j.get("report") or {}).get("timing")
    if not t:
        return ""
    clk = (t.get("clocks") or [{}])[0]
    mhz = f", {clk['fmax_mhz']} MHz max" if clk.get("fmax_mhz") else ""
    return (ok("timing met") if t.get("met") else bad("timing NOT met")) + mhz


def _state(s: str) -> str:
    return {"done": ok, "failed": bad, "running": warn}.get(s, dim)(s)


def render(d: dict, width: int = 100) -> str:
    out = [bold("Brigse") + dim("  Bridge to Semiconductor")]
    if d["busy"]:
        for b in d["busy"]:
            out.append(warn("● Working: ") + str(b["what"]))
    else:
        out.append(ok("● Ready.") + " Nothing is running.")

    pend = [r for r in d["requests"] if r["state"] == "pending"]
    if pend:
        out += ["", bold(warn(f"The agent is asking ({len(pend)})")) + dim("   answer with: brigse approvals")]
        out += [f"  {r['title']}" for r in pend]

    sel = next((b for b in d["boards"] if b["id"] == d["selected_board"]), None)
    out += ["", bold("BOARD")]
    out.append(f"  {sel['name']}  {dim(sel['part'])}  " + (ok("ready to build") if sel["backend_ready"] else warn("add-on missing"))
               if sel else "  none chosen")

    out += ["", bold("BUILDS")]
    if not d["jobs"]:
        out.append(dim("  No builds yet."))
    for j in d["jobs"][:6]:
        step = f" ({j['current_step']}, {j['current_step_seconds']} s)" if j["state"] == "running" and j.get("current_step") else ""
        line = f"  {when(j['started']):<17} {j['project']:<22} {_state(j['state'])}{step}  {_timing(j)}"
        out.append(line + (dim(f"  by {j['by']}") if j["by"] != "agent" else ""))
        for p in ((j.get("diagnosis") or {}).get("problems") or [])[:2]:
            out.append(warn(f"      ! {p['title']}"))
    if len(d["jobs"]) > 6:
        out.append(dim(f"  ... and {len(d['jobs']) - 6} earlier"))

    out += ["", bold("SIMULATIONS")]
    if not d["sims"]:
        out.append(dim("  None yet."))
    for s in d["sims"]:
        res = ok("passed") if s["passed"] else bad("failed" if s["compiled"] else "did not compile")
        out.append(f"  {when(s['started']):<17} {str(s['top']):<22} {res}")

    out += ["", bold("ADD-ONS")]
    for b in d["backends"]:
        if not b["implemented"]:
            state = dim("planned")
        elif b["installed"] and b.get("usable") is False:
            state = bad("not usable")
        elif b["installed"]:
            state = ok("installed")
        else:
            state = warn("not installed")
        out.append(f"  {b['title'][:width - 22]:<{min(60, width - 22)}} {state}")

    p = d.get("last_program")
    if p:
        out += ["", dim(f"Last load: {p.get('file')} to {p.get('target')} at {when(p.get('at'))}")]
    return "\n".join(out)


def status(argv: list[str]) -> int:
    if "--watch" not in argv and "-w" not in argv:
        print(render(dashboard.snapshot(), shutil.get_terminal_size().columns))
        return 0
    try:
        while True:
            dashboard._cache["data"] = None
            text = render(dashboard.snapshot(), shutil.get_terminal_size().columns)
            sys.stdout.write("\033[H\033[2J" + text + "\n\n" + dim("Updates every 3 seconds. Ctrl-C to leave.") + "\n")
            sys.stdout.flush()
            time.sleep(3)
    except KeyboardInterrupt:
        return 0


def _field(label: str, text: str, indent: int = 10) -> str:
    """One labelled line; long text wraps at word ends and lines up under the first word."""
    width = max(40, shutil.get_terminal_size().columns - 2)
    return textwrap.fill(text, width=width, initial_indent="  " + f"{label:<{indent - 2}}",
                         subsequent_indent=" " * indent, break_long_words=False)


def approvals(argv: list[str]) -> int:
    pend = [r for r in store.list_requests() if r["state"] == "pending"]
    if not pend:
        print("The agent is not asking for anything.")
        return 0
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(f"{len(pend)} request(s) waiting. Approving needs a real terminal window and a typed answer, "
              "so run `brigse approvals` yourself (or use the dashboard).")
        for r in pend:
            print(f"  - {r['title']}")
        return 3
    for r in pend:
        plan = r["plan"]
        print("\n" + bold(r["title"]))
        for label, text in (("What", plan.get("what")), ("From", plan.get("source")), ("Disk", plan.get("disk")),
                            ("Licence", plan.get("licence")), ("Why", r.get("reason"))):
            if text:
                print(_field(label, str(text)))
        note = ("Approve deletes the folder shown above." if r["kind"] == "uninstall_backend"
                else "Approve starts the download and install." if plan.get("automatic")
                else "Approve only records your decision; nothing is downloaded.")
        print(dim(textwrap.fill(note, width=max(40, shutil.get_terminal_size().columns - 2),
                                initial_indent="  ", subsequent_indent="  ")))
        while True:
            answer = input("  approve, reject or skip? [approve/reject/skip] ").strip().lower()
            if answer in ("approve", "reject", "skip"):
                break
            print("  Please type the whole word: approve, reject or skip.")
        if answer == "skip":
            continue
        req = store.decide_request(r["id"], approve=answer == "approve")
        if req and req["state"] == "approved":
            installer.start(req["id"])
        print("  " + (ok("Approved.") if answer == "approve" else warn("Rejected.")))
    return 0


def run(command: str, argv: list[str]) -> int:
    return {"status": status, "approvals": approvals}[command](argv)
