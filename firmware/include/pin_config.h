#pragma once
#include <Arduino.h>

// ============================================================================
// CC1101 V2.0 (8-pin) <-> NodeMCU (ESP8266MOD) Wiring Configuration
// ============================================================================
// Pin 1: GND  (Black)  -> NodeMCU GND
// Pin 2: VCC  (Red)    -> NodeMCU 3V3 (3.3V ONLY)
// Pin 3: GDO0 (Blue)   -> NodeMCU D4  (GPIO2)
// Pin 4: CSN  (Pink)   -> NodeMCU D8  (GPIO15)
// Pin 5: SCK  (Green)  -> NodeMCU D5  (GPIO14)
// Pin 6: MOSI (Orange) -> NodeMCU D7  (GPIO13)
// Pin 7: MISO (Yellow) -> NodeMCU D6  (GPIO12)
// Pin 8: GDO2 (Grey)   -> NodeMCU RX  (GPIO3)
// ============================================================================

constexpr uint8_t PIN_CC1101_SCK  = 14; // D5 (Green)  - Hardware SPI SCK
constexpr uint8_t PIN_CC1101_MISO = 12; // D6 (Yellow) - Hardware SPI MISO (SO)
constexpr uint8_t PIN_CC1101_MOSI = 13; // D7 (Orange) - Hardware SPI MOSI (SI)
constexpr uint8_t PIN_CC1101_CSN  = 15; // D8 (Pink)   - Hardware SPI CSN (SS)

constexpr uint8_t PIN_CC1101_GDO0 = 2;  // D4 (Blue)   - Async TX/RX Data & Interrupts
constexpr uint8_t PIN_CC1101_GDO2 = 3;  // RX (Grey)   - Secondary GDO2 (shared with UART0 RX)

constexpr float DEFAULT_SUBGHZ_FREQ_MHZ = 433.92f;
