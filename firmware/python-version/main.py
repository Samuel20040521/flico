"""
Flicker Detector — Raspberry Pi Pico (RP2040)

Two host-selectable modes (send one character over USB serial):
    'd' / 'D' -> DC   : continuous voltage on the OLED.
    'm' / 'M' -> MON  : binary streaming of raw 12-bit ADC + range index.
                        All FFT / analysis happens on the host (read_data.py).
    'h' / '?'         : help

The host can also push two short strings to the Pico's OLED auxiliary lines
(typically: mean current + peak FFT frequency computed on the host):

    '@1 <text>\\n'    : update OLED top line (≤ 8 chars displayed)
    '@2 <text>\\n'    : update OLED bottom line

Binary streaming protocol (MON mode), 3 bytes per sample:

    byte 0:  0xA0 | range_index     (sync: top nibble = 0xA, low = 0..3)
    byte 1:  ADC value low 8 bits
    byte 2:  ADC value high 4 bits  (top 4 bits always 0 — RP2040 ADC is 12-bit)

The host gets two checkable nibbles of sync per frame and recovers trivially
from any byte slip.

When the Pico is about to do something that blocks the sample loop (OLED
refresh, gear-switch settle), it announces the upcoming gap with a single
ASCII line:

    '# GAP=<n_samples>\\n'

The host uses these to keep the time axis honest at high sample rates — at
10 kHz a single OLED refresh costs ~150 samples (15 ms), which is enough to
shift FFT peaks by several percent if treated as uniform. With the GAP
markers the host stitches segments using true sample indices.

Architecture:
    Photodiode -> 74HC4051 (selects feedback resistor) -> MCP6002 TIA -> ADC0

Pinout:
    I2C0:  SDA=GP4, SCL=GP5    (SSD1306 128x32 OLED)
    ADC0:  GP26                (op-amp Vout)
    MUX:   S0=GP10, S1=GP11    (S2 hardwired to GND)
"""

from machine import ADC, I2C, Pin
import array
import framebuf
import select
import sys
import time
import ssd1306


# ---------------------------------------------------------------------------
# Gear table & calibration.
# ---------------------------------------------------------------------------
R_ON = 100

GEARS = [
    # (mux_channel, label, nominal_R, effective_R)
    (0, "1k",   1_000,     1_000     + R_ON),
    (1, "10k",  10_000,    10_000    + R_ON),
    (2, "100k", 100_000,   100_000   + R_ON),
    (3, "1M",   1_000_000, 1_000_000 + R_ON),
]

ADC_REF      = 3.3
ADC_RAW_MAX  = 4095
V_CLIP_HI    = 3.0
V_FLOOR_LO   = 0.2
RAW_CLIP_HI  = int(V_CLIP_HI  * ADC_RAW_MAX / ADC_REF)
RAW_FLOOR_LO = int(V_FLOOR_LO * ADC_RAW_MAX / ADC_REF)
RAW_SAFE_MID = (RAW_CLIP_HI + RAW_FLOOR_LO) // 2

SWITCH_COOLDOWN_MS = 50

# MON streaming. 10 kHz pushes the per-sample budget hard (~100 µs):
#   adc.read_u16    ~5 µs
#   bytearray fill  ~5 µs
#   stdout.write    ~30-50 µs over USB CDC
#   feed_streaming  ~10 µs (max() throttled)
#   loop overhead   ~10 µs
# If your build can't sustain 10 kHz cleanly, drop to 8000 or 6000.
MON_SAMPLE_HZ        = 10000
MON_PERIOD_US        = 1_000_000 // MON_SAMPLE_HZ
MON_OLED_REFRESH_MS  = 500

# Peak-tracking window for streaming auto-range. Expressed in time so the
# behaviour is sample-rate independent — at 10 kHz, 200 ms = 2000 samples.
MON_PEAK_WINDOW_MS    = 200
MON_PEAK_WINDOW_N     = (MON_SAMPLE_HZ * MON_PEAK_WINDOW_MS) // 1000
MON_PEAK_CHECK_EVERY  = MON_SAMPLE_HZ // 10   # run max() ~10x per second
MON_SWITCH_COOLDOWN_MS = 300

MODE_DC  = "DC"
MODE_MON = "MON"


# ---------------------------------------------------------------------------
# Big text rendering for the OLED. The built-in framebuf font is 8x8; we
# render each character into a small temporary framebuf and copy it pixel
# blocks of `scale x scale` into the main display. Only called on OLED
# refresh ticks, so the per-pixel loop is fine.
# ---------------------------------------------------------------------------
_CHAR_BUF = bytearray(8)
_CHAR_FB  = framebuf.FrameBuffer(_CHAR_BUF, 8, 8, framebuf.MONO_HLSB)


def big_text(oled, s, x, y, scale=2):
    for ci, c in enumerate(s):
        _CHAR_FB.fill(0)
        _CHAR_FB.text(c, 0, 0, 1)
        for py in range(8):
            row = _CHAR_BUF[py]
            if not row:
                continue
            for px in range(8):
                if row & (1 << (7 - px)):
                    oled.fill_rect(x + (ci * 8 + px) * scale,
                                   y + py * scale,
                                   scale, scale, 1)


