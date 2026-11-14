"""Catalogue of known boards. Picking a board fixes the part and the cable; ``backends`` lists the add-ons that can build for it.

``loader_board`` is the name openFPGALoader uses (``openFPGALoader --list-boards``);
None means that tool cannot program the board.
"""
from __future__ import annotations

BOARDS: list[dict] = [
    {"id": "spartan3a_starter", "name": "Spartan-3A Starter Kit", "vendor": "AMD/Xilinx",
     "part": "xc3s700a-fg484-4", "backends": ["ise_spartan3"], "loader_board": None,
     "note": "Programming from macOS is not supported yet (needs the vendor programmer)."},
    {"id": "spartan3e_starter", "name": "Spartan-3E Starter Kit", "vendor": "AMD/Xilinx",
     "part": "xc3s500e-fg320-4", "backends": ["ise_spartan3"], "loader_board": None,
     "note": "Build is untested on this part."},
    {"id": "tangnano9k", "name": "Sipeed Tang Nano 9K", "vendor": "Gowin",
     "part": "gw1nr-lv9qn88pc6/i5", "backends": ["gowin_eda", "gowin_oss"], "loader_board": "tangnano9k", "note": ""},
    {"id": "tangnano20k", "name": "Sipeed Tang Nano 20K", "vendor": "Gowin",
     "part": "gw2ar-lv18qn88c8/i7", "backends": ["gowin_eda", "gowin_oss"], "loader_board": "tangnano20k", "note": ""},
    {"id": "arty_a7_35t", "name": "Digilent Arty A7-35T", "vendor": "AMD/Xilinx",
     "part": "xc7a35ticsg324-1l", "backends": ["xilinx7_oss"], "loader_board": "arty_a7_35t", "note": ""},
    {"id": "basys3", "name": "Digilent Basys 3", "vendor": "AMD/Xilinx",
     "part": "xc7a35tcpg236-1", "backends": ["xilinx7_oss"], "loader_board": "basys3", "note": ""},
    {"id": "de10nano", "name": "Terasic DE10-Nano", "vendor": "Altera",
     "part": "5cseba6u23i7", "backends": ["cyclone5_oss"], "loader_board": "de10nano", "note": ""},
]


def get(board_id: str) -> dict | None:
    return next((b for b in BOARDS if b["id"] == board_id), None)
