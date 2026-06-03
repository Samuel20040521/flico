"""
SKiDL netlist generator for the PD Flicker Detector.

Run with KiCad's bundled Python so symbol libraries resolve:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/\
        Versions/Current/bin/python3 -m pip install --user skidl
    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/\
        Versions/Current/bin/python3 gen_schematic.py

Outputs `pd_detector.net` (KiCad netlist) in the current directory.
Import it via Pcbnew → File → Import → Netlist (or use the schematic editor's
"Update PCB from Schematic" workflow after creating a placeholder .kicad_sch).

Notes:
- ADS8326 may not be in stock symbol libs; substitute MCP3201 footprint
  (also MSOP-8) and edit the Value field if needed.
- OPA320 part name in stock lib is `OPA320xxIDBV` (Amplifier_Operational).
- TMUX1208 lives in Analog_Switch lib.
- BPW34 lives in Sensor_Optical lib.
"""

from skidl import (
    Part, Net, generate_netlist, ERC, set_default_tool, KICAD9, TEMPLATE,
    lib_search_paths,
)

set_default_tool(KICAD9)

# Point SKiDL at the stock KiCad 10 symbol libraries that ship with the .app
# bundle. Without this it falls back to its built-in mini-lib (which only has
# a few generic parts) and Part("Device", "R", ...) fails.
KICAD_SYMBOLS = (
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"
)
lib_search_paths[KICAD9].append(KICAD_SYMBOLS)

# ----------------------------------------------------------------------------
# Nets
# ----------------------------------------------------------------------------
vbus       = Net("VBUS")
vbus_filt  = Net("VBUS_FILT")
v3v3_a     = Net("+3V3_A")
gnd        = Net("GND")
gnda       = Net("GNDA")
v_ref      = Net("V_REF")
tia_sum    = Net("TIA_SUM")
tia_out    = Net("TIA_OUT")
mux_a0     = Net("MUX_A0")
mux_a1     = Net("MUX_A1")
mux_a2     = Net("MUX_A2")
mux_en     = Net("MUX_EN")
spi_sck    = Net("SPI_SCK")
spi_sdo    = Net("SPI_SDO")
spi_cs     = Net("SPI_CS")
gear       = [Net(f"GEAR{i}") for i in range(8)]
v_byp      = Net("LDO_BYP")

# ----------------------------------------------------------------------------
# Templates for common passives (cuts boilerplate)
# ----------------------------------------------------------------------------
R0805 = Part("Device", "R", dest=TEMPLATE,
             footprint="Resistor_SMD:R_0805_2012Metric")
C0805 = Part("Device", "C", dest=TEMPLATE,
             footprint="Capacitor_SMD:C_0805_2012Metric")

# ----------------------------------------------------------------------------
# Power input: VBUS → ferrite → LDO → +3V3_A
# ----------------------------------------------------------------------------
FB1 = Part("Device", "L", value="600R@100MHz",
           footprint="Inductor_SMD:L_0805_2012Metric")
FB1[1, 2] += vbus, vbus_filt

C_VIN = C0805(value="1uF");   C_VIN[1, 2] += vbus_filt, gnd

U_LDO = Part("Regulator_Linear", "LP5907MFX-3.3",
             footprint="Package_TO_SOT_SMD:SOT-23-5")
U_LDO["IN"]  += vbus_filt
U_LDO["OUT"] += v3v3_a
U_LDO["GND"] += gnda
U_LDO["EN"]  += vbus_filt              # always on
U_LDO[4]     += v_byp                  # pin 4 = BYP (KiCad symbol labels it NC)

