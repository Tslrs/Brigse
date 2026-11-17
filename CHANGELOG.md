# Changelog

## 0.2.1

Changed
- `program` now needs the user's approval for every load the agent asks for. Found in a real test: Claude Code called `program` with `confirm=true` straight away and wrote to the board without asking. Now `confirm=true` only creates a request; the user approves in the dashboard or with `brigse approvals`, and the agent calls `program` again with the same arguments (`wait_seconds=50` waits for the answer). An approval is for that exact file content, target and memory (a flash request is never satisfied by an SRAM approval), is used once and is good for ten minutes. A rejection is respected for ten minutes. A dry run (`confirm=false`) is unchanged, and the dashboard's own "Load to board" button needs no extra step: the click is the approval.

Fixed
- A build whose folder was deleted stayed `running` for ever and, because only one build runs per add-on, blocked every new build on it (an agent was stuck behind a 45-minute-old ghost job and had to switch tools). It now ends as `failed` with the reason. `tests/test_localjob.py`.
- Flaky checks in CI fixed (found when the first CI runs after the changes above failed on and off): the dashboard security test could build a "wrong" token equal to the real one (1 run in 64), the terminal test could wait for ever on a read, and the `program` test assumed fixed delays. A script that hangs now reports where it is stuck. No change to the dashboard or the terminal commands themselves.
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
