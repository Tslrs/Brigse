"""Which module of a design uses how much of the chip: logic, flip-flops, block RAM, multipliers.

The real build flattens the design (the tools optimise across module borders), so its reports cannot
say which module costs what. This runs a separate, quick synthesis with the hierarchy kept (yosys,
`synth_gowin -noflatten`) and adds up the cells per module. It never changes a build.
The numbers are an estimate for the Gowin architecture (4-input LUTs, which is also what Spartan-3
has); a real build of the whole design differs by a few percent.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from . import oss_suite
from .backends.base import BackendError

KINDS = ("luts", "carry_alus", "flip_flops", "block_rams", "dsps", "io_buffers")
LABELS = {"luts": "4-input LUTs", "carry_alus": "carry/adder cells (each takes a LUT slot)",
          "flip_flops": "flip-flops and latches", "block_rams": "block RAMs", "dsps": "multiplier/DSP blocks",
          "io_buffers": "pin buffers"}
BRAM = ("DPB", "DPX9B", "SDPB", "SDPX9B", "SP", "SPX9", "ROM16", "pROM", "pROMX9")
DSP = ("MULT", "ALU54D", "PADD", "MULTALU", "MULTADDALU", "PADD9", "PADD18")


def kind(cell: str) -> str | None:
    """Which resource a Gowin primitive counts as (None: not a resource, e.g. GND, VCC or a mux inside a slice)."""
    if re.fullmatch(r"LUT[1-4]", cell):
        return "luts"
    if cell == "ALU":
        return "carry_alus"
    if re.match(r"(DFF|DLA|DLN)", cell):
        return "flip_flops"
    if cell in BRAM:
        return "block_rams"
    if cell.startswith(DSP):
        return "dsps"
    if cell in ("IBUF", "OBUF", "IOBUF", "TBUF"):
        return "io_buffers"
    return None


def pretty(name: str) -> str:
    """'$paramod\\counter\\W=s32'0000...10000' -> 'counter (W=16)'; '\\top' -> 'top'."""
    m = re.match(r"^\$paramod(?:\$[0-9a-f]+)?\\([^\\]+)((?:\\[^\\=]+=\S+?)*)$", name)
    if not m:
        return name.lstrip("\\")
    params = []
    for p in re.findall(r"\\([^\\=]+)=(\S+?)(?=\\|$)", m.group(2)):
        key, val = p
        v = re.fullmatch(r"s?32'([01]+)", val)
        params.append(f"{key}={int(v.group(1), 2)}" if v else f"{key}={val}")
    return f"{m.group(1)} ({', '.join(params)})" if params else m.group(1)


def summarise(stat: dict, top_name: str | None = None) -> dict:
    """Turn yosys' `stat -json` (hierarchy kept) into per-module resource use."""
    mods = stat["modules"]

    def key(cell: str) -> str | None:
        """The module a cell type stands for: yosys writes the module as '\\mul' but the instance as 'mul'."""
        return cell if cell in mods else "\\" + cell if "\\" + cell in mods else None
    own: dict[str, dict[str, int]] = {}
    kids: dict[str, dict[str, int]] = {}
    for name, m in mods.items():
        own[name] = dict.fromkeys(KINDS, 0)
        kids[name] = {}
        for cell, n in m.get("num_cells_by_type", {}).items():
            if key(cell):
                kids[name][key(cell)] = n
            elif kind(cell):
                own[name][kind(cell)] += n
    totals: dict[str, dict[str, int]] = {}

    def total(name: str) -> dict[str, int]:
        if name not in totals:
            t = dict(own[name])
            for child, n in kids[name].items():
                for k, v in total(child).items():
                    t[k] += n * v
            totals[name] = t
        return totals[name]

    top = key(top_name) if top_name else None
    top = top or next(n for n in mods if not any(n in k for k in kids.values()))
    count = {n: 0 for n in mods}
    count[top] = 1

    def spread(name: str, times: int) -> None:
        for child, n in kids[name].items():
            count[child] += times * n
            spread(child, times * n)
    spread(top, 1)

    grand = total(top)
    rows = []
    for name in mods:
        if count[name] == 0:
            continue
        each = total(name)
        row = {"module": pretty(name), "instances": count[name], "own": {k: v for k, v in own[name].items() if v},
               "with_submodules": {k: v for k, v in each.items() if v},
               "in_design": {k: v * count[name] for k, v in own[name].items() if v}}
        base = grand["luts"] + grand["carry_alus"]
        mine = (own[name]["luts"] + own[name]["carry_alus"]) * count[name]
        row["share_of_logic_percent"] = round(100 * mine / base, 1) if base else 0.0
        rows.append(row)
    rows.sort(key=lambda r: (-r["share_of_logic_percent"], r["module"]))
    return {"top": pretty(top), "design_total": {k: v for k, v in grand.items() if v}, "modules": rows}


def analyse(sources: list[str], top: str, cwd: str | None, family: str = "gw1n", timeout_s: int = 120) -> dict:
    if not oss_suite.installed(("yosys",)):
        raise BackendError("This needs yosys from the open-source Gowin add-on, which is not installed. "
                           "Call request_install('gowin_oss') and ask the user to approve it.")
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    if not base.is_dir():
        raise BackendError(f"Folder not found: {base}")
    files = sources or sorted(p.name for p in base.iterdir() if p.suffix.lower() in (".v", ".sv")
                              and not re.search(r"(^tb_|_tb\.|^test)", p.name, re.I))
    missing = [f for f in files if not (base / f).is_file()]
    if not files or missing:
        raise BackendError(f"HDL sources not found in {base}: {missing or 'none'}")
    if family not in ("gw1n", "gw2a", "gw5a"):
        raise BackendError("family must be gw1n, gw2a or gw5a")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "stat.json"
        read = "; ".join(f"read_verilog -sv {f}" for f in files)
        cmd = [str(oss_suite.tool("yosys")), "-q", "-p",
               f"{read}; hierarchy -check -top {top}; synth_gowin -top {top} -family {family} -noflatten; "
               f"tee -q -o {out} stat -json -top {top}"]
        try:
            p = subprocess.run(cmd, cwd=base, capture_output=True, text=True, timeout=max(1, min(timeout_s, 600)),
                               env=oss_suite.env())
        except subprocess.TimeoutExpired:
            raise BackendError(f"The synthesis did not finish in {timeout_s} s.")
        if p.returncode != 0 or not out.is_file():
            err = [l for l in (p.stdout + p.stderr).splitlines() if "ERROR" in l] or (p.stdout + p.stderr).splitlines()[-5:]
            raise BackendError("yosys could not synthesise the design: " + " | ".join(err)[:600])
        result = summarise(json.loads(out.read_text()), top)
    result["labels"] = LABELS
    result["note"] = ("An estimate: a separate synthesis for the Gowin architecture with the hierarchy kept. The real build "
                      "flattens the design and optimises across modules, so its totals differ by a few percent. "
                      "'own' is the module itself, 'with_submodules' one instance including what it contains, "
                      "'in_design' all its instances together.")
    return result
