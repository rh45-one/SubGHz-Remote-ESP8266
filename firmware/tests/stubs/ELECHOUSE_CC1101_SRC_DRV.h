#pragma once
#include <stdint.h>
constexpr uint8_t CC1101_IOCFG2 = 0, CC1101_IOCFG0 = 2, CC1101_SIDLE = 0x36;
struct FakeRadio {
    uint8_t regs[3] = {};
    bool idle = true;
    unsigned transmissions = 0;
    void setSpiPin(int, int, int, int) {}
    void setGDO0(int) {}
    void Init() { regs[0] = 0x29; }
    bool getCC1101() { return true; }
    void SpiWriteReg(uint8_t reg, uint8_t value) { regs[reg] = value; }
    void SpiStrobe(uint8_t) { idle = true; }
    void setCCMode(int) { regs[0] = regs[2] = 0x0D; }
    void setModulation(int) {}
    void setMHZ(float) {}
    void setRxBW(float) {}
    void SetRx() { idle = false; }
    void SetTx() { idle = false; ++transmissions; }
};
extern FakeRadio ELECHOUSE_cc1101;
