"""Turn the raw logs of a failed or doubtful build into a short list of problems.

Every rule below was written from text that the real tools printed while a design
was broken on purpose (the samples are in tests/fixtures). A message that no rule
knows is still reported, in the tool's own words, marked "unexplained": Brigse
never invents a cause it has not seen.

Besides the logs it checks two things the tools miss:

* the open-source Gowin flow ignores a constraint for a name that is not in the
  design, and builds a design whose signal name was mistyped (only a warning);
* the Gowin IDE silently puts a port that has no pin constraint on a pin of its own.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

MAX_PROBLEMS = 6
TOOL_DIRS = ("/share/yosys/", "/oss-cad-suite/", "/Gowin_EDA/", "/ISE_DS/")
LOGS = {
    "ise": ["xst.log", "ngdbuild.log", "map.log", "par.log", "trce.log", "bitgen.log"],
    "oss": ["yosys.log", "nextpnr.log", "pack.log"],
    "eda": ["gowin_eda.log"],
}
KIND_OF_BACKEND = {"ise_spartan3": "ise", "gowin_oss": "oss", "gowin_eda": "eda"}

# kind "error": stops the build. "silent": the build goes on and produces wrong hardware.
# "consequence": follows from an earlier problem and is hidden.
RULES = [
    dict(id="syntax", kind="error", patterns=[
        r"^unexpected token: ", r"^expecting '.*', found", r"^syntax error, unexpected",
        r"^Syntax error near", r"^Verilog 2001 keyword"],
         title="Syntax error",
         explain="The compiler gave up at this line. The real mistake is usually just before it: a "
                 "missing ';' (or ')' or 'end') at the end of the previous statement.",
         fix="Look at the end of the line above this one, and at its brackets."),
    dict(id="unknown_module", kind="error", patterns=[
        r"Could not find module/primitive '(?P<name>[^']+)'",
        r"Module `\\(?P<name>\w+)' referenced .* is not part of the design",
        r"Instantiating unknown module '(?P<name>[^']+)'"],
         title="Module '{name}' is not defined",
         explain="The design uses a module called '{name}', but no source file defines it.",
         fix="Add the file that contains it to `sources` (or to the project folder), or fix the "
             "spelling. A vendor primitive or IP core may simply not exist in this add-on."),
    dict(id="undeclared", kind="error", patterns=[
        r"'(?P<name>\w+)' has not been declared", r"'(?P<name>\w+)' is not declared"],
         title="Signal '{name}' is not declared",
         explain="'{name}' is used but never declared. This is most often a typo in a signal name.",
         fix="Compare the spelling with the declarations (case matters in Verilog) and the module's ports."),
    dict(id="silent_undeclared", kind="silent", patterns=[
        r"Identifier `\\(?P<name>\w+)' is implicitly declared",
        r"Range select .* out of bounds on signal `\\(?P<name>\w+)'"],
         title="Signal '{name}' was never declared, yet the build went on",
         explain="Verilog quietly created an undriven wire named '{name}', so whatever uses it is wrong "
                 "(typically undefined outputs). This is almost always a typo, and a bitstream is "
                 "still produced, so nothing else will warn you.",
         fix="Fix the spelling. Putting `default_nettype none` at the top of the file turns this "
             "mistake into an error."),
    dict(id="pin_not_in_package", kind="error", patterns=[
        r"^Pin (?P<pin>\S+) not found", r"Can't find pad location '(?P<pin>[^']+)'"],
         title="Pin {pin} does not exist",
         explain="The constraints file puts a signal on pin {pin}, and this package has no such pin.",
         fix="Check the pin number against the board's schematic or pin table."),
    dict(id="constraint_no_object", kind="error", patterns=[
        r"NET \"(?P<name>[^\"]+)\" not found", r"Can't find object named '(?P<name>[^']+)'"],
         title="A constraint for '{name}' matches nothing",
         explain="The constraints file mentions '{name}', but the design has no port or net with that name.",
         fix="Check the spelling and, for a bus, the index (led[0], led[1], ...). Also check that the "
             "signal still exists in the code."),
    dict(id="pin_collision", kind="error", patterns=[
        r"Can't place \S+ at \S+ because it's already taken by", r"cannot be placed according to constraint, "
        r"for the location has already been used", r"^The constrained location has already been used"],
         title="Two signals share one pin",
         explain="Two ports were constrained to the same pin, and a pin can carry only one signal.",
         fix="Give every port its own pin in the constraints file."),
    dict(id="unconstrained_io", kind="error", patterns=[r"^Unconstrained IO:\s*(?P<name>\S+)"],
         title="A port has no pin constraint",
         explain="One bit of port '{name}' has no pin in the constraints file. The open-source placer "
                 "refuses to choose a pin on its own.",
         fix="Add a pin for every bit of every port of the top module."),
    dict(id="after_error", kind="consequence", patterns=[
        r"^failed to parse CST file", r"ignored due to previous errors", r"remains a black box",
        r"ignored due to errors"], title="", explain="", fix=""),
]
for _r in RULES:
    _r["compiled"] = [re.compile(p) for p in _r["patterns"]]

ISE_LINE = re.compile(r"^(ERROR|WARNING):(\w+)(?::(\d+))? - (.*)$")
OSS_LINE = re.compile(r"^(?:(?P<file>[^\s:]+\.\w+):(?P<line>\d+): )?(?P<sev>ERROR|Warning|WARNING): (?P<msg>.*)$")
EDA_LINE = re.compile(r"^(ERROR|WARN)\s*\((\w+)\)\s*:\s*(.*)$")


def _parse_ise(text: str) -> list[dict]:
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        m = ISE_LINE.match(line)
        if not m:
            continue
        msg = m.group(4)
        for follow in lines[i + 1:]:
            if follow.startswith("   ") and follow.strip():
                msg += " " + follow.strip()
            else:
                break
        file = lnum = None
        loc = re.match(r'"([^"]+)" line (\d+)\s*(.*)', msg)
        if loc:
            file, lnum, msg = loc.group(1), int(loc.group(2)), loc.group(3)
        else:
            loc = re.search(r"\[([^\]]+?)\((\d+)\)\]", msg)
            if loc:
                file, lnum = loc.group(1), int(loc.group(2))
        out.append(dict(severity="error" if m.group(1) == "ERROR" else "warning",
                        code=f"{m.group(2)}:{m.group(3)}" if m.group(3) else m.group(2),
                        msg=msg, file=file, line=lnum))
    return out


def _parse_oss(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = OSS_LINE.match(line)
        if m:
            out.append(dict(severity="error" if m.group("sev") == "ERROR" else "warning", code=None,
                            msg=m.group("msg"), file=m.group("file"),
                            line=int(m.group("line")) if m.group("line") else None))
    return out


def _parse_eda(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = EDA_LINE.match(line)
        if not m:
            continue
        body, file, lnum = m.group(3), None, None
        a = re.match(r'^"([^"]+)":(\d+)\s*\|\s*(.*)$', body)
        b = re.match(r'^(.*)\("([^"]+)":(\d+)\)$', body)
        if a:
            file, lnum, body = a.group(1), int(a.group(2)), a.group(3)
        elif b:
            body, file, lnum = b.group(1), b.group(2), int(b.group(3))
        out.append(dict(severity="error" if m.group(1) == "ERROR" else "warning", code=m.group(2),
                        msg=body, file=file, line=lnum))
    return out


PARSERS = {"ise": _parse_ise, "oss": _parse_oss, "eda": _parse_eda}


def _rule_for(msg: str):
    for rule in RULES:
        for pattern in rule["compiled"]:
            m = pattern.search(msg)
            if m:
                return rule, {k: v for k, v in m.groupdict().items() if v}
    return None, {}


def _where(issue: dict) -> str | None:
    if not issue.get("file"):
        return None
    base = Path(issue["file"]).name
    return f"{base}:{issue['line']}" if issue.get("line") else base


def _from_logs(kind: str, out_dir: Path) -> list[dict]:
    problems, seen, consequences = [], set(), 0
    for name in LOGS[kind]:
        path = out_dir / name
        if not path.is_file():
            continue
        for issue in PARSERS[kind](path.read_text(errors="replace")):
            if issue["severity"] == "warning" and any(d in (issue.get("file") or "") for d in TOOL_DIRS):
                continue               # a warning inside the tool's own library is not about your design
            rule, groups = _rule_for(issue["msg"])
            if rule is None:
                if issue["severity"] != "error":
                    continue           # unknown warnings are only counted, never shown
                rule = dict(id="unexplained", kind="unexplained", title=issue["msg"][:90],
                            explain=None, fix=None)
            if rule["kind"] == "consequence":
                consequences += 1
                continue
            key = (rule["id"], issue.get("file"), issue.get("line"), groups.get("name"), groups.get("pin"))
            if key in seen:
                continue
            seen.add(key)
            if "name" in groups:
                groups["name"] = re.sub(r"_(?:OBUF|IBUF|IOBUF)(?:_[IO]+)*(?:_\d+)?$", "", groups["name"])
            problems.append(dict(
                kind=rule["kind"], rule=rule["id"], tool=name.split(".")[0], code=issue["code"],
                title=rule["title"].format_map(_Defaults(groups)),
                where=_where(issue),
                explain=rule["explain"].format_map(_Defaults(groups)) if rule["explain"] else None,
                fix=rule["fix"], raw=issue["msg"][:300]))
    return problems


class _Defaults(dict):
    def __missing__(self, key):
        return "?"


# ---- checks the tools do not make ------------------------------------------------------------
def _cst_names(project: Path) -> tuple[set[str], set[str]] | None:
    """(names that have a pin, all names mentioned) from the single .cst file, if there is one."""
    files = sorted(project.glob("*.cst"))
    if len(files) != 1:
        return None
    text = files[0].read_text(errors="replace")
    pinned = set(re.findall(r'IO_LOC\s+"([^"]+)"', text))
    mentioned = pinned | set(re.findall(r'IO_PORT\s+"([^"]+)"', text))
    return pinned, mentioned


def _bits(name: str, width: int) -> list[str]:
    return [name] if width == 1 else [f"{name}[{i}]" for i in range(width)]


def _lint_oss(out_dir: Path, project: Path) -> list[dict]:
    """Compare the constraint names with the ports of the synthesised design."""
    cst = _cst_names(project)
    netlists = [p for p in out_dir.glob("*.json") if not p.name.endswith("_pnr.json")]
    if cst is None or not netlists:
        return []
    top = netlists[0].stem
    try:
        ports = json.loads(netlists[0].read_text())["modules"][top]["ports"]
    except (KeyError, ValueError):
        return []
    pinned, mentioned = cst
    known = {b for n, p in ports.items() for b in _bits(n, len(p["bits"]))}
    out = []
    for name in sorted(known - pinned):
        out.append(dict(kind="error", rule="lint_unpinned_port", tool="brigse", code=None,
                        title=f"Port '{name}' has no pin constraint", where=None,
                        explain=f"The constraints file gives no pin for '{name}'. The open-source placer "
                                "refuses to choose one on its own (Gowin EDA would pick a pin silently).",
                        fix="Add an IO_LOC line for it.", raw=""))
    for name in sorted(mentioned - known):
        out.append(dict(kind="silent", rule="lint_constraint_no_port", tool="brigse", code=None,
                        title=f"A constraint for '{name}' matches no port",
                        where=None,
                        explain=f"The constraints file mentions '{name}', but the design has no such port. "
                                "The open-source tools ignore it without a word; Gowin EDA treats the same "
                                "line as an error.",
                        fix="Check the spelling and the bus index, or remove the line.", raw=""))
    return out


def _lint_eda(out_dir: Path, project: Path) -> list[dict]:
    """Find ports that the Gowin IDE pinned on its own because the constraints do not mention them."""
    cst = _cst_names(project)
    reports = sorted((out_dir / "impl" / "pnr").glob("*.pin.html"))
    if cst is None or not reports:
        return []
    text = html.unescape(re.sub(r"<[^>]+>", " ", reports[0].read_text(errors="replace")))
    ports = set(re.findall(r"([A-Za-z_][\w\.]*(?:\[\d+\])?)\s+-\s+\d+/\d+", text))
    pinned, _ = cst
    out = []
    for name in sorted(ports - pinned):
        out.append(dict(kind="silent", rule="lint_unpinned_port", tool="brigse", code=None,
                        title=f"Port '{name}' has no pin constraint",
                        where=None,
                        explain=f"The constraints file gives no pin for '{name}'. Gowin EDA did not "
                                "stop: it chose a pin by itself, which is almost never the one on your "
                                "board, so the signal will not appear where you expect.",
                        fix="Add an IO_LOC line for it.", raw=""))
    return out


# ---- timing ---------------------------------------------------------------------------------
def _worst_path(twr: str) -> dict | None:
    m = re.search(r"Slack \(setup path\):\s+(-[\d.]+)ns.*?\n((?:  .*\n)+)", twr)
    if not m:
        return None

    def field(label):
        f = re.search(rf"^\s+{label}:\s+(.*)$", m.group(2), re.M)
        return f.group(1).strip() if f else None
    delay = field("Data Path Delay")
    levels = re.search(r"Levels of Logic = (\d+)", delay or "")
    return dict(slack=float(m.group(1)), source=field("Source"), destination=field("Destination"),
                requirement=field("Requirement"), delay=(delay or "").split("ns")[0] + "ns",
                levels=int(levels.group(1)) if levels else None)


def _timing(kind: str, out_dir: Path, report: dict | None) -> list[dict]:
    t = (report or {}).get("timing") or {}
    if not t or t.get("met") is not False:
        return []
    fix = ("Put a register in the middle of the slowest path (fewer logic levels between registers), "
           "ask for a slower clock if the design does not need this speed, or use a faster speed grade. "
           "(General guidance, not specific to this design.)")
    problems = []
    if kind == "ise":
        twr = next(iter(sorted(out_dir.glob("*.twr"))), None)
        path = _worst_path(twr.read_text(errors="replace")) if twr else None
        worst = {}
        for f in t.get("failing") or []:
            if f["constraint"] not in worst or f["slack_ns"] < worst[f["constraint"]]["slack_ns"]:
                worst[f["constraint"]] = f
        for f in sorted(worst.values(), key=lambda f: f["slack_ns"])[:2]:
            explain = (f"The tools could only reach a clock period of {f.get('achievable_ns')} ns here "
                       f"(slack {f['slack_ns']} ns, {f.get('errors')} paths too slow).")
            if path and not problems:        # the report's worst path belongs to the worst constraint
                explain += (f" The slowest path runs from {path['source']} to {path['destination']}: "
                            f"{path['delay']} of delay against {path['requirement']} available"
                            + (f", through {path['levels']} levels of logic." if path["levels"] else "."))
            problems.append(dict(kind="timing", rule="timing", tool="trce", code=None,
                                 title=f"Timing not met on {f['constraint']}", where=None,
                                 explain=explain, fix=fix, raw=""))
    else:
        for c in (t.get("failing") or [])[:2]:
            explain = (f"Clock '{c['clock']}' can run at {c['fmax_mhz']} MHz, but {c['target_mhz']} MHz "
                       "was required.")
            if c["target_mhz"] == 12.0 and kind == "oss":
                explain += (" No clock constraint was given, so the placer used its default of 12 MHz; "
                            "add a clock constraint with the real frequency.")
            problems.append(dict(kind="timing", rule="timing", tool="nextpnr" if kind == "oss" else "gowin",
                                 code=None, title=f"Timing not met on clock {c['clock']}", where=None,
                                 explain=explain, fix=fix, raw=""))
    return problems


# ---- entry point ----------------------------------------------------------------------------
def analyse(backend: str, out_dir: Path, report: dict | None = None, state: str | None = None) -> dict | None:
    """Return {"verdict", "problems"} or None when there is nothing worth saying."""
    kind = KIND_OF_BACKEND.get(backend)
    out_dir = Path(out_dir)
    if kind is None or not out_dir.is_dir():
        return None
    project = out_dir.parent.parent
    problems = _from_logs(kind, out_dir)
    if state != "failed":
        problems = [p for p in problems if p["kind"] in ("silent", "timing")]
    extra = (_lint_oss(out_dir, project) if kind == "oss" else
             _lint_eda(out_dir, project) if kind == "eda" and state != "failed" else [])
    if any(p["rule"] == "lint_unpinned_port" for p in extra):     # the exact bit beats the vague buffer name
        problems = [p for p in problems if p["rule"] != "unconstrained_io"]
    problems += extra + _timing(kind, out_dir, report)
    order = {"error": 0, "unexplained": 1, "silent": 2, "timing": 3}
    problems.sort(key=lambda p: order.get(p["kind"], 9))
    if not problems:
        return None
    errors = [p for p in problems if p["kind"] in ("error", "unexplained")]
    if state == "failed":
        verdict = f"The build failed: {len(errors) or len(problems)} problem(s). Start with: {problems[0]['title']}"
    else:
        verdict = f"The build finished, but {len(problems)} thing(s) need attention before you trust it."
    return dict(verdict=verdict, problems=problems[:MAX_PROBLEMS], more=max(0, len(problems) - MAX_PROBLEMS))
