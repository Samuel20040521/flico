"""
apply_layout.py — push the PD-Detector-Pro PCB into a Pico-shield layout.

Operates on PD_detector_pro.kicad_pcb via the pcbnew Python API. After
this script runs, the board has:

    * 21 x 51 mm rectangular outline (Pico footprint, portrait)
    * Every footprint moved to its planned (x, y, rot) coordinate
    * Custom net class HIGH_Z (TIA_SUM, V_REF) with tighter clearance
    * Track-width / via-size defaults set for a 2-layer AFE
    * GNDA + GND zones on F.Cu and B.Cu (split vertically, GND strip on
      the right edge where J1/J2 live)

Routing is intentionally left to the user — that's the visual part where
pcbnew's UI is much better than a script.

Run with KiCad's bundled Python (the only Python that has `pcbnew`):

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/\
        Versions/Current/bin/python3 apply_layout.py

IMPORTANT: close KiCad / Pcbnew before running. The script saves over
the .kicad_pcb directly.
"""

import json
import os
import sys

import pcbnew


# ----------------------------------------------------------------------------
# Geometry constants
# ----------------------------------------------------------------------------

BOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "PD_detector_pro.kicad_pcb")
PROJECT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "PD_detector_pro.kicad_pro")

# Board: 21 mm wide x 51 mm tall, portrait orientation matching Pi Pico.
BOARD_W = 21.0
BOARD_H = 51.0

# Pico GPIO row centerline (offset from one long edge). Pin 21 of Pico is
# 1.61 mm from the board edge; our shield mirrors that.
PICO_PIN_X = BOARD_W - 1.61          # 19.39 mm
PICO_PITCH = 2.54

# J2 (1x3) covers Pico pins 38, 39, 40 ─ counting "down" the right column
# (40 at top, 38 third from top). J2 pin 1 (closest to first FP pad) maps
# to Pico pin 38 = GND when rotated 180°.
# Pico pin row N has Y = 1.61 + (N-21) * 2.54   (for N in 21..40 on the
# right edge, with pin 21 at the bottom of the board and pin 40 at the top
# in our shield's portrait coordinate frame — but wait, in the shield's
# coords we put pin 40 at the *top* (Y=1.61) because that's the USB end.
#
# So: Pico pin K (21..40) sits at Y = 1.61 + (40 - K) * 2.54 in our shield.
def pico_pin_y(k):
    return 1.61 + (40 - k) * PICO_PITCH

# KiCad's footprint "position" is the FP origin, which for pin-header
# footprints is pin 1. When we rotate 180°, pin 1 stays put and the rest
# of the pin strip flips to the −Y direction. So we anchor each header at
# the world location where its pin 1 should land.
#
#   J2: pin 1 (GND) → Pico pin 38  → (X, pico_pin_y(38))
#   J1: pin 1 (SPI_SDO) → Pico pin 21 → (X, pico_pin_y(21))
#
# With rotation = 180°, the strip then grows toward smaller Y, putting
# pin 3 of J2 on Pico pin 40 and pin 10 of J1 on Pico pin 30 — exactly
# what the schematic mapping expects.
J2_CENTER = (PICO_PIN_X, pico_pin_y(38))      # 6.69 mm  -- pin 1 = GND
J1_CENTER = (PICO_PIN_X, pico_pin_y(21))      # 49.87 mm -- pin 1 = SPI_SDO

# Pico-pin clearance holes. The user's Pico already has tall pin headers
# soldered to *every* one of its 40 pins, so the shield must let those
# unused pins pass through. We connect 13 pins electrically (J1 + J2);
# the remaining 27 get NPTH (non-plated through-hole) clearance holes
# sized for a 0.64 mm header pin with comfortable margin.
PICO_PIN_HOLE_DRILL = 1.0   # mm
PICO_LEFT_EDGE_X    = 1.61  # X-coord of Pico left column (pins 1-20)

PICO_PIN_HOLE_POSITIONS = []   # list of (x, y, label) tuples
for n in range(20):   # left column: Pico pins 1..20
    pico_pin = n + 1
    PICO_PIN_HOLE_POSITIONS.append(
        (PICO_LEFT_EDGE_X, 1.61 + n * PICO_PITCH, f"P{pico_pin}")
    )
for pico_pin in range(31, 38):   # right column gap between J2 (38-40) and J1 (21-30)
    PICO_PIN_HOLE_POSITIONS.append(
        (PICO_PIN_X, pico_pin_y(pico_pin), f"P{pico_pin}")
    )

