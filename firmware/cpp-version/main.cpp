// Flicker Detector — Raspberry Pi Pico (RP2040), C++ / pico-sdk port of the
// MicroPython version. Designed for raw speed: ADC is driven by a free-
// running hardware sampler into a circular DMA ring, so the CPU only does
// data shuffling and never waits on a sample.
//
// Streaming rate: LOCKED at 16 kHz (Nyquist 8 kHz). This matches the TIA's
// natural ~16 kHz analog bandwidth, so the host gets clean spectra without
// aliasing slack. Rate cycling ('r') has been removed — read_data.py's RATE
// status line is still emitted once on MON entry so the host stays in sync.
//
// Host protocol is unchanged from the MicroPython version (see read_data.py):
//
//   Status lines  : ASCII, "# MODE=...", "# RATE=...", "# GAP=N\n"
//   Sample frames : 3 bytes
//       byte 0  : 0xA0 | range_index
//       byte 1  : ADC low 8 bits
//       byte 2  : ADC high 4 bits  (top 4 bits always 0)
//   Aux commands  : '@1 <text>\n', '@2 <text>\n' from host to set OLED lines
//   Mode commands : 'd' (DC), 'm' (MON), 'h'/'?' (help)
//
// Pinout matches the MicroPython build:
//   I2C0  SDA = GP4, SCL = GP5     (SSD1306 128x32)
//   ADC0  GP26
//   MUX   S0 = GP10, S1 = GP11    (S2 hardwired to GND -> Y0..Y3 only)

#include <cstdio>
#include <cstdint>
#include <cstring>

#include "pico/stdlib.h"
#include "hardware/adc.h"
#include "hardware/dma.h"
#include "hardware/i2c.h"
#include "hardware/gpio.h"

#include "ssd1306.hpp"


// ===== Pin / hardware mapping ===============================================
static constexpr uint OLED_SDA = 4;
static constexpr uint OLED_SCL = 5;
static constexpr uint ADC_GPIO = 26;         // ADC0
static constexpr uint ADC_CHAN = 0;
static constexpr uint MUX_S0   = 10;
static constexpr uint MUX_S1   = 11;
static i2c_inst_t* const OLED_I2C = i2c0;

// OLED kill switch. When false the panel is not initialised, I2C0 stays
// unconfigured, and the render paths are compiled out. Host-side '@1'/'@2'
// aux commands are still accepted (stored in g_aux) but go nowhere.
static constexpr bool OLED_ENABLED = true;


// ===== Calibration & thresholds =============================================
static constexpr float    ADC_REF     = 3.3f;
static constexpr int      ADC_RAW_MAX = 4095;             // 12-bit
static constexpr float    V_CLIP_HI   = 3.0f;
static constexpr float    V_FLOOR_LO  = 0.2f;
static constexpr uint16_t RAW_CLIP_HI = (uint16_t)((V_CLIP_HI  * ADC_RAW_MAX) / ADC_REF);
static constexpr uint16_t RAW_FLOOR_LO= (uint16_t)((V_FLOOR_LO * ADC_RAW_MAX) / ADC_REF);
static constexpr uint16_t RAW_SAFE_MID= (RAW_CLIP_HI + RAW_FLOOR_LO) / 2;

static constexpr uint32_t SWITCH_COOLDOWN_MS     = 50;
static constexpr uint32_t MON_SWITCH_COOLDOWN_MS = 300;
static constexpr uint32_t MON_OLED_REFRESH_MS    = 500;

// ===== Streaming rate (LOCKED) ==============================================
// 16 kHz matches the TIA's analog roll-off, giving a clean 8 kHz Nyquist with
// no aliasing slack to worry about. read_data.py's RATE_VIEW[16000] window is
// 4–8 kHz FFT band over a 1.25 ms time window.
static constexpr int SAMPLE_HZ = 16000;


