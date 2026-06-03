"""
reset_routing.py — wipe all tracks/vias and (re)classify nets.

After this script runs:

  * Every segment, arc, and via is removed from the .kicad_pcb.
  * Zones, footprint positions, edge.cuts, project design rules survive.
  * Nets are bucketed into 4 net classes with clear widths and intent:

        POWER       0.40 mm   for VBUS / VBUS_FILT / +3V3_A
                              (any layer, but use F.Cu wide star-distribute)
        HIGH_Z      0.25 mm   for TIA_SUM and V_REF
                              (F.Cu only, no vias)
        DIGITAL_B   0.20 mm   for SPI + MUX control
                              (B.Cu preferred — return current goes in B.Cu
                               GND pour, only cross GND↔GNDA near R_STAR)
        Default     0.20 mm   everything else (TIA_OUT, GEAR0..7, etc.)

Each class is colour-tagged so when you turn on "Color by Net Class" in
pcbnew you can see at a glance which trace belongs to which class.

Run with KiCad's bundled Python (close pcbnew first):

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/\
        Versions/Current/bin/python3 reset_routing.py
"""

import json
import os
import sys

import pcbnew


HERE         = os.path.dirname(os.path.abspath(__file__))
BOARD_PATH   = os.path.join(HERE, "PD_detector_pro.kicad_pcb")
PROJECT_PATH = os.path.join(HERE, "PD_detector_pro.kicad_pro")


# ------------------------------------------------------------------
# Net class definitions: name -> (clearance_mm, track_w_mm, via_dia_mm,
#                                 via_drill_mm, colour_rgba_str)
# ------------------------------------------------------------------
CLASSES = {
    "POWER": (
        0.20, 0.40, 0.60, 0.30,
        "rgba(255,  80,  80, 1.000)",         # red
    ),
    "HIGH_Z": (
        0.50, 0.25, 0.60, 0.30,
        "rgba(255, 200,   0, 1.000)",         # gold
    ),
    "DIGITAL_B": (
        0.20, 0.20, 0.60, 0.30,
        "rgba( 80, 140, 255, 1.000)",         # blue
    ),
}

NET_ASSIGNMENTS = {
    # POWER
    "VBUS":         "POWER",
    "VBUS_FILT":    "POWER",
    "+3V3_A":       "POWER",
    # HIGH_Z (F.Cu only)
    "TIA_SUM":      "HIGH_Z",
    "V_REF":        "HIGH_Z",
    # DIGITAL_B (B.Cu preferred)
    "SPI_SDO":      "DIGITAL_B",
    "SPI_CS":       "DIGITAL_B",
    "SPI_SCK":      "DIGITAL_B",
    "MUX_A0":       "DIGITAL_B",
    "MUX_A1":       "DIGITAL_B",
    "MUX_A2":       "DIGITAL_B",
    "MUX_EN":       "DIGITAL_B",
    # Everything else → Default class
    # (TIA_OUT, GEAR0..GEAR7, GND, GNDA, LDO_BYP)
}

DEFAULT_TRACK_W = 0.20
DEFAULT_CLEARANCE = 0.20


# ------------------------------------------------------------------
def wipe_routing(board):
    """Delete every segment, arc, and via. Keep zones / footprints / drawings."""
    tracks = list(board.GetTracks())   # snapshot first
    seg = arc = via = 0
    for t in tracks:
        tp = t.Type()
        if tp == pcbnew.PCB_TRACE_T:    seg += 1
        elif tp == pcbnew.PCB_ARC_T:    arc += 1
        elif tp == pcbnew.PCB_VIA_T:    via += 1
        board.Remove(t)
    print(f"  removed: {seg} segments, {arc} arcs, {via} vias")


