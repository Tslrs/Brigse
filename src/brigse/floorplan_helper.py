"""Draw the floorplan of a placed Gowin design as an SVG.

Runs with the Python of the OSS CAD Suite, because only that one has apycula, the database of
the chip's tiles. Usage: floorplan_helper.py <family> <placed.json> <out.svg> <label>
Prints a JSON summary on stdout.
"""
import json
import re
import sys
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
# cell type prefix -> the tile letters it may sit on (used only to check the orientation of the map)
EXPECT = (("LUT", "CM"), ("ALU", "CM"), ("DFF", "CM"), ("IBUF", "I"), ("OBUF", "I"), ("IOBUF", "I"),
          ("TBUF", "I"), ("rPLL", "P"), ("PLL", "P"))
NAMES = {"GW1N-9C": "Tang Nano 9K", "GW2A-18C": "Tang Nano 20K"}


def main(family, placed_path, out_svg, label):
    db = chipdb.load_chipdb(str(Path(apycula.__file__).parent / f"{family}.msgpack.xz"))
    kind_of = {}
    for letter, ids in db.tile_types.items():
        for i in ids:
            kind_of[i] = letter

    def kind(r, c):
        return kind_of.get(db.grid[r][c], "?")

    totals = Counter(kind(r, c) for r in range(db.rows) for c in range(db.cols))
    logic_total = totals["C"] + totals["M"]

    cells = next(iter(json.loads(Path(placed_path).read_text())["modules"].values()))["cells"]
    used = defaultdict(lambda: {"slots": set(), "ffs": set(), "n": 0, "types": Counter(), "names": []})
    mismatch = 0
    for name, cell in cells.items():
        bel = cell.get("attributes", {}).get("NEXTPNR_BEL")
        if not bel or "/" not in bel:
            continue
        xy, slot = bel.split("/", 1)
        m = re.fullmatch(r"X(\d+)Y(\d+)", xy)
        if not m:
            continue
        c, r = int(m.group(1)), int(m.group(2))
        if not (0 <= r < db.rows and 0 <= c < db.cols):
            mismatch += 1
            continue
        u = used[(r, c)]
        u["n"] += 1
        u["types"][cell["type"]] += 1
        u["names"].append(name)
        (u["ffs"] if slot.startswith("DFF") else u["slots"]).add(slot)
        for prefix, letters in EXPECT:
            if cell["type"].startswith(prefix) and kind(r, c) not in letters:
                mismatch += 1
                break

    summary = Counter()
    for (r, c), u in used.items():
        k = kind(r, c)
        summary["logic" if k in "CM" else k] += 1

    w, h = db.cols * CELL + 40, db.rows * CELL + 150
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
           'font-family="-apple-system,Segoe UI,sans-serif">',
           f'<rect width="{w}" height="{h}" rx="10" fill="#0f141b"/>',
           f'<text x="20" y="28" fill="#e6e9ef" font-size="16" font-weight="600">{label}</text>',
           f'<text x="20" y="46" fill="#98a2b3" font-size="12">Each square is one tile of the chip. '
           f'Coloured squares hold part of your design.</text>']
    for r in range(db.rows):
        for c in range(db.cols):
            k = kind(r, c)
            if k not in COLORS:
                continue
            empty, full = COLORS[k]
            u = used.get((r, c))
            x, y = 20 + c * CELL, 60 + r * CELL
            if u:
                occ = 1.0 if k not in "CM" else min(1.0, max(len(u["slots"]), len(u["ffs"])) / 8)
                opacity = 0.45 + 0.55 * occ
                tip = ", ".join(f"{n} x {t}" for t, n in sorted(u["types"].items()))
                out.append(f'<rect x="{x}" y="{y}" width="{CELL - 2}" height="{CELL - 2}" rx="3" fill="{full}" '
                           f'fill-opacity="{opacity:.2f}"><title>X{c}Y{r}: {tip}</title></rect>')
            else:
                out.append(f'<rect x="{x}" y="{y}" width="{CELL - 2}" height="{CELL - 2}" rx="3" fill="{empty}"/>')

    ly = 60 + db.rows * CELL + 22
    legend = [("C", "Logic (LUTs and flip-flops)", f"{summary['logic']} of {logic_total} used"),
              ("I", "Pins (inputs/outputs)", f"{summary['I']} of {totals['I']} used"),
              ("B", "Block RAM", f"{summary['B']} of {totals['B']} used"),
              ("D", "Multipliers (DSP)", f"{summary['D']} of {totals['D']} used"),
              ("P", "Clock generators (PLL)", f"{summary['P']} of {totals['P']} used")]
    for i, (k, text, count) in enumerate(legend):
        x = 20 + (i % 3) * 270
        y = ly + (i // 3) * 36
        out.append(f'<rect x="{x}" y="{y}" width="14" height="14" rx="3" fill="{COLORS[k][1]}"/>')
        out.append(f'<text x="{x + 22}" y="{y + 11}" fill="#e6e9ef" font-size="12">{text}</text>')
        out.append(f'<text x="{x + 22}" y="{y + 26}" fill="#98a2b3" font-size="11">{count}</text>')
    out.append(f'<text x="{w - 20}" y="{h - 12}" fill="#667085" font-size="10" text-anchor="end">'
               'Placement by nextpnr. Brighter logic squares are fuller.</text></svg>')
    Path(out_svg).write_text("\n".join(out))
    print(json.dumps({"family": family, "tiles": db.rows * db.cols, "logic_total": logic_total,
                      "logic_used": summary["logic"], "io_used": summary["I"], "io_total": totals["I"],
                      "bsram_used": summary["B"], "bsram_total": totals["B"],
                      "dsp_used": summary["D"], "dsp_total": totals["D"],
                      "pll_used": summary["P"], "pll_total": totals["P"],
                      "cells_placed": sum(u["n"] for u in used.values()), "mismatch": mismatch}))


if __name__ == "__main__":
    fam, placed, svg, lab = sys.argv[1:5]
    main(fam, placed, svg, lab)