# ----------------------------------------------------------------------------
# Component placement table:  refdes -> (x_mm, y_mm, rotation_deg)
# ----------------------------------------------------------------------------

# Component group shift: keeps non-header parts off the X=1.61 Pico-pin
# clearance holes on the left edge, and centers the cluster vertically on
# the board. Applied to every refdes except J1 / J2 (those have to stay on
# the Pico pin grid).
COMPONENT_SHIFT = (1.0, 2.5)

# Footprints that should sit on the bottom layer (B.Cu).
# (Previously D1 was here, but we moved to a vertical-case layout with a
# light pipe — D1 now stays on F.Cu at the bottom edge so the case-side
# 90° light pipe can rest against its top-looker active face.)
BOTTOM_LAYER_FOOTPRINTS = set()

def _shift(x, y, r):
    return (x + COMPONENT_SHIFT[0], y + COMPONENT_SHIFT[1], r)

#
# FLIPPED ISLAND LAYOUT (D1 + TIA at bottom, ADC at top-middle).
#
# Reason for the restructure: the earbud case is vertical with USB-C at
# the top edge and the light intake at the BOTTOM edge. D1 must be at
# the bottom of the PCB so that the case-internal light pipe has the
# shortest path. The TIA summing node is the single most sensitive net,
# so U2 OPA, U3 TMUX, and the 8× R||C feedback bank all need to be
# physically adjacent to D1. The ADC (whose input TIA_OUT tolerates much
# longer routing) is moved up to the top-middle.
#
# Layout (top → bottom):
#   Y= 2..13   Power strip + R_STAR
#   Y=15..21   ADC + ADC decoupling
#   Y=23..27   V_REF divider
#   Y=29..36   8× R||C feedback bank
#   Y=37..42   TMUX1208
#   Y=43..46   OPA320 (+ OPA decoupling)
#   Y=47..50   D1 (active face out of F.Cu, into the light pipe)
#
PLACEMENTS = {
    # ---- Headers (right edge of shield, mating to Pico right column) ----
    "J2":  (J2_CENTER[0], J2_CENTER[1], 180),    # 1x3 (GND/NC/VBUS)
    "J1":  (J1_CENTER[0], J1_CENTER[1], 180),    # 1x10 (SDO/CS/GND/SCK/MUX*)

    # ---- Power group (top of board, around J2 entry) ----
    "L1":  _shift(14.0,  3.0,    0),    # ferrite (VBUS -> VBUS_FILT)
    "C1":  _shift(10.0,  3.0,    0),    # 1uF VBUS bulk
    "U1":  _shift(12.0,  6.5,    0),    # LP5907MFX-3.3
    "C2":  _shift( 8.5,  6.5,    0),    # 1uF LDO IN
    "C3":  _shift(15.5,  6.5,    0),    # 1uF LDO OUT
    "C4":  _shift(12.0,  9.0,    0),    # 10nF BYP (0603)
    "R1":  _shift(16.0, 11.0,    0),    # R_STAR (0Ω, GND↔GNDA bridge)

    # ---- ADC at TOP-MIDDLE (moved up from old bottom position) ----
    "U4":  _shift( 9.0, 14.5,   0),     # ADS8326 MSOP-8
    "C20": _shift( 4.0, 14.5,   0),     # 100nF ADC REF (closest to U4 pin 1)
    "C21": _shift( 4.0, 17.0,   0),     # 10uF  ADC REF
    "C18": _shift(13.0, 14.5,   0),     # 100nF ADC VDD
    "C19": _shift(13.0, 17.0,   0),     # 10uF  ADC VDD

    # ---- V_REF divider (middle of board) ----
    "R2":  _shift( 3.0, 19.5,   0),     # 100k V_REF top
    "R3":  _shift( 3.0, 21.5,   0),     # 4.7k  V_REF bot
    "C5":  _shift( 3.0, 23.5,   0),     # 100nF V_REF cap (low impedance)

    # ---- 8× R||C feedback bank (4 cols × 2 rows) above TMUX ----
    "R4":  _shift( 3.5, 26.5, 0), "C10": _shift( 3.5, 28.0, 0),  # gear 0
    "R5":  _shift( 7.0, 26.5, 0), "C11": _shift( 7.0, 28.0, 0),  # gear 1
    "R6":  _shift(10.5, 26.5, 0), "C12": _shift(10.5, 28.0, 0),  # gear 2
    "R7":  _shift(14.0, 26.5, 0), "C13": _shift(14.0, 28.0, 0),  # gear 3
    "R8":  _shift( 3.5, 30.0, 0), "C14": _shift( 3.5, 31.5, 0),  # gear 4
    "R9":  _shift( 7.0, 30.0, 0), "C15": _shift( 7.0, 31.5, 0),  # gear 5
    "R10": _shift(10.5, 30.0, 0), "C16": _shift(10.5, 31.5, 0),  # gear 6
    "R11": _shift(14.0, 30.0, 0), "C17": _shift(14.0, 31.5, 0),  # gear 7

    # ---- TMUX1208 directly under feedback bank ----
    "U3":  _shift( 9.0, 36.5,   90),    # TSSOP-16, long axis along Y
    "C8":  _shift(14.5, 35.0,    0),    # 100nF MUX VDD HF
    "C9":  _shift(14.5, 38.0,    0),    # 10uF  MUX VDD bulk

    # ---- OPA320 directly under TMUX, hugging D1 ----
    "U2":  _shift( 9.0, 41.5,    0),    # OPA320 SOT-23-5
    "C6":  _shift(14.0, 40.5,    0),    # 100nF OPA VDD HF
    "C7":  _shift(14.0, 43.0,    0),    # 10uF  OPA VDD bulk

    # ---- D1 SFH 2440 at the very bottom edge ----
    # F.Cu top-looker; active face exits +Z (out the front of case).
    # Case-internal light pipe routes light from the bottom case opening
    # up 90° onto this active face.
    "D1":  _shift( 9.0, 46.0,    0),    # → world (10.0, 48.5)
}


