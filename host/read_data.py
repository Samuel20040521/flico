"""
Flicker Monitor — host-side dashboard for the Pico flicker detector.

PyQtGraph rewrite. The previous matplotlib version suffered from full-canvas
redraws (visible flashes on macOS) every time the y-axis rescaled. PyQtGraph
draws into a QOpenGL/QPainter widget that Qt swaps atomically, so view
changes are flicker-free.

Pairs with main.cpp on the Pico. In MON mode the Pico streams a 3-byte
binary frame per sample at MON_SAMPLE_HZ. All FFT and peak-detection happens
here on the host using numpy.

Wire protocol:

    Status lines      : ASCII, "#..." prefix, '\\n' terminated.
                        '# MODE=MON' / '# MODE=DC' / '# RATE=10000'
                        '# GAP=<n>'  -- the Pico blocked the sample loop.
    Sample frames     : 3 bytes
        byte 0:  0xA0 | range_index    (sync nibble + 4-bit range 0..3)
        byte 1:  ADC low 8 bits
        byte 2:  ADC high 4 bits

Keyboard (focus the plot window):
    space   Run/Stop
    m       send 'm' to Pico (start streaming)
    d       send 'd' to Pico (stop streaming)
    r       cycle Pico sample rate (also swaps FFT band + time window)
    a       toggle smart-tracking view (auto FFT/time around detected peak)
    e       export current buffer to a timestamped CSV
    z       reset auto-fit (time + FFT axes)
    l       toggle log Y on the time plot
    c       clear the rolling buffer
    q       quit

Scroll wheel + drag on the plots zooms / pans (built into pyqtgraph).
"""

import argparse
from collections import deque
import csv
import sys
import threading
import time
from datetime import datetime

import numpy as np
import pyqtgraph as pg
import serial
import serial.tools.list_ports
from PySide6 import QtCore, QtGui, QtWidgets

from demo_serial import FakeSerial


# --- Settings ----------------------------------------------------------------
PORT          = "/dev/cu.usbmodem14101"   # set None to auto-pick first Pico
BAUD          = 115200                    # cosmetic for USB CDC
PICO_SAMPLE_HZ = 10000                    # default; updated by "# RATE=..."
# 8192-sample rolling window: at 1 kHz that's 8.2 s / 0.12 Hz FFT bin width.
MAX_POINTS    = 8192
# Each sample rate owns one view: FFT covers an octave above the previous
# rate's Nyquist, and the time-domain window equals 5 × the longest period in
# the band (= 5 / band_lo). Pressing 'r' swaps the whole tuple atomically.
#   1000 Hz uses band_lo = 1 Hz (the screen-flicker floor) so the time window
#   settles at a comfortable 5 s; higher rates show progressively shorter
#   windows because they're only there to resolve faster signals.
RATE_VIEW = {
    #  rate:    (fft_lo, fft_hi,   time_window_s)
    1000:       (1.0,        500.0,   5.0),
    2000:       (500.0,    1000.0,    0.010),
    4000:       (1000.0,   2000.0,    0.005),
    8000:       (2000.0,   4000.0,    0.0025),
    16000:      (4000.0,   8000.0,    0.00125),
    32000:      (8000.0,  16000.0,    0.000625),
}
MIN_FFT_HZ    = 0.3
# Smart-mode tuning: band = peak × [1/SMART_FFT_PAD, SMART_FFT_PAD], time = SMART_CYCLES / peak.
SMART_FFT_PAD = 5.0
SMART_CYCLES  = 20.0


def _view_for_rate(rate):
    """Return (band_lo, band_hi, time_window_s) for a sample rate.

    Falls back to a Nyquist-spanning view (5 s window, capped to buffer) for
    rates that don't have a hand-tuned entry above.
    """
    view = RATE_VIEW.get(rate)
    if view is not None:
        return view
    nyq = rate * 0.5
    band_lo = MIN_FFT_HZ
    band_hi = nyq
    tw = min(5.0 / band_lo, MAX_POINTS / rate)
    return (band_lo, band_hi, tw)
UPDATE_MS     = 40
AUX_SEND_MS   = 500
ADC_REF       = 3.3
ADC_MAX       = 4095

