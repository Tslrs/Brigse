# Changelog

## Unreleased

Fixed
- `program` with a `part` refused every Gowin part (Tang Nano 9K and 20K included): the loader lists `GW1NR-9`, the check looked for `gw1nr`. Found by the new `tests/test_program.py`. Spartan-3A parts are still refused, as documented.

## 0.2.0

New tools
- `lint`: checks Verilog in about a second with Verilator (Icarus Verilog as a fallback), each issue with file, line and source, before a build that takes minutes.
- `cocotb_test`: Python testbenches (cocotb) on Icarus Verilog, one row per test. cocotb lives in its own environment, made with `brigse install-cocotb`.
- `utilization_by_module`: which module uses how much logic, flip-flops, block RAM and multipliers, from a separate synthesis that keeps the hierarchy.

Improved
- ISE report: Fmax and required frequency for every clock (`timing.clocks`).
- Dashboard: live build log (Server-Sent Events).
- Dashboard security: foreign `Origin` refused, requests capped at 100 KB, the page cannot be framed, security headers; an attack test.
- Dashboard: the port is open when `start_in_background` returns (a first request could arrive too early; it showed on Linux).

Testing
- `uv run pytest` runs every check that needs no vendor tools, locally and on GitHub Actions (Ubuntu and macOS).
- The MCP protocol is tested end to end against a fake add-on.
- `ruff` (real mistakes only) runs in CI; `uv.lock` is committed.

## 0.1.0

First public version: Xilinx ISE (Spartan-3) and two Gowin add-ons, error explainer, chip view, waveforms, dashboard with approvals, `install-agent`, terminal commands.