def patch_netclasses():
    """Rewrite net classes + assignments in the .kicad_pro JSON."""
    with open(PROJECT_PATH) as f:
        proj = json.load(f)

    ns = proj.setdefault("net_settings", {})
    classes = ns.setdefault("classes", [])

    # 1) Wipe everything except Default and the canonical ones we manage.
    keep = {"Default"}
    classes = [c for c in classes if c.get("name") in keep]

    # 2) Default class (always present)
    default_cls = next(c for c in classes if c.get("name") == "Default")
    default_cls.update({
        "clearance":    DEFAULT_CLEARANCE,
        "track_width":  DEFAULT_TRACK_W,
        "via_diameter": 0.60,
        "via_drill":    0.30,
    })

    # 3) Our 3 custom classes
    for name, (cl, tw, vd, vdrl, colour) in CLASSES.items():
        classes.append({
            "name":            name,
            "clearance":       cl,
            "track_width":     tw,
            "via_diameter":    vd,
            "via_drill":       vdrl,
            "pcb_color":       colour,
            "schematic_color": colour,
        })

    ns["classes"] = classes

    # 4) Assignments
    if not isinstance(ns.get("netclass_assignments"), dict):
        ns["netclass_assignments"] = {}
    nca = ns["netclass_assignments"]
    nca.clear()
    for net_name, cls_name in NET_ASSIGNMENTS.items():
        nca[net_name] = cls_name

    # 5) Track-width and via dropdowns (so the UI offers them)
    board_ds = proj.setdefault("board", {}).setdefault("design_settings", {})
    board_ds["track_widths"] = [0, DEFAULT_TRACK_W, 0.25, 0.40]
    board_ds["via_dimensions"] = [
        {"diameter": 0, "drill": 0},
        {"diameter": 0.60, "drill": 0.30},
    ]
    board_ds.setdefault("rules", {})["min_clearance"]   = DEFAULT_CLEARANCE
    board_ds["rules"]["min_track_width"]                = DEFAULT_TRACK_W

    with open(PROJECT_PATH, "w") as f:
        json.dump(proj, f, indent=2)

    print(f"  net classes after patch:")
    for c in classes:
        print(f"    {c['name']:10s} track={c.get('track_width')}mm "
              f"clearance={c.get('clearance')}mm  "
              f"color={'set' if c.get('pcb_color') else '—'}")
    print(f"  net assignments: {len(nca)} nets bound to non-Default classes")


def main():
    if not os.path.exists(BOARD_PATH):
        print(f"Board not found: {BOARD_PATH}", file=sys.stderr); sys.exit(1)
    if not os.path.exists(PROJECT_PATH):
        print(f"Project not found: {PROJECT_PATH}", file=sys.stderr); sys.exit(1)

    print(f"Loading {BOARD_PATH}")
    board = pcbnew.LoadBoard(BOARD_PATH)

    print("Wiping all routing (segments, arcs, vias)...")
    wipe_routing(board)

    print("Saving board (no routing, but footprints/zones/outline intact)...")
    board.Save(BOARD_PATH)

    print("Patching project file (net classes + assignments)...")
    patch_netclasses()

    print("\nDone.")
    print()
    print("Next steps in KiCad:")
    print("  1. Open Pcbnew (or File → Revert if it was open).")
    print("  2. View → Net Inspector — confirm each net's class is right.")
    print("  3. Enable: Render Options → Color by Net Class (top-right).")
    print("     POWER → red, HIGH_Z → gold, DIGITAL_B → blue.")
    print("  4. Press 'B' to fill GND/GNDA zones.")
    print("  5. Route fresh, starting with:")
    print("     TIA_SUM (F.Cu, 0.25 mm — auto from HIGH_Z class)")
    print("     V_REF   (F.Cu, 0.25 mm)")
    print("     +3V3_A  (F.Cu, 0.40 mm — auto from POWER class)")
    print("     SPI_*   (B.Cu, 0.20 mm — DIGITAL_B; via near R_STAR)")
    print("     MUX_*   (B.Cu, 0.20 mm)")


if __name__ == "__main__":
    main()