// ===== Gear table ===========================================================
struct Gear {
    uint8_t     mux_channel;
    const char* label;
    uint32_t    effective_r;   // ohms incl. ~100 ohm MUX R_on
};
static constexpr Gear GEARS[4] = {
    {0, "1k",   1'000     + 100},
    {1, "10k",  10'000    + 100},
    {2, "100k", 100'000   + 100},
    {3, "1M",   1'000'000 + 100},
};


// ===== DMA ring buffer ======================================================
// Ring DMA needs the buffer aligned to its byte size, and 'ring_size_bits'
// = log2(buffer_bytes). 2048 samples × 2 bytes = 4096 bytes => 12 bits.
static constexpr int RING_SAMPLES = 2048;
static constexpr int RING_BYTES   = RING_SAMPLES * 2;
static constexpr int RING_BITS    = 12;
alignas(RING_BYTES) static uint16_t g_ring[RING_SAMPLES];

static int g_adc_dma_chan;
static absolute_time_t g_adc_start_time;


static inline int dma_write_index() {
    uintptr_t base = (uintptr_t)g_ring;
    uintptr_t wa   = (uintptr_t)dma_channel_hw_addr(g_adc_dma_chan)->write_addr;
    return (int)(((wa - base) >> 1) & (RING_SAMPLES - 1));
}


// True ADC sample count since boot. Anchored to absolute time and the locked
// sample rate, so it ignores software jitter — when we need to announce a
// GAP after a blocking operation, this gives us the honest delta.
static inline uint64_t samples_total() {
    uint64_t us = absolute_time_diff_us(g_adc_start_time, get_absolute_time());
    return (us * (uint64_t)SAMPLE_HZ) / 1'000'000ULL;
}


// ===== Range manager ========================================================
// 200 ms of rolling-peak history at 16 kHz = 3200 samples; ~10 checks/sec.
class RangeManager {
public:
    RangeManager() {
        for (int i = 0; i < PEAK_WINDOW; ++i) peak_buf_[i] = RAW_SAFE_MID;
        apply_();
    }

    int         index()       const { return idx_; }
    const char* label()       const { return GEARS[idx_].label; }
    uint32_t    effective_r() const { return GEARS[idx_].effective_r; }

    // DC-mode auto-range from a single voltage reading.
    bool update_dc(float v) {
        uint32_t now = to_ms_since_boot(get_absolute_time());
        if (now - last_switch_ms_ < SWITCH_COOLDOWN_MS) return false;
        if (v > V_CLIP_HI && idx_ > 0)              { idx_--; apply_(); return true; }
        if (v < V_FLOOR_LO && idx_ < 3)             { idx_++; apply_(); return true; }
        return false;
    }

    // Streaming-mode auto-range from raw 12-bit samples. Decides on the
    // ROLLING PEAK of recent samples so AC troughs don't trigger phantom
    // upshifts. Returns true on the call where it actually toggles MUX.
    bool feed_streaming(uint16_t raw) {
        peak_buf_[peak_idx_++] = raw;
        if (peak_idx_ >= PEAK_WINDOW) peak_idx_ = 0;

        if (++peak_check_n_ < PEAK_CHECK_EVERY) return false;
        peak_check_n_ = 0;

        uint32_t now = to_ms_since_boot(get_absolute_time());
        if (now - last_switch_ms_ < MON_SWITCH_COOLDOWN_MS) return false;

        uint16_t peak = 0;
        for (int i = 0; i < PEAK_WINDOW; ++i) {
            if (peak_buf_[i] > peak) peak = peak_buf_[i];
        }
        bool changed = false;
        if (peak > RAW_CLIP_HI && idx_ > 0)       { idx_--; changed = true; }
        else if (peak < RAW_FLOOR_LO && idx_ < 3) { idx_++; changed = true; }
        if (changed) {
            apply_();
            for (int i = 0; i < PEAK_WINDOW; ++i) peak_buf_[i] = RAW_SAFE_MID;
            peak_idx_     = 0;
            peak_check_n_ = 0;
        }
        return changed;
    }

private:
    void apply_() {
        uint8_t ch = GEARS[idx_].mux_channel;
        gpio_put(MUX_S0,  ch & 0x1);
        gpio_put(MUX_S1, (ch >> 1) & 0x1);
        last_switch_ms_ = to_ms_since_boot(get_absolute_time());
    }

    int      idx_ = 2;                  // start on 100 k
    uint32_t last_switch_ms_ = 0;

    static constexpr int PEAK_WINDOW      = SAMPLE_HZ / 5;   // 200 ms = 3200
    static constexpr int PEAK_CHECK_EVERY = SAMPLE_HZ / 10;  // ~10 Hz = 1600
    uint16_t peak_buf_[PEAK_WINDOW];
    int      peak_idx_         = 0;
    int      peak_check_n_     = 0;
};


// ===== Host -> Pico OLED aux ================================================
struct OledAux {
    char line1[16] = {0};
    char line2[16] = {0};
    bool dirty     = true;
};


// ===== Mode & globals =======================================================
enum Mode { MODE_DC, MODE_MON };
static Mode          g_mode = MODE_DC;
static RangeManager  g_rm;
static OledAux       g_aux;
static SSD1306*      g_oled = nullptr;

static int      g_read_idx       = 0;
static uint64_t g_emitted_count  = 0;


// ===== Outgoing batch buffer ================================================
// fwrite to stdio_usb has fixed overhead per call (tud_task + flush). Batch
// the frames in RAM so we make one fwrite per ~256 bytes instead of one per
// 3 bytes.
static constexpr int TX_BATCH = 384;
static uint8_t       g_tx[TX_BATCH];
static int           g_tx_pos = 0;


static inline void tx_flush() {
    if (g_tx_pos == 0) return;
    if (stdio_usb_connected()) {
        fwrite(g_tx, 1, (size_t)g_tx_pos, stdout);
    }
    g_tx_pos = 0;
}

static inline void tx_push_frame(uint8_t range_idx, uint16_t adc12) {
    if (g_tx_pos > TX_BATCH - 3) tx_flush();
    g_tx[g_tx_pos++] = (uint8_t)(0xA0 | (range_idx & 0x0F));
    g_tx[g_tx_pos++] = (uint8_t)(adc12 & 0xFF);
    g_tx[g_tx_pos++] = (uint8_t)((adc12 >> 8) & 0x0F);
}


// ===== Command parsing ======================================================
// Single-char mode commands ('d', 'm', 'h', '?') plus multi-byte aux
// commands ('@1 <text>\n' / '@2 <text>\n') from read_data.py. The 'r'
// (cycle-rate) command is silently ignored since the rate is locked.
static char  g_aux_line[64];
static int   g_aux_pos    = 0;
static bool  g_in_aux     = false;


static void process_aux_line() {
    g_aux_line[g_aux_pos] = '\0';
    if (g_aux_pos >= 1 && (g_aux_line[0] == '1' || g_aux_line[0] == '2')) {
        const char* text = (g_aux_pos >= 2 && g_aux_line[1] == ' ') ? &g_aux_line[2] : "";
        char* dst = (g_aux_line[0] == '1') ? g_aux.line1 : g_aux.line2;
        std::strncpy(dst, text, sizeof(g_aux.line1) - 1);
        dst[sizeof(g_aux.line1) - 1] = '\0';
        g_aux.dirty = true;
    }
    g_aux_pos = 0;
    g_in_aux  = false;
}


static void print_help() {
    std::printf("# commands: d=DC m=MON h=help | aux: '@1 <text>' '@2 <text>'\n");
    std::printf("# MON: 3-byte frames [0xA0|range, adc_lo, adc_hi] @ %d Hz (locked)\n", SAMPLE_HZ);
}


static void handle_byte(int ch) {
    if (g_in_aux) {
        if (ch == '\n' || ch == '\r') {
            process_aux_line();
        } else if (g_aux_pos < (int)sizeof(g_aux_line) - 1) {
            g_aux_line[g_aux_pos++] = (char)ch;
        }
        return;
    }
    if (ch == '@') {
        g_in_aux = true;
        return;
    }
    char c = (char)ch;
    if (c >= 'A' && c <= 'Z') c = (char)(c + 0x20);
    if (c == 'd') {
        if (g_mode != MODE_DC) {
            g_mode = MODE_DC;
            g_aux.dirty = true;
            std::printf("\n# MODE=DC\n");
        }
    } else if (c == 'm') {
        if (g_mode != MODE_MON) {
            g_mode = MODE_MON;
            g_aux.dirty = true;
            std::printf("\n# MODE=MON\n# RATE=%d\n", SAMPLE_HZ);
        }
    } else if (c == 'r') {
        // Rate is locked at 16 kHz. Re-announce so a host that requested a
        // cycle stays in sync rather than waiting for a RATE that never comes.
        std::printf("\n# RATE=%d\n", SAMPLE_HZ);
    } else if (c == 'h' || c == '?') {
        print_help();
    }
}


static void poll_input() {
    while (true) {
        int ch = getchar_timeout_us(0);
        if (ch == PICO_ERROR_TIMEOUT) break;
        handle_byte(ch);
    }
}


// ===== OLED rendering =======================================================
static void oled_show_dc(float v) {
    char l1[16];
    std::snprintf(l1, sizeof(l1), "%.3fV", v);
    g_oled->fill(false);
    g_oled->text(l1,             0,  0, /*scale=*/2);
    g_oled->text(g_rm.label(),   0, 16, /*scale=*/2);
    g_oled->show();
}


static void oled_show_mon() {
    g_oled->fill(false);
    if (g_aux.line1[0] || g_aux.line2[0]) {
        if (g_aux.line1[0]) g_oled->text(g_aux.line1, 0,  0, 2);
        if (g_aux.line2[0]) g_oled->text(g_aux.line2, 0, 16, 2);
    } else {
        g_oled->text("MON",           0,  0, 2);
        g_oled->text(g_rm.label(),    0, 16, 2);
    }
    g_oled->show();
}


// ===== ADC + DMA setup ======================================================
static void setup_adc_dma() {
    adc_init();
    adc_gpio_init(ADC_GPIO);
    adc_select_input(ADC_CHAN);

    adc_fifo_setup(
        /*en=*/    true,
        /*dreq_en=*/true,
        /*dreq_thresh=*/1,
        /*err_in_fifo=*/false,
        /*byte_shift=*/false
    );

    // Sample rate = 48 MHz / (1 + clkdiv). For 16 kHz: clkdiv = 2999.
    float clkdiv = 48'000'000.0f / (float)SAMPLE_HZ - 1.0f;
    adc_set_clkdiv(clkdiv);

    g_adc_dma_chan = dma_claim_unused_channel(true);
    dma_channel_config cfg = dma_channel_get_default_config(g_adc_dma_chan);
    channel_config_set_transfer_data_size(&cfg, DMA_SIZE_16);
    channel_config_set_read_increment(&cfg, false);
    channel_config_set_write_increment(&cfg, true);
    channel_config_set_dreq(&cfg, DREQ_ADC);
    channel_config_set_ring(&cfg, /*write=*/true, RING_BITS);

    dma_channel_configure(
        g_adc_dma_chan, &cfg,
        g_ring,
        &adc_hw->fifo,
        0xFFFFFFFF,
        true
    );

    g_adc_start_time = get_absolute_time();
    adc_run(true);
}


// ===== Main loop ============================================================
// On a stock Pico/Pico H, GP23 drives the on-board RT6150 SMPS PS pin. Default
// (low) lets the regulator drop into PFM at light load, which is efficient but
// dumps ~10–50 kHz ripple onto 3V3 — right in the band we care about. Pulling
// it high locks the SMPS into PWM, trading a few mA of idle current for a much
// cleaner rail and a quieter ADC floor.
static constexpr uint PICO_SMPS_PS = 23;

int main() {
    stdio_init_all();

    gpio_init(PICO_SMPS_PS);
    gpio_set_dir(PICO_SMPS_PS, GPIO_OUT);
    gpio_put(PICO_SMPS_PS, 1);

    sleep_ms(800);

    SSD1306 oled(OLED_I2C);
    if constexpr (OLED_ENABLED) {
        i2c_init(OLED_I2C, 400'000);
        gpio_set_function(OLED_SDA, GPIO_FUNC_I2C);
        gpio_set_function(OLED_SCL, GPIO_FUNC_I2C);
        gpio_pull_up(OLED_SDA);
        gpio_pull_up(OLED_SCL);
        oled.init();
        g_oled = &oled;
    }

    gpio_init(MUX_S0); gpio_set_dir(MUX_S0, GPIO_OUT);
    gpio_init(MUX_S1); gpio_set_dir(MUX_S1, GPIO_OUT);

    setup_adc_dma();

    std::printf("\n# Flicker Detector ready (C++ / pico-sdk).\n");
    std::printf("# RATE=%d\n", SAMPLE_HZ);
    print_help();

    sleep_ms(SWITCH_COOLDOWN_MS);

    g_read_idx                 = dma_write_index();
    g_emitted_count            = samples_total();
    [[maybe_unused]] absolute_time_t last_oled = get_absolute_time();

    while (true) {
        poll_input();

        if (g_mode == MODE_DC) {
            int wr = dma_write_index();
            int prev = (wr - 1 + RING_SAMPLES) & (RING_SAMPLES - 1);
            uint16_t raw = g_ring[prev] & 0x0FFF;
            float v = (float)raw * (ADC_REF / ADC_RAW_MAX);

            if (g_rm.update_dc(v)) {
                sleep_ms(SWITCH_COOLDOWN_MS);
            }
            if constexpr (OLED_ENABLED) oled_show_dc(v);
            sleep_ms(20);
            g_emitted_count = samples_total();
            g_read_idx      = dma_write_index();
            continue;
        }

        // ---- MON mode ----
        int wr = dma_write_index();
        bool switched = false;

        while (g_read_idx != wr) {
            uint16_t raw = g_ring[g_read_idx] & 0x0FFF;
            tx_push_frame((uint8_t)g_rm.index(), raw);
            g_emitted_count++;

            if (g_rm.feed_streaming(raw)) {
                switched = true;
            }
            g_read_idx = (g_read_idx + 1) & (RING_SAMPLES - 1);

            if (switched) break;
        }
        tx_flush();

        if (switched) {
            sleep_ms(SWITCH_COOLDOWN_MS);
            uint64_t now_total = samples_total();
            uint64_t gap = (now_total > g_emitted_count) ? (now_total - g_emitted_count) : 0;
            std::printf("\n# GAP=%llu\n", (unsigned long long)gap);
            std::fflush(stdout);
            g_emitted_count = now_total;
            g_read_idx      = dma_write_index();
            continue;
        }

        if constexpr (OLED_ENABLED) {
            absolute_time_t now = get_absolute_time();
            if (g_aux.dirty ||
                absolute_time_diff_us(last_oled, now) > MON_OLED_REFRESH_MS * 1000) {
                last_oled = now;
                g_aux.dirty = false;
                oled_show_mon();
            }
        }
    }
}