# ---------------------------------------------------------------------------
# Range manager.
# ---------------------------------------------------------------------------
class RangeManager:
    def __init__(self, s0_pin, s1_pin, start_index=2):
        self._s0 = Pin(s0_pin, Pin.OUT)
        self._s1 = Pin(s1_pin, Pin.OUT)
        self._index = start_index
        self._last_switch_ms = time.ticks_ms()
        self._peak_buf = array.array("H", [RAW_SAFE_MID] * MON_PEAK_WINDOW_N)
        self._peak_idx = 0
        self._peak_check_n = 0
        self._apply()

    def _apply(self):
        channel, _, _, _ = GEARS[self._index]
        self._s0.value(channel & 0x1)
        self._s1.value((channel >> 1) & 0x1)
        self._last_switch_ms = time.ticks_ms()

    @property
    def index(self):
        return self._index

    @property
    def label(self):
        return GEARS[self._index][1]

    @property
    def effective_r(self):
        return GEARS[self._index][3]

    def _cooling_down(self):
        return time.ticks_diff(time.ticks_ms(), self._last_switch_ms) < SWITCH_COOLDOWN_MS

    def update(self, voltage):
        """Instantaneous-voltage auto-range. DC mode only."""
        if self._cooling_down():
            return False
        if voltage > V_CLIP_HI and self._index > 0:
            self._index -= 1
            self._apply()
            return True
        if voltage < V_FLOOR_LO and self._index < len(GEARS) - 1:
            self._index += 1
            self._apply()
            return True
        return False

    def _reset_peak_buf(self):
        for i in range(len(self._peak_buf)):
            self._peak_buf[i] = RAW_SAFE_MID
        self._peak_idx = 0
        self._peak_check_n = 0

    def feed_streaming_raw(self, raw):
        """Rolling-peak auto-range used in MON mode. Returns True on switch."""
        self._peak_buf[self._peak_idx] = raw
        self._peak_idx += 1
        if self._peak_idx >= len(self._peak_buf):
            self._peak_idx = 0

        self._peak_check_n += 1
        if self._peak_check_n < MON_PEAK_CHECK_EVERY:
            return False
        self._peak_check_n = 0

        if time.ticks_diff(time.ticks_ms(), self._last_switch_ms) < MON_SWITCH_COOLDOWN_MS:
            return False

        window_peak = max(self._peak_buf)

        if window_peak > RAW_CLIP_HI and self._index > 0:
            self._index -= 1
            self._apply()
            self._reset_peak_buf()
            return True
        if window_peak < RAW_FLOOR_LO and self._index < len(GEARS) - 1:
            self._index += 1
            self._apply()
            self._reset_peak_buf()
            return True
        return False


# ---------------------------------------------------------------------------
# ADC helpers.
# ---------------------------------------------------------------------------
def read_voltage(adc):
    return adc.read_u16() * (ADC_REF / 65535)


# ---------------------------------------------------------------------------
# Host-pushed OLED auxiliary lines. The host sends short formatted strings
# (mean current, peak FFT frequency, …) and we redraw the OLED whenever
# they update or every MON_OLED_REFRESH_MS as a fallback heartbeat.
# ---------------------------------------------------------------------------
class OledAux:
    __slots__ = ("line1", "line2", "updated_ms", "dirty")

    def __init__(self):
        self.line1 = ""
        self.line2 = ""
        self.updated_ms = 0
        self.dirty = True

    def set(self, n, text):
        text = text[:8]   # 8 chars × 16 px at scale=2 fills 128 px exactly
        if n == 1 and text != self.line1:
            self.line1 = text
            self.dirty = True
        elif n == 2 and text != self.line2:
            self.line2 = text
            self.dirty = True
        self.updated_ms = time.ticks_ms()

    def stale(self):
        return time.ticks_diff(time.ticks_ms(), self.updated_ms) > 3000


# ---------------------------------------------------------------------------
# Non-blocking serial input. Handles single-char mode commands AND multi-byte
# '@N <text>\n' aux-line updates from the host. Drains everything available
# in one call so the hot streaming loop can poll cheaply.
# ---------------------------------------------------------------------------
_stdin_poll = select.poll()
_stdin_poll.register(sys.stdin, select.POLLIN)

_aux_buf = bytearray()
_in_aux  = False


def poll_input(oled_aux):
    """Return MODE_xxx if a mode-switch was requested, else None."""
    global _in_aux
    mode_request = None

    while _stdin_poll.poll(0):
        ch = sys.stdin.read(1)
        if not ch:
            break

        if _in_aux:
            if ch == "\n":
                _process_aux_line(_aux_buf.decode("ascii", errors="ignore"), oled_aux)
                del _aux_buf[:]
                _in_aux = False
            elif len(_aux_buf) < 64:
                _aux_buf.append(ord(ch))
            continue

        if ch == "@":
            _in_aux = True
            continue

        c = ch.lower()
        if c == "d":
            mode_request = MODE_DC
        elif c == "m":
            mode_request = MODE_MON
        elif c in ("h", "?"):
            print_help()
    return mode_request


