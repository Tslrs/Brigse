"""Read a VCD file (the signal recording a simulator writes) and put it into two shapes:

* a compact structure for the dashboard's wave viewer (``to_json``);
* a plain-text table of value changes for an AI agent, which cannot look at a picture (``table``).

Only what Icarus Verilog writes is handled: scalar and vector values, x/z, aliases. Real-number
signals and parameters are skipped.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_CHANGES_PER_SIGNAL = 4000
MAX_TOTAL_CHANGES = 60000


class Vcd:
    def __init__(self):
        self.timescale = "1ns"
        self.end_time = 0
        self.signals: list[dict] = []          # {name, width, id}
        self.changes: dict[int, list] = {}     # signal index -> [(time, value string)]
        self.truncated = False


def _pad(value: str, width: int) -> str:
    """VCD drops leading bits: a short value is padded with 0, or with x/z when it starts with one."""
    if len(value) >= width:
        return value[-width:]
    return (value[0] if value[0] in "xXzZ" else "0") * (width - len(value)) + value


def parse(path: Path) -> Vcd:
    vcd = Vcd()
    scope: list[str] = []
    by_id: dict[str, list[int]] = {}
    now, total, in_defs = 0, 0, True
    text = Path(path).read_text(errors="replace")
    tokens = iter(text.split())
    for tok in tokens:
        if in_defs:
            if tok == "$timescale":
                body = []
                for t in tokens:
                    if t == "$end":
                        break
                    body.append(t)
                vcd.timescale = "".join(body)
            elif tok == "$scope":
                kind, name = next(tokens), next(tokens)
                scope.append(name)
                next(tokens)
            elif tok == "$upscope":
                if scope:
                    scope.pop()
                next(tokens)
            elif tok == "$var":
                vtype, width, ident = next(tokens), int(next(tokens)), next(tokens)
                name = next(tokens)
                for t in tokens:
                    if t == "$end":
                        break
                if vtype not in ("real", "realtime", "event", "parameter"):
                    vcd.signals.append({"name": ".".join(scope + [name]), "width": width, "id": ident})
                    by_id.setdefault(ident, []).append(len(vcd.signals) - 1)
            elif tok == "$enddefinitions":
                next(tokens)
                in_defs = False
            continue
        if tok.startswith("#"):
            now = int(tok[1:])
            vcd.end_time = max(vcd.end_time, now)
        elif tok[0] in "bB" and len(tok) > 1:
            value, ident = tok[1:], next(tokens, None)
            _record(vcd, by_id, ident, now, value)
        elif tok[0] in "rR":
            next(tokens, None)
        elif tok[0] in "01xXzZ" and len(tok) > 1:
            _record(vcd, by_id, tok[1:], now, tok[0].lower())
        total += 1
        if total > 5_000_000:
            vcd.truncated = True
            break
    return vcd


def _record(vcd: Vcd, by_id: dict, ident: str | None, time: int, value: str) -> None:
    for index in by_id.get(ident or "", ()):
        rows = vcd.changes.setdefault(index, [])
        value_here = _pad(value.lower(), vcd.signals[index]["width"])
        if rows and rows[-1][1] == value_here:
            continue
        if len(rows) >= MAX_CHANGES_PER_SIGNAL:
            vcd.truncated = True
            continue
        rows.append((time, value_here))


def to_json(vcd: Vcd, max_signals: int = 80) -> dict:
    """The shape the dashboard draws. Signals keep the order of the file."""
    out, budget = [], MAX_TOTAL_CHANGES
    for i, sig in enumerate(vcd.signals[:max_signals]):
        rows = vcd.changes.get(i, [])[:budget]
        budget -= len(rows)
        out.append({"name": sig["name"], "width": sig["width"], "changes": [[t, v] for t, v in rows]})
    return {"timescale": vcd.timescale, "end": vcd.end_time, "signals": out,
            "total_signals": len(vcd.signals), "truncated": vcd.truncated or budget <= 0}


def _show(value: str) -> str:
    if len(value) == 1:
        return value
    if any(c in value for c in "xz"):
        return "b" + value
    return f"{int(value, 2):#x}"


def _value_at(vcd: Vcd, index: int, time: int) -> str | None:
    value = None
    for t, v in vcd.changes.get(index, []):
        if t > time:
            break
        value = v
    return value


def table(vcd: Vcd, names: list[str] | None = None, start: int = 0, end: int | None = None,
          max_rows: int = 40, sample_on: str | None = None) -> dict:
    """Value changes as text: one row per moment at which any chosen signal changed.

    With sample_on (a clock), one row per rising edge of that signal instead, listing the value of
    every chosen signal just after the edge. That is the natural view of a synchronous design.
    """
    chosen = [i for i, s in enumerate(vcd.signals)
              if not names or any(s["name"] == n or s["name"].endswith("." + n) for n in names)]
    chosen = chosen[:24]
    if sample_on:
        clock = next((i for i, s in enumerate(vcd.signals)
                      if s["name"] == sample_on or s["name"].endswith("." + sample_on)), None)
        if clock is None:
            return {"error": f"No signal named '{sample_on}' to sample on."}
        chosen = [i for i in chosen if i != clock]
        rises = [t for (t0, v0), (t, v) in zip(vcd.changes.get(clock, []), vcd.changes.get(clock, [])[1:])
                 if v0 == "0" and v == "1" and t >= start and (end is None or t <= end)]
        label = {i: (vcd.signals[i]["name"].split(".", 1)[-1] if "." in vcd.signals[i]["name"]
                     else vcd.signals[i]["name"]) for i in chosen}
        rows = [{"time": t, "values": {label[i]: _show(v) for i in chosen
                                        if (v := _value_at(vcd, i, t)) is not None}} for t in rises]
        shown = rows[:max_rows]
        return {"timescale": vcd.timescale, "end_time": vcd.end_time, "sampled_on": sample_on,
                "rises": len(rows), "rows": shown, "rows_not_shown": max(0, len(rows) - len(shown)),
                "note": "One row per rising edge of the clock; values are those just after the edge."}
    events: dict[int, dict[int, str]] = {}
    state: dict[int, str] = {}
    for i in chosen:
        for t, v in vcd.changes.get(i, []):
            if t < start:
                state[i] = v
            elif end is None or t <= end:
                events.setdefault(t, {})[i] = v
    label = {i: vcd.signals[i]["name"].split(".", 1)[-1] if "." in vcd.signals[i]["name"] else vcd.signals[i]["name"]
             for i in chosen}
    rows = []
    if state:
        rows.append({"time": start, "changed": {label[i]: _show(v) for i, v in state.items()}, "note": "values at the start"})
    for t in sorted(events):
        rows.append({"time": t, "changed": {label[i]: _show(v) for i, v in events[t].items()}})
    shown = rows[:max_rows]
    summary = []
    for i in chosen:
        ch = vcd.changes.get(i, [])
        summary.append({"signal": vcd.signals[i]["name"], "bits": vcd.signals[i]["width"], "changes": len(ch),
                        "first": _show(ch[0][1]) if ch else None, "last": _show(ch[-1][1]) if ch else None,
                        "constant": len({v for _, v in ch}) <= 1})
    return {"timescale": vcd.timescale, "end_time": vcd.end_time, "signals": summary, "rows": shown,
            "rows_not_shown": max(0, len(rows) - len(shown)),
            "note": "Each row lists only the signals that changed at that time; unchanged signals keep their value."}
