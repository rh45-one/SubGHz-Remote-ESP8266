#include "cc1101_diag.h"
#include "pin_config.h"
#include <SPI.h>
#include <ELECHOUSE_CC1101_SRC_DRV.h>
#include "subghz_engine.h"

namespace {
// Register access bitmasks (CC1101_IOCFG2, CC1101_IOCFG0, CC1101_SRES, etc.
// are already provided by ELECHOUSE_CC1101_SRC_DRV.h)
constexpr uint8_t REG_READ_SINGLE = 0x80;
constexpr uint8_t REG_READ_BURST  = 0xC0;

// GDOx Configuration Values (Table 41 in CC1101 Datasheet)
constexpr uint8_t GDO_CFG_HIGH_Z    = 0x2E; // High impedance (3-state)
constexpr uint8_t GDO_CFG_DRIVE_LOW = 0x2F; // HW to 0 (drives pin LOW)
constexpr uint8_t GDO_CFG_DRIVE_HIGH= 0x6F; // HW to 0 + GDOx_INV=1 (drives pin HIGH)

} // namespace

bool CC1101Diagnostics::resetChip() {
    pinMode(PIN_CC1101_CSN, OUTPUT);
    pinMode(PIN_CC1101_SCK, OUTPUT);
    pinMode(PIN_CC1101_MOSI, OUTPUT);
    pinMode(PIN_CC1101_MISO, INPUT);

    // Manual power-on reset sequence per CC1101 datasheet section 19.1.2
    digitalWrite(PIN_CC1101_CSN, HIGH);
    delayMicroseconds(10);
    digitalWrite(PIN_CC1101_CSN, LOW);
    delayMicroseconds(10);
    digitalWrite(PIN_CC1101_CSN, HIGH);
    delayMicroseconds(45);

    digitalWrite(PIN_CC1101_CSN, LOW);
    uint32_t startUs = micros();
    while (digitalRead(PIN_CC1101_MISO) == HIGH) {
        if (micros() - startUs > 50000) { // 50ms timeout
            digitalWrite(PIN_CC1101_CSN, HIGH);
            return false;
        }
    }

    SPI.begin();
    SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
    SPI.transfer(CC1101_SRES);

    startUs = micros();
    while (digitalRead(PIN_CC1101_MISO) == HIGH) {
        if (micros() - startUs > 50000) {
            SPI.endTransaction();
            digitalWrite(PIN_CC1101_CSN, HIGH);
            return false;
        }
    }
    SPI.endTransaction();
    digitalWrite(PIN_CC1101_CSN, HIGH);
    delay(2);
    return true;
}

uint8_t CC1101Diagnostics::readReg(uint8_t addr) {
    digitalWrite(PIN_CC1101_CSN, LOW);
    uint32_t startUs = micros();
    while (digitalRead(PIN_CC1101_MISO) == HIGH && (micros() - startUs < 5000)) {}
    SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
    SPI.transfer(addr | REG_READ_SINGLE);
    uint8_t val = SPI.transfer(0x00);
    SPI.endTransaction();
    digitalWrite(PIN_CC1101_CSN, HIGH);
    return val;
}

uint8_t CC1101Diagnostics::readStatusReg(uint8_t addr) {
    digitalWrite(PIN_CC1101_CSN, LOW);
    uint32_t startUs = micros();
    while (digitalRead(PIN_CC1101_MISO) == HIGH && (micros() - startUs < 5000)) {}
    SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
    SPI.transfer(addr | REG_READ_BURST);
    uint8_t val = SPI.transfer(0x00);
    SPI.endTransaction();
    digitalWrite(PIN_CC1101_CSN, HIGH);
    return val;
}

void CC1101Diagnostics::writeReg(uint8_t addr, uint8_t value) {
    digitalWrite(PIN_CC1101_CSN, LOW);
    uint32_t startUs = micros();
    while (digitalRead(PIN_CC1101_MISO) == HIGH && (micros() - startUs < 5000)) {}
    SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
    SPI.transfer(addr & 0x3F);
    SPI.transfer(value);
    SPI.endTransaction();
    digitalWrite(PIN_CC1101_CSN, HIGH);
}

void CC1101Diagnostics::writeStrobe(uint8_t strobe) {
    digitalWrite(PIN_CC1101_CSN, LOW);
    uint32_t startUs = micros();
    while (digitalRead(PIN_CC1101_MISO) == HIGH && (micros() - startUs < 5000)) {}
    SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
    SPI.transfer(strobe);
    SPI.endTransaction();
    digitalWrite(PIN_CC1101_CSN, HIGH);
}

void CC1101Diagnostics::setGdo2HighImpedance() {
    writeReg(CC1101_IOCFG2, GDO_CFG_HIGH_Z);
}

