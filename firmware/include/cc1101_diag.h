#pragma once
#include <Arduino.h>

struct CC1101DiagResult {
    bool spi_miso_ready;       // CSN low -> MISO goes low (CHIP_RDYn)
    uint8_t partnum;           // Expected: 0x00
    uint8_t version;           // Expected: 0x14 or 0x04
    bool spi_identity_ok;      // Valid PARTNUM & VERSION read over SPI
    bool register_rw_ok;       // Write & read-back verification on IOCFG0
    bool gdo0_toggle_ok;       // Physical toggle test on D4 (GPIO2 / Blue wire)
    bool gdo2_toggle_ok;       // Physical toggle test on RX (GPIO3 / Grey wire)
    uint8_t marcstate;         // State machine register after RX init

    bool allRequiredPassed() const {
        return spi_miso_ready && spi_identity_ok && register_rw_ok && gdo0_toggle_ok;
    }
};

class CC1101Diagnostics {
public:
    // Run full wire-by-wire hardware diagnostic test
    static CC1101DiagResult runSelfTest();

    // Print detailed human-readable diagnostic table to Serial
    static void printReport(const CC1101DiagResult& res);

    // Bounded capture wrapper; SubGhzEngine::begin() must have succeeded first.
    static bool startLiveMonitor(float freqMhz = 433.92f);

    // Poll the unified capture engine (no separate diagnostic ISR).
    static void loopLiveMonitor();

    // Put GDO2 (GPIO3 / RX) into High-Impedance so USB-UART flashing never conflicts
    static void setGdo2HighImpedance();

private:
    static uint8_t readReg(uint8_t addr);
    static uint8_t readStatusReg(uint8_t addr);
    static void writeReg(uint8_t addr, uint8_t value);
    static void writeStrobe(uint8_t strobe);
    static bool resetChip();
};