def _process_aux_line(s, oled_aux):
    # Format: '<digit><space><text>'. Digit selects which OLED line to set.
    if len(s) >= 2 and s[0] in "12" and (len(s) < 3 or s[1] == " "):
        oled_aux.set(int(s[0]), s[2:] if len(s) > 2 else "")


def print_help():
    print("# commands: d=DC  m=MON  h=help  | aux: '@1 <text>' / '@2 <text>'")
    print("# MON: 3-byte frames [0xA0|range, adc_lo, adc_hi] @ {} Hz"
          .format(MON_SAMPLE_HZ))


# ---------------------------------------------------------------------------
# Gap announcement — tells the host how many samples we are about to skip
# while doing something blocking.
# ---------------------------------------------------------------------------
def announce_gap(n_samples):
    if n_samples > 0:
        sys.stdout.write("\n# GAP={}\n".format(n_samples).encode())


# ---------------------------------------------------------------------------
# DC mode tick.
# ---------------------------------------------------------------------------
def tick_dc(adc, rm, oled):
    v = read_voltage(adc)
    if rm.update(v):
        time.sleep_ms(SWITCH_COOLDOWN_MS)
        v = read_voltage(adc)
    oled.fill(0)
    big_text(oled, "{:.3f}V".format(v), 0, 0, 2)
    big_text(oled, rm.label, 0, 16, 2)
    oled.show()
    time.sleep_ms(20)


# ---------------------------------------------------------------------------
# MON mode tick. Hot path — keep per-sample work small.
# ---------------------------------------------------------------------------
_FRAME = bytearray(3)
_STDOUT = sys.stdout


class MonState:
    __slots__ = ("started", "last_oled_ms")

    def __init__(self):
        self.started = False
        self.last_oled_ms = 0

    def reset(self):
        self.started = False
        self.last_oled_ms = 0


def _refresh_mon_oled(oled, rm, oled_aux):
    """One OLED redraw. Caller is responsible for announcing the gap first."""
    oled.fill(0)
    if oled_aux.line1 or oled_aux.line2:
        if oled_aux.line1:
            big_text(oled, oled_aux.line1, 0, 0, 2)
        if oled_aux.line2:
            big_text(oled, oled_aux.line2, 0, 16, 2)
    else:
        big_text(oled, "MON", 0, 0, 2)
        big_text(oled, rm.label, 0, 16, 2)
    oled.show()


def tick_mon(adc, rm, oled, state, oled_aux):
    if not state.started:
        state.started = True
        state.last_oled_ms = time.ticks_ms()
        sys.stdout.write(b"# MODE=MON\n")
        sys.stdout.write("# RATE={}\n".format(MON_SAMPLE_HZ).encode())

    deadline = time.ticks_add(time.ticks_us(), MON_PERIOD_US)

    raw = adc.read_u16() >> 4
    idx = rm._index

    _FRAME[0] = 0xA0 | (idx & 0x0F)
    _FRAME[1] = raw & 0xFF
    _FRAME[2] = (raw >> 8) & 0x0F
    _STDOUT.write(_FRAME)

    if rm.feed_streaming_raw(raw):
        announce_gap(SWITCH_COOLDOWN_MS * 1000 // MON_PERIOD_US)
        time.sleep_ms(SWITCH_COOLDOWN_MS)
        return

    now = time.ticks_ms()
    if oled_aux.dirty or time.ticks_diff(now, state.last_oled_ms) > MON_OLED_REFRESH_MS:
        state.last_oled_ms = now
        t0 = time.ticks_us()
        oled_aux.dirty = False
        _refresh_mon_oled(oled, rm, oled_aux)
        elapsed_us = time.ticks_diff(time.ticks_us(), t0)
        # We just blocked the sample loop for elapsed_us microseconds; the
        # host needs to know so it doesn't compress those samples into the
        # uniform-time assumption.
        announce_gap(elapsed_us // MON_PERIOD_US)
        return

    while time.ticks_diff(time.ticks_us(), deadline) < 0:
        pass


# ---------------------------------------------------------------------------
# Main loop.
# ---------------------------------------------------------------------------
def main():
    i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400_000)
    oled = ssd1306.SSD1306_I2C(128, 32, i2c)
    adc = ADC(Pin(26))
    rm = RangeManager(s0_pin=10, s1_pin=11, start_index=2)
    mon_state = MonState()
    oled_aux = OledAux()

    print("# Flicker Detector ready.")
    print_help()

    mode = MODE_DC
    time.sleep_ms(SWITCH_COOLDOWN_MS)

    while True:
        cmd = poll_input(oled_aux)
        if cmd and cmd != mode:
            mode = cmd
            mon_state.reset()
            oled_aux.dirty = True
            sys.stdout.write("# MODE={}\n".format(mode).encode())

        if mode == MODE_DC:
            tick_dc(adc, rm, oled)
        else:
            tick_mon(adc, rm, oled, mon_state, oled_aux)


if __name__ == "__main__":
    main()