C_LDOIN  = C0805(value="1uF");   C_LDOIN[1, 2]  += vbus_filt, gnda
C_LDOOUT = C0805(value="1uF");   C_LDOOUT[1, 2] += v3v3_a,    gnda
C_BYP    = Part("Device", "C", value="10nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
C_BYP[1, 2] += v_byp, gnda

# Star-ground tie (single point between digital GND and analog GNDA)
R_STAR = R0805(value="0", ref_prefix="R");  R_STAR[1, 2] += gnd, gnda

# ----------------------------------------------------------------------------
# V_REF divider for TIA non-inverting input (~150 mV)
# ----------------------------------------------------------------------------
R_REF_TOP = R0805(value="100k");  R_REF_TOP[1, 2] += v3v3_a, v_ref
R_REF_BOT = R0805(value="4.7k");  R_REF_BOT[1, 2] += v_ref,  gnda
C_REF     = C0805(value="100nF"); C_REF[1, 2]     += v_ref,  gnda

# ----------------------------------------------------------------------------
# Photodiode (BPW34) — cathode to summing node, anode to GNDA
# ----------------------------------------------------------------------------
D_PD = Part("Sensor_Optical", "SFH2440",
            footprint="OptoDevice:Osram_SFH2440")
D_PD["K"] += tia_sum
D_PD["A"] += gnda

# ----------------------------------------------------------------------------
# OPA320 TIA
# ----------------------------------------------------------------------------
# OPA320 not in stock KiCad lib — borrow pin-compatible SOT-23-5 single
# op-amp symbol (OPA1655DBV) and override the Value field to OPA320AIDBVR.
U_OPA = Part("Amplifier_Operational", "OPA1655DBV",
             value="OPA320AIDBVR",
             footprint="Package_TO_SOT_SMD:SOT-23-5")
U_OPA[1] += tia_out      # OUT
U_OPA[2] += gnda         # V-
U_OPA[3] += v_ref        # IN+
U_OPA[4] += tia_sum      # IN-
U_OPA[5] += v3v3_a       # V+

C_OPA_HF   = C0805(value="100nF"); C_OPA_HF[1, 2]   += v3v3_a, gnda
C_OPA_BULK = C0805(value="10uF");  C_OPA_BULK[1, 2] += v3v3_a, gnda

# ----------------------------------------------------------------------------
# TMUX1208 — gain-switch in feedback path. D (common) → V- (summing node).
# ----------------------------------------------------------------------------
U_MUX = Part("Analog_Switch", "TMUX1208PW",
             value="TMUX1208PWR",
             footprint="Package_SO:TSSOP-16_4.4x5mm_P0.65mm")
U_MUX["VDD"] += v3v3_a
U_MUX["GND"] += gnda
U_MUX["D"]   += tia_sum
U_MUX["A0"]  += mux_a0
U_MUX["A1"]  += mux_a1
U_MUX["A2"]  += mux_a2
U_MUX["EN"]  += mux_en
for i in range(8):
    U_MUX[f"S{i+1}"] += gear[i]

# If you'd rather have always-on, replace mux_en with v3v3_a above.

C_MUX_HF   = C0805(value="100nF"); C_MUX_HF[1, 2]   += v3v3_a, gnda
C_MUX_BULK = C0805(value="10uF");  C_MUX_BULK[1, 2] += v3v3_a, gnda

# ----------------------------------------------------------------------------
# Feedback RC banks (8 gears).
# Each: TIA_OUT —— R||C —— GEARn
# Values targeting ~16 kHz BW per gear (1/(2π·R·C)). For 32 kHz halve every C.
# ----------------------------------------------------------------------------
gear_rc = [
    ("1k",   "10nF"),
    ("3.3k", "3.3nF"),
    ("10k",  "1nF"),
    ("33k",  "330pF"),
    ("100k", "100pF"),
    ("330k", "33pF"),
    ("1M",   "10pF"),
    ("10M",  None),     # board parasitics provide effective C
]
for i, (rv, cv) in enumerate(gear_rc):
    R = R0805(value=rv);  R[1, 2] += tia_out, gear[i]
    if cv is not None:
        C = C0805(value=cv); C[1, 2] += tia_out, gear[i]
    else:
        # Leave footprint for hand-tuning at assembly time (DNP).
        C = C0805(value="DNP"); C[1, 2] += tia_out, gear[i]

# ----------------------------------------------------------------------------
# ADS8326 — 16-bit, 250 kSps SAR ADC, pseudo-differential
# ----------------------------------------------------------------------------
# ADS8326 not in stock KiCad lib. MCP3201 has identical MSOP-8 pinout
# (REF, IN+, IN-, Vss, /CS, Dout, CLK, Vdd) so we borrow its symbol and
# override the Value field.
U_ADC = Part("Analog_ADC", "MCP3201",
             value="ADS8326",
             footprint="Package_SO:MSOP-8_3x3mm_P0.65mm")
U_ADC[1] += v3v3_a   # REF (use supply ratiometrically; add C_ADC_REF nearby)
U_ADC[2] += tia_out  # +IN
U_ADC[3] += v_ref    # -IN (pseudo-diff cancels TIA bias offset)
U_ADC[4] += gnda     # GND
U_ADC[5] += spi_cs   # CS
U_ADC[6] += spi_sdo  # SDO
U_ADC[7] += spi_sck  # SCLK
U_ADC[8] += v3v3_a   # VDD

C_ADC_HF   = C0805(value="100nF"); C_ADC_HF[1, 2]   += v3v3_a, gnda
C_ADC_BULK = C0805(value="10uF");  C_ADC_BULK[1, 2] += v3v3_a, gnda
# Dedicated REF decoupling — distance to REF pin must be <1 mm in layout.
C_ADC_REFA = C0805(value="100nF"); C_ADC_REFA[1, 2] += v3v3_a, gnda
C_ADC_REFB = C0805(value="10uF");  C_ADC_REFB[1, 2] += v3v3_a, gnda

# ----------------------------------------------------------------------------
# Pico shield connectors.
#
# Board mates on top of Pi Pico (51 x 21 mm). All signals come up the right
# long edge of Pico (pins 21..40); VBUS is at the very top corner (pin 40).
# That forces us to use TWO headers so VBUS doesn't share a row with SPI:
#
#   J_SIG  = 1x10 over Pico pins 21..30  -- SPI + MUX control + GNDs
#   J_PWR  = 1x3  over Pico pins 38..40  -- GND, NC (VSYS), VBUS
#
# This requires re-assigning the MUX control GPIOs on the Pico firmware from
# GP10..13 (Pico pins 14..17, LEFT edge) to GP19..22 (Pico pins 25..28,
# RIGHT edge). Firmware edit is tracked in plan §7 step 5.
# ----------------------------------------------------------------------------
J_SIG = Part("Connector_Generic", "Conn_01x10",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")
J_SIG[1]  += spi_sdo     # Pico pin 21 = GP16 (SPI0 RX / MISO)
J_SIG[2]  += spi_cs      # Pico pin 22 = GP17
J_SIG[3]  += gnd         # Pico pin 23 = GND
J_SIG[4]  += spi_sck     # Pico pin 24 = GP18 (SPI0 SCK)
J_SIG[5]  += mux_a0      # Pico pin 25 = GP19  <-- was GP10
J_SIG[6]  += mux_a1      # Pico pin 26 = GP20  <-- was GP11
J_SIG[7]  += mux_a2      # Pico pin 27 = GP21  <-- was GP12
J_SIG[8]  += mux_en      # Pico pin 28 = GP22  <-- was GP13
# J_SIG[9]  = Pico pin 29 = RUN  -- LEAVE NC! Connecting would reset the Pico
# J_SIG[10] = Pico pin 30 = GP26 -- NC (reserved for future ADC0 use)

J_PWR = Part("Connector_Generic", "Conn_01x03",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
J_PWR[1] += gnd          # Pico pin 38 = GND  (dedicated VBUS return)
# J_PWR[2] = Pico pin 39 = VSYS -- LEAVE NC (don't want USB-current bypass)
J_PWR[3] += vbus         # Pico pin 40 = VBUS (5V from USB)

# ----------------------------------------------------------------------------
# Done.
# ----------------------------------------------------------------------------
ERC()
generate_netlist(file_="pd_detector.net")
print("wrote pd_detector.net")
