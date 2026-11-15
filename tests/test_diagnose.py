"""Check the error explainer against real tool output (tests/fixtures). Needs no vendor tools.

usage: uv run python tests/test_diagnose.py
"""
import shutil
import tempfile
from pathlib import Path

from brigse import diagnose, registry
from brigse.backends.base import BackendError

FX = Path(__file__).resolve().parent / "fixtures"
BACKEND = {"ise": "ise_spartan3", "oss": "gowin_oss", "eda": "gowin_eda", "good_oss": "gowin_oss",
           "good_eda": "gowin_eda", "good_oss_demo": "gowin_oss"}


def run(case: str, state: str):
    prefix = case.split("_")[0] if not case.startswith("good_") else case
    backend = BACKEND[prefix]
    out = FX / case / "build_out" / "job"
    try:
        report = registry.by_name(backend).report(out)
    except BackendError:
        report = {}
    return diagnose.analyse(backend, out, report, state)


def rules(d):
    return [p["rule"] for p in d["problems"]] if d else []


EXPECT = {
    # case: (state, rules, where shown for the first problem or None, text the title must contain)
    "ise_syntax": ("failed", ["syntax"], "blinky.v:11", "Syntax"),
    "ise_undefined_module": ("failed", ["unknown_module"], "blinky.v:11", "'foo'"),
    "ise_undeclared_signal": ("failed", ["undeclared"], "blinky.v:11", "'cnt'"),
    "ise_ucf_net_missing": ("failed", ["constraint_no_object"], "blinky.ucf:4", "'nope'"),
    "oss_syntax": ("failed", ["syntax"], "blinky.v:13", "Syntax"),
    "oss_undefined_module": ("failed", ["unknown_module"], None, "'foo'"),
    "oss_undeclared_signal": ("done", ["silent_undeclared"], "blinky.v:13", "'cnt'"),
    "oss_bad_pin_number": ("failed", ["pin_not_in_package"], None, "999"),
    "oss_net_not_in_design": ("done", ["lint_constraint_no_port"], None, "'nope'"),
    "oss_two_signals_one_pin": ("failed", ["pin_collision"], None, "share one pin"),
    "oss_missing_pin": ("failed", ["lint_unpinned_port"], None, "led[3]"),
    "eda_syntax": ("failed", ["syntax"], "blinky.v:13", "Syntax"),
    "eda_undefined_module": ("failed", ["unknown_module"], "blinky.v:13", "'foo'"),
    "eda_undeclared_signal": ("failed", ["undeclared"], "blinky.v:13", "'cnt'"),
    "eda_bad_pin_number": ("failed", ["pin_not_in_package"], "tangnano9k.cst:2", "999"),
    "eda_net_not_in_design": ("failed", ["constraint_no_object"], "tangnano9k.cst:14", "'nope'"),
    "eda_two_signals_one_pin": ("failed", ["pin_collision"], None, "share one pin"),
    "eda_missing_pin": ("done", ["lint_unpinned_port"], None, "led[3]"),
}

failures = []
for case, (state, want_rules, want_where, want_title) in EXPECT.items():
    d = run(case, state)
    problems = d["problems"] if d else []
    ok = (rules(d) == want_rules and problems and want_title in problems[0]["title"]
          and (problems[0]["where"] == want_where))
    explained = all(p["explain"] and p["fix"] for p in problems)
    print(("ok   " if ok and explained else "FAIL ") + case, rules(d), problems[0]["where"] if problems else None)
    if not (ok and explained):
        failures.append(case)

# a good build must stay quiet
for case in ("good_oss", "good_eda", "good_oss_demo"):
    d = run(case, "done")
    print(("ok   " if d is None else "FAIL ") + case + " stays quiet")
    if d is not None:
        failures.append(case)

# a timing failure names the slowest path
d = run("ise_timing_impossible", "done")
t = d["problems"][0] if d else {}
ok = bool(d) and t["kind"] == "timing" and "count_" in t["explain"] and "levels of logic" in t["explain"]
print(("ok   " if ok else "FAIL ") + "ise_timing_impossible names the worst path")
if not ok:
    failures.append("ise_timing_impossible")

# a message with no rule is passed on unexplained, never guessed at
scratch = Path(tempfile.mkdtemp())
(scratch / "build_out" / "job").mkdir(parents=True)
(scratch / "build_out" / "job" / "xst.log").write_text("ERROR:Xst:9999 - Something nobody wrote a rule for.\n")
d = diagnose.analyse("ise_spartan3", scratch / "build_out" / "job", {}, "failed")
shutil.rmtree(scratch, ignore_errors=True)
ok = d and d["problems"][0]["kind"] == "unexplained" and d["problems"][0]["explain"] is None
print(("ok   " if ok else "FAIL ") + "unknown message is reported as unexplained")
if not ok:
    failures.append("unknown_error")

assert not failures, f"failed: {failures}"
print("\nDIAGNOSE OK")