CC1101DiagResult CC1101Diagnostics::runSelfTest() {
    CC1101DiagResult res = {};
    detachInterrupt(digitalPinToInterrupt(PIN_CC1101_GDO0));
    pinMode(PIN_CC1101_GDO0, INPUT);

    // 1. Test SPI Reset & MISO Ready (CHIP_RDYn)
    res.spi_miso_ready = resetChip();

    // Immediately set GDO2 to High-Z so GPIO3 (RX) doesn't fight USB-UART
    setGdo2HighImpedance();
    writeStrobe(CC1101_SIDLE);
    writeReg(CC1101_IOCFG0, GDO_CFG_HIGH_Z);

    // 2. Read PARTNUM & VERSION status registers
    res.partnum = readStatusReg(CC1101_PARTNUM);
    res.version = readStatusReg(CC1101_VERSION);
    res.spi_identity_ok = (res.partnum == 0x00) &&
                          (res.version != 0x00 && res.version != 0xFF);

    // 3. Test Register Write & Readback on IOCFG0
    writeReg(CC1101_IOCFG0, GDO_CFG_DRIVE_LOW);
    uint8_t readbackLow = readReg(CC1101_IOCFG0);
    writeReg(CC1101_IOCFG0, GDO_CFG_DRIVE_HIGH);
    uint8_t readbackHigh = readReg(CC1101_IOCFG0);
    res.register_rw_ok = (readbackLow == GDO_CFG_DRIVE_LOW) &&
                         (readbackHigh == GDO_CFG_DRIVE_HIGH);

    // 4. Physical Wire Test: GDO0 -> NodeMCU D4 (GPIO2 / Blue Wire)
    pinMode(PIN_CC1101_GDO0, INPUT);
    writeReg(CC1101_IOCFG0, GDO_CFG_DRIVE_LOW);
    delay(2);
    int gdo0Low = digitalRead(PIN_CC1101_GDO0);

    writeReg(CC1101_IOCFG0, GDO_CFG_DRIVE_HIGH);
    delay(2);
    int gdo0High = digitalRead(PIN_CC1101_GDO0);
    writeReg(CC1101_IOCFG0, GDO_CFG_HIGH_Z);
    res.gdo0_toggle_ok = (gdo0Low == LOW && gdo0High == HIGH);

    // 5. Physical Wire Test: GDO2 -> NodeMCU RX (GPIO3 / Grey Wire)
    // Temporarily switch GPIO3 to GPIO FUNCTION_3 to read its digital state,
    // then immediately release GDO2 to High-Z so Serial remains undisturbed.
    Serial.flush();
    pinMode(PIN_CC1101_GDO2, FUNCTION_3);
    pinMode(PIN_CC1101_GDO2, INPUT);

    writeReg(CC1101_IOCFG2, GDO_CFG_DRIVE_LOW);
    delay(2);
    int gdo2Low = digitalRead(PIN_CC1101_GDO2);

    writeReg(CC1101_IOCFG2, GDO_CFG_DRIVE_HIGH);
    delay(2);
    int gdo2High = digitalRead(PIN_CC1101_GDO2);

    // Release GDO2 to High-Impedance so USB flashing and Serial RX over GPIO3 work smoothly
    setGdo2HighImpedance();
    pinMode(PIN_CC1101_GDO2, SPECIAL); // Restore UART0 RX function on GPIO3
    res.gdo2_toggle_ok = (gdo2Low == LOW && gdo2High == HIGH);

    writeStrobe(CC1101_SIDLE);
    writeReg(CC1101_IOCFG0, GDO_CFG_HIGH_Z);
    return res;
}

void CC1101Diagnostics::printReport(const CC1101DiagResult& res) {
    Serial.println();
    Serial.println(F("================================================================"));
    Serial.println(F("   SubGHz-Remote-ESP8266 : Phase 1 Wiring & Hardware Self-Test  "));
    Serial.println(F("================================================================"));

    Serial.printf(" [1] Power & MISO Ready (D6/Yellow + D8/Pink + 3V3/GND) : %s\r\n",
                  res.spi_miso_ready ? "PASS" : "FAIL (MISO stayed HIGH)");

    Serial.printf(" [2] SPI Chip Identity  (D5/Green SCK + D7/Orange MOSI) : %s (PARTNUM=0x%02X, VERSION=0x%02X)\r\n",
                  res.spi_identity_ok ? "PASS" : "FAIL",
                  res.partnum, res.version);

    Serial.printf(" [3] SPI Register R/W   (Bidirectional SPI Integrity)   : %s\r\n",
                  res.register_rw_ok ? "PASS" : "FAIL");

    Serial.printf(" [4] GDO0 Wire Test     (Pin 3 Blue -> NodeMCU D4/GPIO2): %s\r\n",
                  res.gdo0_toggle_ok ? "PASS (Toggled LOW & HIGH)" : "FAIL (Check Blue wire on D4)");

    Serial.printf(" [5] GDO2 Wire Test     (Pin 8 Grey -> NodeMCU RX/GPIO3): %s\r\n",
                  res.gdo2_toggle_ok ? "PASS (Toggled LOW & HIGH, now set High-Z)"
                                     : "WARN / NOT CONNECTED (Optional for RX/TX)");

    Serial.println(F("----------------------------------------------------------------"));
    if (res.allRequiredPassed()) {
        Serial.println(F(" OVERALL RESULT: [PASS] CC1101 is wired correctly and operational!"));
    } else {
        Serial.println(F(" OVERALL RESULT: [FAIL] Wiring fault detected. Troubleshooting:"));
        if (!res.spi_miso_ready) {
            Serial.println(F("   - Verify Red wire is on 3V3 and Black wire is on GND."));
            Serial.println(F("   - Verify Pink wire (CSN) is on D8 and Yellow wire (MISO) is on D6."));
        } else if (!res.spi_identity_ok || !res.register_rw_ok) {
            Serial.println(F("   - Verify Green wire (SCK) is on D5 and Orange wire (MOSI) is on D7."));
        } else if (!res.gdo0_toggle_ok) {
            Serial.println(F("   - SPI works, but Blue wire (GDO0 -> D4) did not toggle."));
        }
    }
    Serial.println(F("================================================================"));
    Serial.println();
}

bool CC1101Diagnostics::startLiveMonitor(float freqMhz) {
    // Compatibility wrapper: monitoring is now bounded, never perpetual RX.
    return SubGhzEngine::startCapture(freqMhz);
}

void CC1101Diagnostics::loopLiveMonitor() {
    SubGhzEngine::poll();
}