# ----------------------------------------------------------------------------
# Design rules
# ----------------------------------------------------------------------------

# Defaults (Default net class):
DEFAULT_TRACK_W    = 0.20
DEFAULT_CLEARANCE  = 0.20
POWER_TRACK_W      = 0.40
VIA_DRILL          = 0.30
VIA_DIA            = 0.60

# HIGH_Z net class (TIA_SUM + V_REF):
HIGH_Z_NETS        = ["TIA_SUM", "V_REF"]
HIGH_Z_TRACK_W     = 0.25
HIGH_Z_CLEARANCE   = 0.50


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def mm(v):
    """mm -> internal nm (KiCad stores in nanometers)."""
    return pcbnew.FromMM(v)


def vec(x, y):
    """Make a VECTOR2I from mm."""
    return pcbnew.VECTOR2I(mm(x), mm(y))


def remove_existing_edge_cuts(board):
    to_remove = []
    for drw in board.GetDrawings():
        if drw.GetLayer() == pcbnew.Edge_Cuts:
            to_remove.append(drw)
    for d in to_remove:
        board.Remove(d)


def add_edge_cuts_rectangle(board, w, h):
    """Place 4 lines on Edge.Cuts forming a (0,0)-(w,h) rectangle."""
    corners = [(0, 0), (w, 0), (w, h), (0, h), (0, 0)]
    for (x1, y1), (x2, y2) in zip(corners, corners[1:]):
        seg = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetStart(vec(x1, y1))
        seg.SetEnd(vec(x2, y2))
        seg.SetWidth(mm(0.15))
        board.Add(seg)


def remove_existing_zones(board):
    """Remove any previously-script-created zones (keep user zones alone)."""
    to_remove = []
    for z in board.Zones():
        # Heuristic: we only ever script-add zones with names containing
        # "GNDA-" or "GND-". Don't remove user zones.
        try:
            name = z.GetZoneName()
        except Exception:
            name = ""
        if name.startswith("GNDA-") or name.startswith("GND-"):
            to_remove.append(z)
    for z in to_remove:
        board.Remove(z)


def find_net(board, name):
    """Return the NETINFO_ITEM for a net by name, or None."""
    net = board.FindNet(name)
    return net