GEAR_RESISTANCE = {
    0: 1_000     + 100,
    1: 10_000    + 100,
    2: 100_000   + 100,
    3: 1_000_000 + 100,
}
GEAR_LABEL = {0: "1k", 1: "10k", 2: "100k", 3: "1M"}
# Numpy LUT used in the hot path — gear_arr indexes straight in.
GEAR_R_LUT = np.array(
    [GEAR_RESISTANCE[i] for i in range(4)], dtype=np.float32
)


# --- Theme -------------------------------------------------------------------
THEME = {
    "bg":      "#0b0e14",
    "panel":   "#11151c",
    "grid":    "#1f2937",
    "fg":      "#e6edf3",
    "muted":   "#7d8590",
    "accent":  "#06b6d4",
    "accent2": "#ec4899",
    "warn":    "#fbbf24",
    "ok":      "#22c55e",
    "err":     "#ef4444",
    "stop":    "#f87171",
}


# --- Serial helpers ----------------------------------------------------------
def auto_detect_port():
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        if "usbmodem" in p.device or "pico" in desc or "rp2040" in desc:
            return p.device
    return None


def open_serial():
    port = PORT or auto_detect_port()
    if not port:
        print("Could not find a Pico serial port. Set PORT explicitly.", file=sys.stderr)
        sys.exit(1)
    try:
        return serial.Serial(port, BAUD, timeout=0), port
    except Exception as e:
        print(f"Could not open {port}: {e}", file=sys.stderr)
        sys.exit(1)


# --- Binary stream parser ----------------------------------------------------
class StreamParser:
    """Eats raw bytes; emits on_samples(ranges, adcs) and on_status(line).

    The fast path bulk-decodes aligned 3-byte frames with numpy. Byte-by-byte
    resync only kicks in when alignment breaks (rare in practice).
    """

    def __init__(self, on_samples, on_status):
        self._buf = bytearray()
        self._on_samples = on_samples
        self._on_status = on_status

    def feed(self, data):
        if not data:
            return
        self._buf.extend(data)
        buf = self._buf

        i = 0
        n = len(buf)
        while i < n:
            b0 = buf[i]

            if b0 == 0x23:  # '#' -> ASCII status line, read to '\n'
                nl = buf.find(b"\n", i)
                if nl < 0:
                    break
                line = bytes(buf[i:nl]).decode("ascii", errors="ignore")
                self._on_status(line)
                i = nl + 1
                continue

            if b0 == 0x0A or b0 == 0x0D:
                i += 1
                continue

            if (b0 & 0xF0) == 0xA0 and n - i >= 3:
                consumed = self._bulk_parse(buf, i, n)
                if consumed > 0:
                    i += consumed
                    continue

            i += 1  # resync

        del buf[:i]

    def _bulk_parse(self, buf, start, n):
        """Decode aligned 3-byte frames from buf[start:]. Returns bytes consumed."""
        avail = n - start
        n_frames = avail // 3
        if n_frames <= 0:
            return 0

        # Zero-copy view over the bytearray slice — np.frombuffer accepts the
        # buffer protocol directly, no intermediate bytes() allocation. The
        # derived ranges/adcs arrays below are fresh allocations so the view
        # doesn't outlive this method.
        view = memoryview(buf)[start:start + n_frames * 3]
        arr = np.frombuffer(view, dtype=np.uint8)
        b0 = arr[0::3]
        b2 = arr[2::3]
        valid = ((b0 & 0xF0) == 0xA0) & ((b2 & 0xF0) == 0x00)
        if not bool(valid[0]):
            return 0

        if valid.all():
            good = n_frames
        else:
            good = int(np.argmin(valid))
            if good == 0:
                return 0

        ranges = b0[:good] & 0x0F
        b1 = arr[1::3][:good].astype(np.uint16)
        adcs = b1 | (b2[:good].astype(np.uint16) << 8)
        self._on_samples(ranges, adcs)
        return good * 3


# --- Unit helpers ------------------------------------------------------------
def auto_current_unit(i_max_abs):
    if i_max_abs <= 0 or not np.isfinite(i_max_abs):
        return "nA", 1e9
    if i_max_abs < 1e-9:
        return "pA", 1e12
    if i_max_abs < 1e-6:
        return "nA", 1e9
    if i_max_abs < 1e-3:
        return "uA", 1e6
    return "mA", 1e3


def format_range_idx(idx):
    if idx is None:
        return "Range: ?"
    return f"Range: {GEAR_LABEL.get(idx, '?')}"


