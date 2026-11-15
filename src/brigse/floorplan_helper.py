"""Draw where a design sits on a Gowin chip.

Runs with the Python of the OSS CAD Suite, because only that one has apycula, the database of the
chip's tiles. Two ways to learn which tiles are used:

  placement  the placement nextpnr wrote (exact; open-source builds only)
  bitstream  a .fs file of any build, read back with gowin_unpack (Gowin EDA builds included)

Usage:
  floorplan_helper.py placement <family> <placed.json> <out.svg> <label>
  floorplan_helper.py bitstream <family> <file.fs> <out.svg> <label> <gowin_unpack>
Writes <out.svg> and <out.svg>.model.json (what the PNG export draws from); prints a JSON summary.
"""
import json
import re
import subprocess
import sys
import tempfile
import warnings
from collections import Counter, defaultdict
from pathlib import Path

warnings.filterwarnings("ignore")
import apycula                      # noqa: E402
from apycula import chipdb          # noqa: E402

CELL = 18
COLORS = {  # kind: (empty tile, used tile)
    "C": ("#1b2330", "#6c9cff"), "M": ("#1b2330", "#6c9cff"),
    "I": ("#1f2d2a", "#4cc38a"), "B": ("#2e2822", "#f0a050"),
    "D": ("#2b2638", "#b48cff"), "P": ("#352424", "#f0776c"),
}
# cell type prefix -> the tile letters it may sit on (only used to check the orientation of the map)
EXPECT = (("LUT", "CM"), ("ALU", "CM"), ("DFF", "CM"), ("IBUF", "I"), ("OBUF", "I"), ("IOBUF", "I"),
          ("TBUF", "I"), ("rPLL", "P"), ("PLL", "P"))
TILE = re.compile(r"R(\d+)C(\d+)_")
CATEGORY_KIND = {"logic": "CM", "io": "I", "bsram": "B", "dsp": "D", "pll": "P"}


def load_chip(family):
    db = chipdb.load_chipdb(str(Path(apycula.__file__).parent / f"{family}.msgpack.xz"))
    kind_of = {i: letter for letter, ids in db.tile_types.items() for i in ids}
    return db, lambda r, c: kind_of.get(db.grid[r][c], "?")


def from_placement(path, db, kind):
    """Tiles used according to nextpnr's placement. Returns (used, mismatches)."""
    cells = next(iter(json.loads(Path(path).read_text())["modules"].values()))["cells"]
    used = defaultdict(lambda: {"slots": set(), "ffs": set(), "types": Counter()})
    mismatch = 0
    for cell in cells.values():
        bel = cell.get("attributes", {}).get("NEXTPNR_BEL")
        m = bel and re.fullmatch(r"X(\d+)Y(\d+)/(\w+)", bel)
        if not m:
            continue
        c, r, slot = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (0 <= r < db.rows and 0 <= c < db.cols):
            mismatch += 1
            continue
        u = used[(r, c)]
        u["types"][cell["type"]] += 1
        (u["ffs"] if slot.startswith("DFF") else u["slots"]).add(slot)
        for prefix, letters in EXPECT:
            if cell["type"].startswith(prefix) and kind(r, c) not in letters:
                mismatch += 1
                break
    out = {}
    for t, u in used.items():
        occ = 1.0 if kind(*t) not in "CM" else min(1.0, max(len(u["slots"]), len(u["ffs"])) / 8)
        out[t] = {"occ": occ, "tip": ", ".join(f"{n} x {ty}" for ty, n in sorted(u["types"].items()))}
    return out, mismatch


def read_unpacked(text):
    """Cells that a bitstream really configures, from the netlist gowin_unpack writes.

    The unpacker also lists thousands of default flip-flops, multiplexers and input buffers that
    belong to no design, so only cells that cannot be defaults are counted: a LUT or ALU or a
    parameter setting (logic), a block RAM, a PLL, an output buffer. Multipliers are not decoded by
    the unpacker and input buffers cannot be told from defaults, so neither is shown.
    Returns {(row, col): {"cat": ..., "n": ..., "types": Counter}} with 0-based tiles.
    """
    text = "\n".join(l for l in text.splitlines() if l.strip() and not l.lstrip().startswith(("`", "//")))
    body = text[text.index(");") + 2:]
    tiles = {}
    for stmt in body.split(";"):
        stmt = " ".join(stmt.split())
        if not stmt:
            continue
        first = stmt.split(None, 1)[0]
        if first in ("wire", "input", "output", "endmodule", "assign"):
            continue
        m = TILE.search(stmt)
        if not m:
            continue
        tile = (int(m.group(1)) - 1, int(m.group(2)) - 1)
        if stmt.startswith("defparam") or (first.startswith(("LUT", "ALU")) and first != "ALU54D"):
            cat, label = "logic", "LUT" if first.startswith("LUT") else first
        elif first == "BSRAM":
            cat, label = "bsram", "BSRAM"
        elif first.endswith("PLL"):
            cat, label = "pll", first
        elif first in ("OBUF", "TBUF", "IOBUF"):
            cat, label = "io", first
        else:
            continue
        entry = tiles.setdefault(tile, {"cat": cat, "n": 0, "types": Counter()})
        entry["n"] += 1
        if not stmt.startswith("defparam"):
            entry["types"][label] += 1
    return tiles


