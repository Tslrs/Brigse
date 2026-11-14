<p align="center"><img src="assets/logo.svg" alt="Brigse logo: a tap with a drop" width="140"></p>

<h1 align="center">Brigse</h1>

<p align="center"><em>Bridge to Semiconductor.</em><br>
The name also sounds like the Greek word for a tap (βρύση), hence the logo.</p>

A modular [MCP](https://modelcontextprotocol.io) server that lets an AI agent build, inspect,
simulate and program FPGA designs through one set of tools. Each FPGA family is an add-on behind
a common interface, so the agent calls `build` the same way for every board.

Status: early. Three add-ons are implemented and tested: Xilinx ISE 14.7 for Spartan-3/3A/3E, and two
for Gowin (the vendor's Gowin EDA if you have it, and the open-source flow). When several add-ons
cover a part, the usable vendor tool is preferred; pass `backend` to `build` to choose.

Open-source add-ons are installed on request: the agent calls `request_install`, the plan (what,
from where, how big, licence) appears in the dashboard at `http://brigse.localhost:8765`, and only
the user can approve it there. Vendor tools and licences are never downloaded by the server.

## How the idea came about

Brigse started as a side effect of another project: running a Next186 x86 system-on-chip on an old
Spartan-3A board. That chip can only be built with Xilinx ISE 14.7, a tool from 2013 with no macOS
version, so every build meant booting a 49 GB Ubuntu desktop VM, typing the same six commands and
reading thousand-line logs to find out whether timing had passed.

Two questions came out of that. Could the heavy part shrink, and could an AI agent do the routine
part? The first answer was a slimmed ISE (about 1.7 GB of the 17.8 GB install) in a small headless
VM. Along the way it turned out that ISE cannot run under user-mode x86 translators on Apple
Silicon at all, which is why the VM stays. The second answer was an MCP server, so that "build this
and tell me if timing passes" becomes one request instead of a ritual.

A second board, a Tang Nano 9K, made it clear that each family should be a separate add-on: you
install only what your boards need, vendor tool or open-source flow, behind the same tools. The
local window and the approval step came from a simple rule: an agent may ask to download or delete
something, but a person decides.

The name is short for *Bridge to Semiconductor*, and it sounds like βρύση, the Greek word for a tap.

## Tools

| Tool | What it does |
|---|---|
| `list_backends` | Installed and planned add-ons, the parts each covers, and their state |
| `build` | Starts synthesis-to-bitstream as a background job, returns a `job_id` |
| `build_status` | Progress per step; when finished, fetches outputs and returns the report |
| `report` | Utilisation, timing, failing constraints and bitstream from an output folder |
| `simulate` | Compiles and runs a Verilog testbench with Icarus Verilog |
| `detect_boards` | USB probes and the JTAG chain (openFPGALoader) |
| `program` | Loads a bitstream; a dry run unless `confirm=true` |
| `backend_control` | Start/stop an add-on's runtime (the ISE VM) |
| `activity` | What is running right now and who started it (agent or user) |
| `list_boards`, `select_board` | Known boards and the one selected |
| `request_install`, `request_uninstall`, `install_status` | Ask the user to approve installing or removing an add-on |
| `dashboard_info` | Address of the local window |

## The window

`brigse dashboard` opens `http://brigse.localhost:8765`, a page served on this machine only. It shows
what is running, the selected board, builds with their reports, and the add-ons. The user can pick
the board, approve or reject the agent's requests, repeat a build, load a build into the board's
SRAM (never flash), start or stop the ISE VM, and install or uninstall the open-source tools.
Only what Brigse downloaded can be uninstalled; vendor tools you installed are never touched.

The agent and the user share the same rules: one build at a time per add-on and one use of the
programmer at a time. While anything runs, the page greys its buttons out and the server refuses
them, and the agent's tools get a clear "already running" answer.

## Install

```bash
brew install uv icarus-verilog openfpgaloader
git clone https://github.com/Tslrs/Brigse.git ~/brigse && cd ~/brigse && uv sync
```

Register it with Claude Code:

```bash
claude mcp add fpga -- uv --directory ~/brigse run brigse
```

## Add-ons

| Add-on | Parts | Toolchain | State |
|---|---|---|---|
| `ise_spartan3` | `xc3s*` | Xilinx ISE 14.7 in a headless x86 VM | implemented |
| `gowin_eda` | `gw1n*`, `gw2a*`, `gw5a*` | your own Gowin EDA install (`gw_sh`), full device support | implemented |
| `gowin_oss` | `gw1n*`, `gw2a*`, `gw5a*` | yosys + nextpnr-himbaechel + apicula (OSS CAD Suite) | implemented |
| `xilinx7_oss` | `xc7*` | yosys + nextpnr-xilinx | planned |
| `cyclone5_oss` | `5c*` | yosys + nextpnr-mistral | planned |

An add-on is one module in `src/brigse/backends/` that subclasses `Backend`
(`status`, `build_start`, `build_status`, `report`, `control`) and is listed in `registry.py`.

### Spartan-3A add-on (ISE 14.7)

ISE has no macOS build, and on Apple Silicon it does not run under user-mode x86 translators
(box64, qemu-user and FEX-Emu were all tried). It therefore runs in a small headless x86-64
Debian VM under Lima/QEMU: about 3 GB instead of a full desktop VM.

You need your own licensed ISE 14.7 install (WebPACK is free). **ISE binaries and licence files
are not part of this repository and must not be committed.** Only the command-line tools and the
Spartan-3 device data are needed, about 1.7 GB of the 17.8 GB install:

- `ISE_DS/ISE/{bin,lib}/lin64`, `ISE_DS/common/{bin,lib}/lin64`
- `ISE_DS/ISE/{data,xilinx,xilinxun,fpga,fpgacore,cse,msg,verilog,vhdl,xcf,xcfp}` (without `data/cse/cseflash`)
- `ISE_DS/ISE/{spartan3,spartan3a,spartan3e,spartan3adsp,virtex,virtex2,virtex2p}`

Create the VM and unpack that tree to `/opt/Xilinx/14.7/ISE_DS`, with the licence in `~/.Xilinx/`:

```bash
brew install lima lima-additional-guestagents qemu
limactl start --name=ise --tty=false --arch=x86_64 --vm-type=qemu --cpus=4 --memory=4 --disk=8 --containerd=none template:debian-12
```

`build` copies the project into the VM, runs `xst`, `ngdbuild`, `map`, `par`, `trce` and `bitgen`,
and copies the results to `<project>/build_out/<job_id>/`. If `<top>.xst` exists the project's
own synthesis script is used; otherwise a `.prj`/`.xst` pair is generated from the sources.

Everything runs under full emulation, so it is slow: the Next186 SoC (55% of an XC3S700A) takes
about 27 minutes.

Known limits:

- Programming a Spartan-3A from macOS is untested. openFPGALoader does not list the XC3S700A or
  the Xilinx Platform Cable USB, so `program` refuses that part instead of guessing.
- One VM, one build at a time is the tested case.

## Test

```bash
uv run python tests/smoke_client.py          # protocol, simulate, report
uv run python tests/smoke_client.py --build  # also builds examples/blinky in the ISE VM
```

## Related work

[fpgaZeroMCP](https://glama.ai/mcp/servers/lcapossio/fpgaZeroMCP) (open-source flow),
[fpga-mcp by wmm246](https://glama.ai/mcp/servers/wmm246/fpga-mcp) (Vivado/Quartus/Anlogic) and
several Vivado-only servers cover current devices. This project's focus is the add-on structure
and legacy ISE-only parts.

## Contributors

- [Tslrs](https://github.com/Tslrs): idea, direction, hardware and testing on real boards
- Claude (Anthropic), working as a coding agent in Claude Code: implementation and tests

## Licence

Brigse is released under the [MIT licence](LICENSE). It covers only the code in this repository.
The FPGA vendor tools that the add-ons drive (Xilinx ISE, Gowin EDA) have their own licences and
are not distributed here.
