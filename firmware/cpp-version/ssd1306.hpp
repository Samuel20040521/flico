// Minimal SSD1306 128x32 I2C driver.
//
// Mirrors the subset of the MicroPython ssd1306 API the flicker detector
// uses: fill / pixel / fill_rect / text (with optional integer scaling) /
// show. The framebuffer is held in RAM; show() pushes the whole 512-byte
// buffer to the panel in one I2C transaction (~12-15 ms at 400 kHz).

#pragma once

#include <cstdint>
#include "hardware/i2c.h"

class SSD1306 {
public:
    static constexpr int WIDTH  = 128;
    static constexpr int HEIGHT = 32;
    static constexpr int PAGES  = HEIGHT / 8;          // 4 pages
    static constexpr int BUF_LEN = WIDTH * PAGES;      // 512 bytes

    SSD1306(i2c_inst_t* i2c, uint8_t addr = 0x3C)
        : i2c_(i2c), addr_(addr) {}

    void init();
    void show();

    void fill(bool on);
    void pixel(int x, int y, bool on);
    void fill_rect(int x, int y, int w, int h, bool on);

    // Draws an 8x8 ASCII string. `scale` blows each pixel up to a
    // `scale x scale` block — handy for the OLED's small format.
    void text(const char* s, int x, int y, int scale = 1, bool on = true);

private:
    void cmd(uint8_t c);
    void cmd2(uint8_t c0, uint8_t c1);
    void cmd3(uint8_t c0, uint8_t c1, uint8_t c2);

    i2c_inst_t* i2c_;
    uint8_t     addr_;
    uint8_t     buf_[BUF_LEN]{};
};