def add_filled_zone(board, net_name, layer, corners, zone_name):
    """
    Create a polygon-bounded copper zone on `layer` tied to `net_name`.
    `corners` is a list of (x_mm, y_mm) tuples (no need to close it).
    """
    net = find_net(board, net_name)
    if net is None:
        print(f"  ! net '{net_name}' not found — skipping zone {zone_name}")
        return None

    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNetCode(net.GetNetCode())
    z.SetZoneName(zone_name)

    # Outline polygon
    sp = z.Outline()
    sp.NewOutline()
    for (x, y) in corners:
        sp.Append(mm(x), mm(y))

    z.SetIsFilled(False)            # let the user run "Fill all zones" later
    z.SetLocalClearance(mm(0.20))
    z.SetMinThickness(mm(0.20))
    # Thermal relief spokes — gentler than solid for hand-soldering pads
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetThermalReliefGap(mm(0.30))
    z.SetThermalReliefSpokeWidth(mm(0.30))

    board.Add(z)
    return z


def patch_project_design_rules():
    """
    Modify .kicad_pro (JSON) to set net classes, track widths, via sizes,
    and clearance. This is the canonical place KiCad 10 stores DRC rules —
    pcbnew's Python API in v10 mostly reads from here, doesn't write.
    """
    if not os.path.exists(PROJECT_PATH):
        print(f"  ! project file not found at {PROJECT_PATH}, skipping")
        return
    with open(PROJECT_PATH) as f:
        proj = json.load(f)

    # --- net_settings: classes + assignments -------------------------------
    ns = proj.setdefault("net_settings", {})
    classes = ns.setdefault("classes", [])

    # Update Default class
    default_cls = next((c for c in classes if c.get("name") == "Default"), None)
    if default_cls is None:
        default_cls = {"name": "Default"}
        classes.append(default_cls)
    default_cls.update({
        "clearance":    DEFAULT_CLEARANCE,
        "track_width":  DEFAULT_TRACK_W,
        "via_diameter": VIA_DIA,
        "via_drill":    VIA_DRILL,
    })

    # Add (or update) HIGH_Z class
    hz = next((c for c in classes if c.get("name") == "HIGH_Z"), None)
    if hz is None:
        hz = {"name": "HIGH_Z"}
        classes.append(hz)
    hz.update({
        "clearance":      HIGH_Z_CLEARANCE,
        "track_width":    HIGH_Z_TRACK_W,
        "via_diameter":   VIA_DIA,
        "via_drill":      VIA_DRILL,
        "pcb_color":      "rgba(255, 200, 0, 1.000)",   # gold tint
        "schematic_color":"rgba(255, 200, 0, 1.000)",
    })

    # Bind HIGH_Z to the sensitive nets. The field may already exist with
    # value None (default-empty), so coerce to dict first.
    if not isinstance(ns.get("netclass_assignments"), dict):
        ns["netclass_assignments"] = {}
    nca = ns["netclass_assignments"]
    for nname in HIGH_Z_NETS:
        nca[nname] = "HIGH_Z"

    # --- board.design_settings: rules + track/via lists --------------------
    board_section = proj.setdefault("board", {})
    ds = board_section.setdefault("design_settings", {})
    rules = ds.setdefault("rules", {})
    rules["min_clearance"]   = DEFAULT_CLEARANCE
    rules["min_track_width"] = DEFAULT_TRACK_W
    rules["min_via_diameter"] = VIA_DIA
    rules["min_via_annular_width"] = 0.10

    # Track-widths dropdown (UI shortcut to switch widths during routing).
    # KiCad stores "0" as "default = use net class", and additional entries
    # appear as overrides.
    ds["track_widths"] = [0, DEFAULT_TRACK_W, HIGH_Z_TRACK_W, POWER_TRACK_W]
    ds["via_dimensions"] = [
        {"diameter": 0,       "drill": 0},          # default = use net class
        {"diameter": VIA_DIA, "drill": VIA_DRILL},
    ]

    with open(PROJECT_PATH, "w") as f:
        json.dump(proj, f, indent=2)
    print(f"  patched {os.path.basename(PROJECT_PATH)}: 2 net classes, "
          f"track widths, via sizes, min clearance")


def place_components(board):
    """Move every footprint in PLACEMENTS to its planned coordinate.
    Footprints in BOTTOM_LAYER_FOOTPRINTS are flipped to B.Cu."""
    placed = 0
    missed = []
    flipped = []
    for ref, (x, y, rot) in PLACEMENTS.items():
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            missed.append(ref)
            continue
        fp.SetPosition(vec(x, y))
        fp.SetOrientation(pcbnew.EDA_ANGLE(rot, pcbnew.DEGREES_T))
        # Flip to B.Cu if requested. Flip() takes an "around" point —
        # we pivot around the footprint's own position so the centre
        # doesn't move; Flip(pos, false) mirrors front-to-back without
        # rotating the orientation property.
        should_be_bottom = ref in BOTTOM_LAYER_FOOTPRINTS
        is_bottom = fp.IsFlipped()
        if should_be_bottom and not is_bottom:
            fp.Flip(fp.GetPosition(), False)
            flipped.append(ref)
        elif not should_be_bottom and is_bottom:
            fp.Flip(fp.GetPosition(), False)
            flipped.append(f"{ref} (un-flipped)")
        placed += 1
    return placed, missed, flipped


