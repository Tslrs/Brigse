"""The per-clock Fmax in the ISE report, read from real timing reports (tests/fixtures). Needs no vendor tools.

usage: uv run python tests/test_report_ise.py
"""
from pathlib import Path

from brigse import registry

FX = Path(__file__).resolve().parent / "fixtures"
ise = registry.by_name("ise_spartan3")
failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


# a single clock that is far too slow: the numbers the tool prints itself ("Maximum frequency: 218.771MHz")
one = ise.report(FX / "ise_timing_impossible" / "build_out" / "job")["timing"]["clocks"]
check("one clock found", len(one) == 1 and one[0]["constraint"] == "TS_clk")
check("its Fmax is the tool's own figure", one[0]["fmax_mhz"] == 218.771 and one[0]["min_period_ns"] == 4.571)
check("the requirement is read from the constraint line", one[0]["required_ns"] == 1.0 and one[0]["required_mhz"] == 1000.0)
check("it is not met", one[0]["met"] is False and one[0]["timing_errors"] == 141)

# several clocks, derived from a DCM: requirements come from the Derived Constraint Report
rep = ise.report(FX / "ise_timing_multiclock" / "build_out" / "job")["timing"]["clocks"]
by = {c["constraint"]: c for c in rep}
check("six clocks found", len(rep) == 6)
check("a derived clock takes its requirement from the table", by["TS_dcm50_cpu_CLKFX_BUF"]["required_ns"] == 41.25)
check("a clock that makes it is met", by["TS_dcm133_25_CLK90_BUF"]["met"] and by["TS_dcm133_25_CLK90_BUF"]["fmax_mhz"] == 112.461)
check("a clock that misses is not met, with its error count",
      not by["TS_dcm133_25_CLK0_BUF"]["met"] and by["TS_dcm133_25_CLK0_BUF"]["timing_errors"] == 24
      and by["TS_dcm133_25_CLK0_BUF"]["fmax_mhz"] == 97.021)
check("the slowest clock is found", min(rep, key=lambda c: c["fmax_mhz"])["constraint"] == "TS_dcm133_25_CLKFX_BUF")
check("a clock with no paths says its limit is a pulse width", "pulse width" in by["TS_CLK_50MHZ"]["note"])
check("a clock with paths has no such note", "note" not in by["TS_dcm133_25_CLK0_BUF"])

assert not failures, failures
print("\nREPORT ISE OK")