def from_bitstream(fs, family, unpack, db, kind):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "unpacked.v"
        subprocess.run([unpack, "-d", family, "-o", str(out), str(fs)], capture_output=True, timeout=300, check=True)
        tiles = read_unpacked(out.read_text(errors="replace"))
    used, mismatch = {}, 0
    for t, e in tiles.items():
        if not (0 <= t[0] < db.rows and 0 <= t[1] < db.cols):
            mismatch += 1
            continue
        if kind(*t) not in CATEGORY_KIND[e["cat"]]:
            mismatch += 1
        luts = sum(n for ty, n in e["types"].items() if ty.startswith(("LUT", "ALU")))
        occ = min(1.0, luts / 8) if e["cat"] == "logic" else 1.0
        used[t] = {"occ": occ, "tip": ", ".join(f"{n} x {ty}" for ty, n in sorted(e["types"].items()))}
    return used, mismatch


def build_model(db, kind, used, label, source):
    totals = Counter(kind(r, c) for r in range(db.rows) for c in range(db.cols))
    logic_total = totals["C"] + totals["M"]
    summary = Counter()
    tiles = []
    for r in range(db.rows):
        for c in range(db.cols):
            k = kind(r, c)
            if k not in COLORS:
                continue
            u = used.get((r, c))
            if u:
                summary["logic" if k in "CM" else k] += 1
            tiles.append({"r": r, "c": c, "k": "C" if k == "M" else k, "used": bool(u),
                          "occ": u["occ"] if u else 0, "tip": f"X{c}Y{r}: {u['tip']}" if u else ""})
    from_bits = source == "bitstream"
    legend = [("C", "Logic (LUTs and flip-flops)", f"{summary['logic']} of {logic_total} used"),
              ("I", "Pins (inputs/outputs)",
               f"{summary['I']} of {totals['I']} used" if not from_bits else f"{summary['I']} outputs shown, inputs are not visible"),
              ("B", "Block RAM", f"{summary['B']} of {totals['B']} used"),
              ("D", "Multipliers (DSP)", f"{summary['D']} of {totals['D']} used" if not from_bits else "not readable from a bitstream"),
              ("P", "Clock generators (PLL)", f"{summary['P']} of {totals['P']} used")]
    footer = ("Read from the bitstream. A logic square is lit when it holds a configured LUT; brighter means fuller."
              if from_bits else "Placement by nextpnr. Brighter logic squares are fuller.")
    return {"label": label, "source": source, "rows": db.rows, "cols": db.cols, "tiles": tiles,
            "legend": legend, "footer": footer,
            "summary": {"logic_used": summary["logic"], "logic_total": logic_total,
                        "io_used": summary["I"], "io_total": totals["I"], "bsram_used": summary["B"],
                        "bsram_total": totals["B"], "dsp_used": summary["D"], "dsp_total": totals["D"],
                        "pll_used": summary["P"], "pll_total": totals["P"]}}


def render_svg(model):
    rows, cols = model["rows"], model["cols"]
    w, h = cols * CELL + 40, rows * CELL + 176
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
           'font-family="-apple-system,Segoe UI,sans-serif">',
           f'<rect width="{w}" height="{h}" rx="10" fill="#0f141b"/>',
           f'<text x="20" y="28" fill="#e6e9ef" font-size="16" font-weight="600">{model["label"]}</text>',
           '<text x="20" y="46" fill="#98a2b3" font-size="12">Each square is one tile of the chip. '
           'Coloured squares hold part of your design.</text>']
    for t in model["tiles"]:
        empty, full = COLORS[t["k"]]
        x, y = 20 + t["c"] * CELL, 60 + t["r"] * CELL
        if t["used"]:
            out.append(f'<rect x="{x}" y="{y}" width="{CELL - 2}" height="{CELL - 2}" rx="3" fill="{full}" '
                       f'fill-opacity="{0.45 + 0.55 * t["occ"]:.2f}"><title>{t["tip"]}</title></rect>')
        else:
            out.append(f'<rect x="{x}" y="{y}" width="{CELL - 2}" height="{CELL - 2}" rx="3" fill="{empty}"/>')
    ly = 60 + rows * CELL + 22
    for i, (k, text, count) in enumerate(model["legend"]):
        x, y = 20 + (i % 3) * 270, ly + (i // 3) * 36
        out.append(f'<rect x="{x}" y="{y}" width="14" height="14" rx="3" fill="{COLORS[k][1]}"/>')
        out.append(f'<text x="{x + 22}" y="{y + 11}" fill="#e6e9ef" font-size="12">{text}</text>')
        out.append(f'<text x="{x + 22}" y="{y + 26}" fill="#98a2b3" font-size="11">{count}</text>')
    out.append(f'<text x="20" y="{h - 14}" fill="#667085" font-size="11">{model["footer"]}</text></svg>')
    return "\n".join(out)


def main(argv):
    mode, family, source_path, out_svg, label = argv[:5]
    db, kind = load_chip(family)
    if mode == "placement":
        used, mismatch = from_placement(source_path, db, kind)
    else:
        used, mismatch = from_bitstream(source_path, family, argv[5], db, kind)
    model = build_model(db, kind, used, label, mode)
    Path(out_svg).write_text(render_svg(model))
    Path(str(out_svg) + ".model.json").write_text(json.dumps(model))
    print(json.dumps({"family": family, "source": mode, "tiles": db.rows * db.cols, **model["summary"],
                      "mismatch": mismatch}))


if __name__ == "__main__":
    main(sys.argv[1:])