def add_pico_pin_clearance_holes(board):
    """
    Pico has tall pin headers soldered on every one of its 40 pins. The
    shield's J1 (10 pins) + J2 (3 pins) handle 13 of them electrically;
    the other 27 need bare clearance holes for the unused header pins to
    pass through.

    Implementation: one synthetic FOOTPRINT ("PICO_PINS") containing 27
    NPTH (non-plated through-hole) pads. Idempotent — re-running deletes
    the previous instance and re-adds with current parameters.
    """
    # Remove a previous PICO_PINS footprint if present (for idempotency).
    for fp in list(board.GetFootprints()):
        if fp.GetReference() == "PICO_PINS":
            board.Remove(fp)

    holder = pcbnew.FOOTPRINT(board)
    holder.SetReference("PICO_PINS")
    holder.SetValue("Pico_thru")
    holder.SetPosition(vec(0, 0))
    # Hide the silkscreen reference (one ref text on the board is fine —
    # we don't want it sitting where one of the holes lands).
    try:
        holder.Reference().SetVisible(False)
        holder.Value().SetVisible(False)
    except Exception:
        pass

    drill_mm = PICO_PIN_HOLE_DRILL
    for (x, y, label) in PICO_PIN_HOLE_POSITIONS:
        pad = pcbnew.PAD(holder)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        # For NPTH the pad outline equals the drill: no annular ring.
        pad.SetSize(pcbnew.VECTOR2I(mm(drill_mm), mm(drill_mm)))
        pad.SetDrillSize(pcbnew.VECTOR2I(mm(drill_mm), mm(drill_mm)))
        pad.SetPosition(vec(x, y))
        pad.SetNumber("")          # NPTH conventionally has no pin number
        # Pad position is absolute board coord because the holder FP is at (0,0)
        holder.Add(pad)

    board.Add(holder)
    print(f"  added PICO_PINS ({len(PICO_PIN_HOLE_POSITIONS)} NPTH "
          f"clearance holes, {drill_mm} mm drill)")


def remap_j1_pads(board):
    """
    The PCB was imported from the *original* netlist where J1 had VBUS on
    pin 1. The latest gen_schematic.py reassigns J1 to signals-only (SPI
    + MUX), with VBUS moved to J2. Update each J1 pad's net to match the
    new mapping. Idempotent — re-running just confirms.
    """
    j1 = board.FindFootprintByReference("J1")
    if j1 is None:
        print("  ! J1 not found, skipping remap")
        return

    target = {
        "1":  "SPI_SDO",   # Pico pin 21 = GP16 (MISO)
        "2":  "SPI_CS",    # Pico pin 22 = GP17
        "3":  "GND",       # Pico pin 23
        "4":  "SPI_SCK",   # Pico pin 24 = GP18
        "5":  "MUX_A0",    # Pico pin 25 = GP19
        "6":  "MUX_A1",    # Pico pin 26 = GP20
        "7":  "MUX_A2",    # Pico pin 27 = GP21
        "8":  "MUX_EN",    # Pico pin 28 = GP22
        "9":  None,        # Pico pin 29 = RUN  -- NC
        "10": None,        # Pico pin 30 = GP26 -- NC
    }
    changed = 0
    for pad in j1.Pads():
        num = pad.GetNumber()
        if num not in target:
            continue
        wanted_name = target[num]
        if wanted_name is None:
            # No-connect: net code 0 means unconnected in pcbnew internals.
            pad.SetNetCode(0)
            continue
        net = board.FindNet(wanted_name)
        if net is None:
            print(f"  ! net '{wanted_name}' not found for J1 pad {num}")
            continue
        cur = pad.GetNetname()
        if cur != wanted_name:
            pad.SetNet(net)
            changed += 1
    print(f"  remapped J1 pads ({changed} changed of 10)")