# --- CSV export --------------------------------------------------------------
def export_csv(sample_indices, raws, gears, sample_rate):
    n = len(raws)
    if n == 0:
        print("Nothing to export (buffer empty).")
        return None
    fname = datetime.now().strftime("flicker_%Y%m%d_%H%M%S.csv")
    sidx = np.asarray(sample_indices, dtype=np.float64)
    raw_arr  = np.asarray(raws, dtype=np.int32)
    gear_arr = np.asarray(gears, dtype=np.int32)
    t_ms = (sidx - sidx[-1]) * (1000.0 / sample_rate)
    v = raw_arr * (ADC_REF / ADC_MAX)
    r = np.array([GEAR_RESISTANCE[int(g)] for g in gear_arr], dtype=np.float64)
    i = v / r
    with open(fname, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample_idx", "t_ms", "range_label", "range_ohms",
                    "adc_raw", "voltage_V", "current_A"])
        for k in range(n):
            w.writerow([
                int(sidx[k]),
                f"{t_ms[k]:.4f}",
                GEAR_LABEL.get(int(gear_arr[k]), "?"),
                int(r[k]),
                int(raw_arr[k]),
                f"{v[k]:.6f}",
                f"{i[k]:.9e}",
            ])
    print(f"saved {fname}  ({n} samples)")
    return fname


