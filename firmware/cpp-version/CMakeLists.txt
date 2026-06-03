# == DO NOT EDIT THE FOLLOWING LINES for the Raspberry Pi Pico VS Code Extension to work ==
if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.2.0)
set(toolchainVersion 13_2_Rel1)
set(picotoolVersion 2.2.0-a4)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()
# ====================================================================================
cmake_minimum_required(VERSION 3.13)

# Pull the Pico SDK in. PICO_SDK_PATH must be exported, or the Raspberry Pi
# Pico VS Code extension will inject it when it runs the build.
include(pico_sdk_import.cmake)

project(pico_flicker_cpp C CXX ASM)
set(CMAKE_C_STANDARD   11)
set(CMAKE_CXX_STANDARD 17)

if (NOT DEFINED PICO_BOARD)
    set(PICO_BOARD pico)
endif ()

pico_sdk_init()

add_executable(pico_flicker
    main.cpp
    ssd1306.cpp
)

target_link_libraries(pico_flicker
    pico_stdlib
    hardware_adc
    hardware_dma
    hardware_i2c
    hardware_gpio
)

# stdio over USB CDC (printf, fwrite go through the same /dev/cu.usbmodem* the
# MicroPython build used).
pico_enable_stdio_usb(pico_flicker  1)
pico_enable_stdio_uart(pico_flicker 0)

# CRITICAL: disable CRLF translation. By default pico-sdk's stdio_usb inflates
# any '\n' (0x0A) in the output stream to '\r\n' (0x0D 0x0A) — handy for
# terminals, catastrophic for binary streaming. We send 3-byte frames where
# the middle bytes can legitimately be 0x0A; if they get expanded, the host
# parser's sync check accidentally validates [0xA?, 0x0D, 0x0A] as a frame
# and decodes adc = 0xA0D = 2573 every time a sample's low byte equals 0x0A.
# See README / the bug investigation for details.
target_compile_definitions(pico_flicker PRIVATE
    PICO_STDIO_USB_DEFAULT_CRLF=0
    PICO_STDIO_UART_DEFAULT_CRLF=0
)

pico_add_extra_outputs(pico_flicker)