def ensure_j2(board):
    """
    If J2 is missing (user hasn't re-imported the latest netlist), add it
    manually: load the Conn_01x03 footprint, place it, and tie its pads to
    GND and VBUS nets. Skips silently if J2 already exists.
    """
    if board.FindFootprintByReference("J2") is not None:
        return False  # already there, nothing to do

    lib_path = ("/Applications/KiCad/KiCad.app/Contents/SharedSupport/"
                "footprints/Connector_PinHeader_2.54mm.pretty")
    fp = pcbnew.FootprintLoad(lib_path, "PinHeader_1x03_P2.54mm_Vertical")
    if fp is None:
        print("  ! could not load PinHeader_1x03 footprint")
        return False

    fp.SetReference("J2")
    fp.SetValue("Conn_01x03")
    fp.SetPosition(vec(J2_CENTER[0], J2_CENTER[1]))
    fp.SetOrientation(pcbnew.EDA_ANGLE(180, pcbnew.DEGREES_T))

    gnd_net  = board.FindNet("GND")
    vbus_net = board.FindNet("VBUS")
    nc_net   = board.FindNet("")    # empty/no-connect
    if gnd_net is None or vbus_net is None:
        print("  ! GND or VBUS net missing; can't connect J2 pads")
    else:
        for pad in fp.Pads():
            num = pad.GetNumber()
            if num == "1":
                pad.SetNet(gnd_net)
            elif num == "3":
                pad.SetNet(vbus_net)
            else:
                # pin 2 = Pico VSYS, intentional no-connect.
                # Net code 0 = "unconnected" in KiCad's internal model.
                pad.SetNetCode(0)

    board.Add(fp)
    print(f"  added J2 (Conn_01x03) at "
          f"({J2_CENTER[0]:.2f}, {J2_CENTER[1]:.2f}) mm, "
          f"pin 1→GND, pin 3→VBUS")
    return True


def add_zones(board):
    """
    Vertical split: GNDA pour on the left (X=0..16), GND pour on the right
    strip (X=16.5..21) where the J1 / J2 headers live. The 0.5 mm gap
    between zones is bridged only by R_STAR (placed at X≈16.5).
    """
    # Left analog island (GNDA), top + bottom layers
    left_gnda = [(0,    0),
                 (16,   0),
                 (16,  51),
                 (0,   51)]
    # Right strip (GND), top + bottom layers
    right_gnd = [(16.5, 0),
                 (21,   0),
                 (21,  51),
                 (16.5, 51)]

    for layer, suffix in [(pcbnew.F_Cu, "F"), (pcbnew.B_Cu, "B")]:
        add_filled_zone(board, "GNDA", layer, left_gnda, f"GNDA-{suffix}")
        add_filled_zone(board, "GND",  layer, right_gnd, f"GND-{suffix}")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    if not os.path.exists(BOARD_PATH):
        print(f"Board file not found: {BOARD_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {BOARD_PATH}")
    board = pcbnew.LoadBoard(BOARD_PATH)

    print(f"  footprints in board: {len(board.GetFootprints())}")
    print(f"  nets in board:       {len(list(board.GetNetsByName()))}")

    # Do footprint-level work FIRST; KiCad's SWIG bindings invalidate
    # footprint references after board.Remove(drawing) operations, so we
    # must finish all footprint manipulation before touching Edge.Cuts /
    # zones.
    print("Remapping J1 pads (old netlist had VBUS on pin 1)...")
    remap_j1_pads(board)

    print("Adding J2 if missing...")
    ensure_j2(board)

    print("Adding Pico-pin clearance holes...")
    add_pico_pin_clearance_holes(board)

    print("Placing components...")
    placed, missed, flipped = place_components(board)
    print(f"  placed: {placed} / {len(PLACEMENTS)} expected")
    if flipped:
        print(f"  flipped to B.Cu (bottom layer): {flipped}")
    if missed:
        print(f"  unmapped footprints (left at current position): {missed}")

    # Now safe to do drawing-level work — footprints are done.
    print("Removing previous edge cuts (if any)...")
    remove_existing_edge_cuts(board)

    print(f"Adding {BOARD_W} x {BOARD_H} mm board outline...")
    add_edge_cuts_rectangle(board, BOARD_W, BOARD_H)

    print("Patching project file (design rules + net classes)...")
    patch_project_design_rules()

    print("Re-creating ground zones...")
    remove_existing_zones(board)
    add_zones(board)

    print("Saving...")
    board.Save(BOARD_PATH)
    print("Done.")


if __name__ == "__main__":
    main()