# --- Main window -------------------------------------------------------------
class FlickerMonitor(QtWidgets.QMainWindow):
    def __init__(self, ser, port):
        super().__init__()
        self.ser = ser
        self.port = port

        self.sample_indices = deque(maxlen=MAX_POINTS)
        self.raws  = deque(maxlen=MAX_POINTS)
        self.gears = deque(maxlen=MAX_POINTS)

        self.mode          = "?"
        self.sample_rate   = PICO_SAMPLE_HZ
        self.running       = True
        self.last_idx      = None
        self.sample_count  = 0
        self.pending_gap   = 0
        self.last_data     = 0.0
        self.log_y         = False
        self.smart_mode    = False
        self.last_aux_send = 0.0
        self.last_peak_hz  = 0.0
        self.last_xrange_w = 0.0
        self.last_fft_xlo  = -1.0
        self.last_fft_xhi  = -1.0

        # Diagnostics
        self.diag_bytes      = 0
        self.diag_samples    = 0
        self.diag_last       = time.time()
        self.diag_render_acc = 0.0
        self.diag_compute_acc = 0.0
        self.diag_draw_acc    = 0.0
        self.diag_render_n   = 0

        # Cached hanning windows keyed by length — np.hanning is cheap but
        # we hit it every frame, and skipping the allocation matters more on
        # macOS where each ms of compute steals from the Qt event loop.
        self._hann_cache = {}

        self._build_ui()
        self._wire_keys()

        self.parser = StreamParser(self._on_samples, self._on_status)

        self.stop_event = threading.Event()
        self.reader = threading.Thread(
            target=self._reader_loop, name="serial-reader", daemon=True,
        )
        self.reader.start()

        self.send_cmd("m")

        # Reschedule the next tick after the current one finishes, in
        # PreciseTimer mode. This avoids Qt's macOS-side coalescing (which
        # silently throttles repeating QTimers to a few Hz under load) and
        # guarantees the event loop gets to process keyboard / mouse events
        # between updates.
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(UPDATE_MS)

    # ----- UI construction --------------------------------------------------
    def _build_ui(self):
        pg.setConfigOption("background", THEME["bg"])
        pg.setConfigOption("foreground", THEME["fg"])
        pg.setConfigOption("antialias", True)

        self.setWindowTitle("Flicker Monitor")
        self.resize(1200, 800)
        self.setStyleSheet(
            f"QMainWindow {{ background: {THEME['bg']}; }}"
            f"QLabel {{ color: {THEME['fg']}; font-size: 11pt; }}"
        )

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Title + help row
        title_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Flicker Monitor")
        title.setStyleSheet(f"color: {THEME['fg']}; font-size: 14pt; font-weight: bold;")
        help_lbl = QtWidgets.QLabel(
            "[space]Run/Stop  [r]rate+view  [a]smart  [e]CSV  [z]reset  [l]log-Y  [c]clear  [q]quit"
        )
        help_lbl.setStyleSheet(f"color: {THEME['muted']}; font-size: 9pt;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(help_lbl)
        layout.addLayout(title_row)

        # HUD row
        hud_row = QtWidgets.QHBoxLayout()
        self.lbl_port  = QtWidgets.QLabel(f"PORT  {self.port}")
        self.lbl_mode  = QtWidgets.QLabel("MODE  ?")
        self.lbl_state = QtWidgets.QLabel("● RUN")
        self.lbl_rate  = QtWidgets.QLabel("Fs ?  N=0")
        self.lbl_range = QtWidgets.QLabel("Range: ?")
        self.lbl_peak  = QtWidgets.QLabel("waiting for data…")

        self.lbl_port.setStyleSheet(f"color: {THEME['fg']}; font-weight: bold;")
        self.lbl_mode.setStyleSheet(f"color: {THEME['accent']}; font-weight: bold;")
        self.lbl_state.setStyleSheet(f"color: {THEME['ok']}; font-weight: bold;")
        self.lbl_range.setStyleSheet(f"color: {THEME['warn']};")
        self.lbl_peak.setStyleSheet(f"color: {THEME['ok']}; font-weight: bold;")

        for w in (self.lbl_port, self.lbl_mode, self.lbl_state,
                  self.lbl_rate, self.lbl_range):
            hud_row.addWidget(w)
            hud_row.addSpacing(16)
        hud_row.addStretch(1)
        hud_row.addWidget(self.lbl_peak)
        layout.addLayout(hud_row)

        # Plots
        self.plot_time = pg.PlotWidget()
        self.plot_freq = pg.PlotWidget()
        for plt in (self.plot_time, self.plot_freq):
            plt.setBackground(THEME["panel"])
            plt.showGrid(x=True, y=True, alpha=0.25)
            plt.getAxis("left").setPen(pg.mkPen(THEME["muted"]))
            plt.getAxis("bottom").setPen(pg.mkPen(THEME["muted"]))
            plt.getAxis("left").setTextPen(pg.mkPen(THEME["muted"]))
            plt.getAxis("bottom").setTextPen(pg.mkPen(THEME["muted"]))
            # Auto-decimate to the viewport's pixel width and skip drawing
            # off-screen points. Huge win when the buffer has more samples
            # than the plot has pixels (i.e. nearly always at high rates).
            plt.setDownsampling(auto=True, mode="peak")
            plt.setClipToView(True)

        self.plot_time.setTitle("Photodiode current (gear-stitched)",
                                color=THEME["fg"], size="11pt")
        self.plot_time.setLabel("bottom", "Time (ms)")
        self.plot_time.setLabel("left", "Current")
        self.curve_time = self.plot_time.plot(
            pen=pg.mkPen(THEME["accent"], width=1.4),
            antialias=True,
        )

        self.plot_freq.setTitle("FFT magnitude spectrum (current)",
                                color=THEME["fg"], size="11pt")
        self.plot_freq.setLabel("bottom", "Frequency (Hz)")
        self.plot_freq.setLabel("left", "Magnitude")
        self.curve_freq = self.plot_freq.plot(
            pen=pg.mkPen(THEME["accent2"], width=1.6),
            antialias=True,
        )

        # Reference frequency markers — pyqtgraph InfiniteLine survives view
        # changes without redrawing, and the labels follow the line.
        self.ref_lines = []
        for f0 in (1, 2, 5, 10, 60, 120, 180, 240, 480, 1000, 2000, 4000):
            ln = pg.InfiniteLine(
                pos=f0, angle=90, movable=False,
                pen=pg.mkPen(THEME["muted"], width=0.8, style=QtCore.Qt.DotLine),
                label=f"{f0}",
                labelOpts={"position": 0.95, "color": THEME["muted"],
                           "fill": (0, 0, 0, 0), "movable": False},
            )
            self.plot_freq.addItem(ln, ignoreBounds=True)
            self.ref_lines.append(ln)

        # Peak marker on FFT
        self.peak_marker = pg.ScatterPlotItem(
            size=10, brush=pg.mkBrush(THEME["warn"]),
            pen=pg.mkPen(THEME["bg"], width=1.2),
        )
        self.plot_freq.addItem(self.peak_marker)
        self.peak_label = pg.TextItem(color=THEME["warn"], anchor=(0, 1))
        self.plot_freq.addItem(self.peak_label)

        # Gear-switch markers on time plot — pre-allocated, hidden until used.
        self.gear_lines = []
        for _ in range(30):
            ln = pg.InfiniteLine(
                pos=0, angle=90, movable=False,
                pen=pg.mkPen(THEME["warn"], width=0.8, style=QtCore.Qt.DashLine),
            )
            ln.setVisible(False)
            self.plot_time.addItem(ln, ignoreBounds=True)
            self.gear_lines.append(ln)

        # Initial FFT x-range from the active rate's view tuple.
        band_lo0, band_hi0, _ = _view_for_rate(self.sample_rate)
        self.plot_freq.setXRange(band_lo0, band_hi0, padding=0)
        self.plot_freq.setYRange(0, 0.05, padding=0)
        self.last_fft_xlo = band_lo0
        self.last_fft_xhi = band_hi0

        # Both plots get auto-range OFF on Y so we can drive it explicitly with
        # hysteresis (avoids constant wiggle), but ON for X is fine.
        self.plot_time.enableAutoRange(axis="y", enable=False)
        self.plot_freq.enableAutoRange(axis="y", enable=False)
        self.plot_time.enableAutoRange(axis="x", enable=True)

        # Stack the two plots
        layout.addWidget(self.plot_time, 1)
        layout.addWidget(self.plot_freq, 1)

    def _wire_keys(self):
        # Capture keys at the window level so plots having focus still receive.
        self.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.KeyPress:
            self._handle_key(event)
            return True
        return super().eventFilter(obj, event)

    def _handle_key(self, event):
        k = event.key()
        text = (event.text() or "").lower()
        if k == QtCore.Qt.Key_Space:
            self.running = not self.running
            print("RUN" if self.running else "STOP")
        elif text == "m":
            self.send_cmd("m")
        elif text == "d":
            self.send_cmd("d")
            self.raws.clear(); self.gears.clear(); self.sample_indices.clear()
        elif text == "e":
            export_csv(self.sample_indices, self.raws, self.gears, self.sample_rate)
        elif text == "r":
            self.send_cmd("r")
            print("-> cycle rate")
        elif text == "z":
            self.plot_time.enableAutoRange(axis="xy", enable=True)
            self.plot_freq.enableAutoRange(axis="y", enable=True)
            self.last_fft_xhi = -1.0
            print("zoom reset")
        elif text == "a":
            self.smart_mode = not self.smart_mode
            self.last_fft_xlo = -1.0  # force xrange refit on next frame
            self.last_fft_xhi = -1.0
            print(f"smart mode -> {'ON' if self.smart_mode else 'OFF'}")
        elif text == "l":
            self.log_y = not self.log_y
            self.plot_time.setLogMode(y=self.log_y)
            print(f"log Y -> {self.log_y}")
        elif text == "c":
            self.raws.clear(); self.gears.clear(); self.sample_indices.clear()
            self.sample_count = 0
            print("buffer cleared")
        elif text == "q":
            self.close()

    # ----- Serial I/O -------------------------------------------------------
    def send_cmd(self, c):
        try:
            self.ser.write(c.encode())
            self.ser.flush()
        except Exception as e:
            print(f"Serial write failed: {e}", file=sys.stderr)

    def send_aux(self, line1, line2):
        try:
            self.ser.write(f"@1 {line1}\n@2 {line2}\n".encode())
        except Exception:
            pass

    def _reader_loop(self):
        while not self.stop_event.is_set():
            try:
                avail = self.ser.in_waiting
                if avail:
                    data = self.ser.read(avail)
                    self.diag_bytes += len(data)
                    self.parser.feed(data)
                else:
                    time.sleep(0.001)
            except Exception as e:
                print(f"Serial read failed: {e}", file=sys.stderr)
                break

    def _on_samples(self, ranges, adcs):
        n = len(adcs)
        if n == 0:
            return
        self.diag_samples += n
        if not self.running:
            return
        base = self.sample_count + self.pending_gap
        self.pending_gap = 0
        # range(...) gives a C-level iterable for deque.extend — much faster
        # than per-sample appends (n × Python call overhead).
        self.sample_indices.extend(range(base + 1, base + 1 + n))
        self.raws.extend(adcs.tolist())
        self.gears.extend(ranges.tolist())
        self.sample_count = base + n
        self.last_idx = int(ranges[-1])
        self.last_data = time.time()

    def _on_status(self, line):
        line = line.strip()
        if line.startswith("# GAP="):
            try:
                self.pending_gap += int(line[6:].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("# MODE="):
            self.mode = line.split("=", 1)[1].strip()
        elif line.startswith("# RATE="):
            try:
                new_rate = int(line.split("=", 1)[1].strip())
                if new_rate != self.sample_rate:
                    self.sample_rate = new_rate
                    self.raws.clear(); self.gears.clear()
                    self.sample_indices.clear()
                    self.sample_count = 0
                    self.pending_gap = 0
                    self.last_fft_xlo = -1.0   # force xrange refit
                    self.last_fft_xhi = -1.0
                    lo, hi, tw = _view_for_rate(new_rate)
                    print(
                        f"rate -> {new_rate} Hz  FFT {lo:g}-{hi:g} Hz  "
                        f"time {tw*1000:.2f} ms  (buffer cleared)",
                        flush=True,
                    )
                else:
                    self.sample_rate = new_rate
            except ValueError:
                pass
        else:
            print(line)

    # ----- View selection ---------------------------------------------------
    def _current_view(self, fs, n):
        """Return (band_lo, band_hi, time_window_s) for the active mode.

        Smart mode centres on the last detected peak; otherwise the view is
        whatever RATE_VIEW says for the current sample rate. Time window is
        clamped to what the buffer actually holds (n / fs).
        """
        nyq = fs * 0.5
        buf_s = n / fs if n > 0 else 0.0

        if self.smart_mode and self.last_peak_hz > MIN_FFT_HZ:
            lo = max(self.last_peak_hz / SMART_FFT_PAD, MIN_FFT_HZ)
            hi = min(self.last_peak_hz * SMART_FFT_PAD, nyq)
            target_tw = SMART_CYCLES / self.last_peak_hz
            tw = min(max(target_tw, 0.001), buf_s) if buf_s > 0 else target_tw
            return lo, hi, tw

        lo, hi, tw = _view_for_rate(int(fs))
        hi = min(hi, nyq)
        if buf_s > 0:
            tw = min(tw, buf_s)
        return lo, hi, tw

    # ----- Per-frame update -------------------------------------------------
    def _tick(self):
        try:
            self._update()
        finally:
            # Always reschedule, even if _update raised.
            self.timer.start(UPDATE_MS)

    def _update(self):
        self._draw_t = 0.0
        t_start = time.perf_counter()
        try:
            self._update_body()
        finally:
            dt_total = time.perf_counter() - t_start
            self.diag_render_acc  += dt_total
            self.diag_draw_acc    += self._draw_t
            self.diag_compute_acc += max(dt_total - self._draw_t, 0.0)
            self.diag_render_n    += 1
            now = time.time()
            if now - self.diag_last >= 1.0:
                dt = now - self.diag_last
                bps    = self.diag_bytes   / dt
                sps    = self.diag_samples / dt
                nfr    = max(self.diag_render_n, 1)
                avgren = (self.diag_render_acc  / nfr) * 1000.0
                avgcom = (self.diag_compute_acc / nfr) * 1000.0
                avgdrw = (self.diag_draw_acc    / nfr) * 1000.0
                try:
                    backlog = self.ser.in_waiting
                except Exception:
                    backlog = -1
                print(
                    f"[diag] {bps:8.0f} B/s  {sps:6.0f} samp/s  "
                    f"render {avgren:5.1f} ms "
                    f"(compute {avgcom:5.1f} / draw {avgdrw:5.1f})  "
                    f"n={self.diag_render_n}  "
                    f"in_waiting={backlog} B  "
                    f"buf={len(self.raws)}",
                    flush=True,
                )
                self.diag_bytes       = 0
                self.diag_samples     = 0
                self.diag_render_acc  = 0.0
                self.diag_compute_acc = 0.0
                self.diag_draw_acc    = 0.0
                self.diag_render_n    = 0
                self.diag_last        = now

    def _update_body(self):
        self._draw_t = 0.0  # accumulated time inside pyqtgraph setData calls
        # HUD
        self.lbl_mode.setText(f"MODE  {self.mode}")
        self.lbl_range.setText(format_range_idx(self.last_idx))
        smart_tag = "  [SMART]" if self.smart_mode else ""
        if self.running:
            self.lbl_state.setText(f"● RUN{smart_tag}")
            self.lbl_state.setStyleSheet(f"color: {THEME['ok']}; font-weight: bold;")
        else:
            self.lbl_state.setText(f"■ STOP{smart_tag}")
            self.lbl_state.setStyleSheet(f"color: {THEME['stop']}; font-weight: bold;")

        stale = (time.time() - self.last_data) > 1.0 if self.last_data else True
        if stale and self.running:
            self.lbl_mode.setStyleSheet(f"color: {THEME['err']}; font-weight: bold;")
            self.lbl_peak.setText("waiting for data…")
            self.lbl_peak.setStyleSheet(f"color: {THEME['muted']};")
        else:
            self.lbl_mode.setStyleSheet(f"color: {THEME['accent']}; font-weight: bold;")

        n = len(self.raws)
        if n < 64:
            return

        t_compute0 = time.perf_counter()
        raw_arr  = np.asarray(self.raws, dtype=np.float32)
        gear_arr = np.asarray(self.gears, dtype=np.int32)
        sidx     = np.asarray(self.sample_indices, dtype=np.float64)
        v_arr    = raw_arr * (ADC_REF / ADC_MAX)
        # LUT lookup replaces an 8192-iter Python list-comp every frame.
        r_arr    = GEAR_R_LUT[gear_arr]
        i_arr    = v_arr / r_arr

        nominal_fs = float(self.sample_rate)
        t_arr = (sidx - sidx[-1]) * (1000.0 / nominal_fs)
        span_s = (sidx[-1] - sidx[0]) / nominal_fs
        fs_eff = (n - 1) / span_s if span_s > 0 else nominal_fs

        i_peak_abs = float(np.max(np.abs(i_arr)))
        unit_label, unit_scale = auto_current_unit(i_peak_abs)
        i_disp = i_arr * unit_scale

        # Pick FFT band + time window; slice the time-domain plot to the chosen
        # window so high sample rates don't drag the renderer (FFT below still
        # uses the full buffer for resolution).
        band_lo, band_hi, tw_s = self._current_view(nominal_fs, n)
        if tw_s > 0:
            # n_show floor of 16 — at very high rates the window may only hold
            # ~20 samples (e.g. 0.625 ms @ 32 kHz); going below ~16 makes the
            # plot look empty.
            n_show = min(n, max(int(tw_s * nominal_fs), 16))
        else:
            n_show = n
        start = n - n_show
        t_show = t_arr[start:]
        i_show = i_disp[start:]

        td0 = time.perf_counter()
        self.curve_time.setData(t_show, i_show)
        self.plot_time.setLabel("left", f"Current ({unit_label})")
        self._draw_t += time.perf_counter() - td0

        # Gear-switch markers — only those falling inside the visible window.
        if n > 1:
            diffs = np.where(np.diff(gear_arr) != 0)[0] + 1
            diffs = diffs[diffs >= start]
            visible_idxs = diffs[-len(self.gear_lines):]
            for k, ln in enumerate(self.gear_lines):
                if k < len(visible_idxs):
                    x = float(t_arr[visible_idxs[k]])
                    ln.setPos(x)
                    ln.setVisible(True)
                else:
                    ln.setVisible(False)
        else:
            for ln in self.gear_lines:
                ln.setVisible(False)

        self.lbl_rate.setText(
            f"Fs nom={nominal_fs:5.0f} Hz  eff={fs_eff:5.0f} Hz  N={n}"
        )

        # FFT
        t_uniform = np.linspace(t_arr[0], t_arr[-1], n)
        i_uniform = np.interp(t_uniform, t_arr, i_arr)
        dt = (t_uniform[1] - t_uniform[0]) / 1000.0
        if dt <= 0:
            return
        sig = i_uniform - i_uniform.mean()
        window = self._hann_cache.get(n)
        if window is None:
            window = np.hanning(n).astype(np.float32)
            self._hann_cache[n] = window
        spectrum = np.fft.rfft(sig * window)
        freqs = np.fft.rfftfreq(n, d=dt)
        mag = np.abs(spectrum) * (2.0 / window.sum())
        mag_disp = mag * unit_scale

        # Band already chosen above via _current_view. Refit xrange when either
        # edge shifts enough to matter (avoids constant micro-rescales in
        # smart mode).
        if (abs(self.last_fft_xhi - band_hi) / max(band_hi, 1.0) > 0.05
                or abs(self.last_fft_xlo - band_lo) / max(band_hi, 1.0) > 0.05):
            self.plot_freq.setXRange(band_lo, band_hi, padding=0)
            self.last_fft_xlo = band_lo
            self.last_fft_xhi = band_hi
        mask = (freqs >= band_lo) & (freqs <= band_hi)
        f_show = freqs[mask]
        m_show = mag_disp[mask]
        td0 = time.perf_counter()
        self.curve_freq.setData(f_show, m_show)
        self.plot_freq.setLabel("left", f"Magnitude ({unit_label})")
        if m_show.size:
            self.plot_freq.setYRange(0.0, float(m_show.max()) * 1.15, padding=0)
        self._draw_t += time.perf_counter() - td0

        # Time-domain Y autoscale on the visible window (Qt redraws are atomic
        # — no flash on macOS, so we don't need the matplotlib-era hysteresis).
        if self.log_y:
            pos = i_show[i_show > 0]
            if pos.size:
                self.plot_time.setYRange(
                    np.log10(max(pos.min() * 0.5, 1e-12)),
                    np.log10(pos.max() * 2.0),
                    padding=0,
                )
        else:
            lo = float(i_show.min())
            hi = float(i_show.max())
            pad = max((hi - lo) * 0.1, 1e-9)
            self.plot_time.setYRange(lo - pad, hi + pad, padding=0)

        # Peak find — within the current band so the peak label reflects what
        # the user actually sees on the FFT plot.
        f_peak = 0.0
        a_peak = 0.0
        search = (freqs >= max(band_lo, MIN_FFT_HZ)) & (freqs <= band_hi)
        if search.any():
            sub = mag_disp[search]
            idx = int(np.argmax(sub))
            f_peak = float(freqs[search][idx])
            a_peak = float(sub[idx])
            global_idx = int(np.where(freqs == f_peak)[0][0])
            if 1 <= global_idx < len(mag_disp) - 1:
                a = mag_disp[global_idx - 1]
                b = mag_disp[global_idx]
                c = mag_disp[global_idx + 1]
                denom = (a - 2 * b + c)
                if denom != 0:
                    delta = 0.5 * (a - c) / denom
                    f_peak = f_peak + delta * (1.0 / (dt * n))
            self.peak_marker.setData([f_peak], [a_peak])
            self.peak_label.setPos(f_peak, a_peak)
            self.peak_label.setText(f"  {f_peak:.1f} Hz")
            self.lbl_peak.setText(
                f"PEAK  {f_peak:7.2f} Hz   {a_peak:7.3f} {unit_label}"
            )
            self.lbl_peak.setStyleSheet(f"color: {THEME['ok']}; font-weight: bold;")
            self.last_peak_hz = f_peak

        if self.running and self.mode == "MON":
            now = time.time()
            if (now - self.last_aux_send) * 1000.0 > AUX_SEND_MS:
                self.last_aux_send = now
                i_mean = float(np.mean(i_arr))
                u_lbl, u_scl = auto_current_unit(abs(i_mean))
                line1 = f"I:{i_mean * u_scl:.2f}{u_lbl}"[:8]
                line2 = f"F:{f_peak:.0f}Hz"[:8]
                self.send_aux(line1, line2)

    # ----- Shutdown ---------------------------------------------------------
    def closeEvent(self, event):
        self.stop_event.set()
        self.reader.join(timeout=1.0)
        try:
            self.send_cmd("d")
            self.ser.close()
        except Exception:
            pass
        print("Disconnected.")
        super().closeEvent(event)


def main():
    ap = argparse.ArgumentParser(description="Flicker Monitor host dashboard.")
    ap.add_argument("--demo", action="store_true",
                    help="Use a synthetic data source instead of a real Pico. "
                         "Emits 5 µA DC + 1 µA 3 Hz + 0.3 µA 60 Hz + 0.1 µA "
                         "120 Hz + noise on the 100k gear.")
    ap.add_argument("--scenario", default="default",
                    choices=["default", "mains", "audio", "ultrasonic", "sweep"],
                    help="Demo signal scenario (only with --demo). "
                         "'mains' = 60/120 Hz strong, 'audio' = 440 Hz tone, "
                         "'ultrasonic' = 4 kHz tone, 'sweep' = 1 Hz -> 2 kHz "
                         "sweep over 20 s. Use this to exercise the smart "
                         "view ('a') across the spectrum.")
    args = ap.parse_args()

    if args.demo:
        ser = FakeSerial(scenario=args.scenario)
        port = f"demo://{args.scenario}"
        print(f"DEMO mode — synthetic Pico stream (scenario={args.scenario}).")
    else:
        ser, port = open_serial()
        print(f"Connected on {port}.  Auto-sending 'm' to start streaming.")

    app = QtWidgets.QApplication(sys.argv)
    win = FlickerMonitor(ser, port)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
