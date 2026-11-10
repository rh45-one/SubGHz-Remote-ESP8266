#include "subghz_engine.h"
#include "pin_config.h"
#include <ELECHOUSE_CC1101_SRC_DRV.h>

namespace {
constexpr uint8_t HIGH_Z = 0x2E;
constexpr uint32_t FRAME_GAP_US = 12000;
constexpr uint32_t EDGE_GAP_US = 5000;
CaptureResult captured;
volatile uint16_t pulseCount = 0;
volatile uint32_t lastEdgeUs = 0;
volatile bool previousHigh = false;
volatile bool started = false;
volatile bool frameDone = false;
volatile bool overflow = false;
CaptureState captureState = CaptureState::Idle;
uint32_t captureStartMs = 0, captureWindowMs = 0;
bool ready = false;

void IRAM_ATTR onEdge() {
    if (frameDone || overflow) return;
    uint32_t now = micros();
    bool high = digitalRead(PIN_CC1101_GDO0);
    uint32_t elapsed = now - lastEdgeUs;
    if (!started) {
        // Ignore leading silence; the first pulse always begins with a rising edge.
        if (high) {
            started = true;
            previousHigh = true;
            lastEdgeUs = now;
        }
        return;
    }
    if (!previousHigh && elapsed >= EDGE_GAP_US) {
        if (pulseCount >= PulseCodec::MAX_PULSES) overflow = true;
        else captured.pulses[pulseCount++] = -static_cast<int16_t>(elapsed > 32767 ? 32767 : elapsed);
        frameDone = true;
        return;
    }
    if (elapsed > 32767) {
        overflow = true; // Cannot represent this mark in int16_t; never silently truncate it.
        return;
    }
    if (!elapsed || pulseCount >= PulseCodec::MAX_PULSES) {
        overflow = true;
        return;
    }
    captured.pulses[pulseCount++] = static_cast<int16_t>(previousHigh ? static_cast<int32_t>(elapsed) : -static_cast<int32_t>(elapsed));
    lastEdgeUs = now;
    previousHigh = high;
}

void configure(float mhz) {
    ELECHOUSE_cc1101.setCCMode(0);
    ELECHOUSE_cc1101.SpiWriteReg(CC1101_IOCFG2, HIGH_Z);
    ELECHOUSE_cc1101.SpiWriteReg(CC1101_IOCFG0, HIGH_Z);
    ELECHOUSE_cc1101.setMHZ(mhz);
    ELECHOUSE_cc1101.setModulation(2);
    ELECHOUSE_cc1101.setSyncMode(0);
    ELECHOUSE_cc1101.setPA(12);
    ELECHOUSE_cc1101.setRxBW(256.0f);
}
} // namespace

bool SubGhzEngine::begin() {
    ELECHOUSE_cc1101.setSpiPin(PIN_CC1101_SCK, PIN_CC1101_MISO, PIN_CC1101_MOSI, PIN_CC1101_CSN);
    ELECHOUSE_cc1101.setGDO0(PIN_CC1101_GDO0);
    ELECHOUSE_cc1101.Init();
    ELECHOUSE_cc1101.SpiWriteReg(CC1101_IOCFG2, HIGH_Z);
    idle();
    ready = ELECHOUSE_cc1101.getCC1101();
    return ready;
}

void SubGhzEngine::idle() {
    detachInterrupt(digitalPinToInterrupt(PIN_CC1101_GDO0));
    ELECHOUSE_cc1101.SpiStrobe(CC1101_SIDLE);
    ELECHOUSE_cc1101.SpiWriteReg(CC1101_IOCFG0, HIGH_Z);
    ELECHOUSE_cc1101.SpiWriteReg(CC1101_IOCFG2, HIGH_Z);
    pinMode(PIN_CC1101_GDO0, INPUT);
    if (captureState == CaptureState::Recording) captureState = CaptureState::Idle;
}

bool SubGhzEngine::startCapture(float mhz, uint32_t windowMs) {
    if (!ready || captureState == CaptureState::Recording || !PulseCodec::validFrequency(mhz) ||
        !windowMs || windowMs > 10000) return false;
    idle();
    captured = CaptureResult{};
    captured.frequency_mhz = mhz;
    pulseCount = 0;
    started = frameDone = overflow = false;
    configure(mhz);
    pinMode(PIN_CC1101_GDO0, INPUT);
    captureStartMs = millis();
    captureWindowMs = windowMs;
    captureState = CaptureState::Recording;
    ELECHOUSE_cc1101.SpiWriteReg(CC1101_IOCFG0, 0x0D);
    ELECHOUSE_cc1101.SetRx();
    attachInterrupt(digitalPinToInterrupt(PIN_CC1101_GDO0), onEdge, CHANGE);
    return true;
}

void SubGhzEngine::poll() {
    if (captureState != CaptureState::Recording) return;
    noInterrupts();
    bool full = overflow;
    bool done = frameDone || (started && pulseCount && !previousHigh && micros() - lastEdgeUs >= FRAME_GAP_US);
    bool timedOut = millis() - captureStartMs >= captureWindowMs;
    if (done && !frameDone) {
        // Preserve a final OFF interval so RAW replay retains inter-burst spacing.
        uint32_t silence = micros() - lastEdgeUs;
        if (pulseCount >= PulseCodec::MAX_PULSES) full = true;
        else captured.pulses[pulseCount++] = -static_cast<int16_t>(silence > 32767 ? 32767 : silence);
    }
    // Freeze the ISR before copying its counters or returning the chip to idle.
    if (full || done || timedOut) frameDone = true;
    interrupts();
    if (!full && !done && !timedOut) return;
    idle();
    captured.count = pulseCount;
    captureState = full ? CaptureState::Overflow : (done ? CaptureState::Complete : CaptureState::Timeout);
    if (captureState == CaptureState::Complete) {
        // The terminal idle interval may have been measured without a following edge.
        size_t decodeCount = captured.count;
        if (decodeCount && captured.pulses[decodeCount - 1] < 0) --decodeCount;
        captured.decoded = PulseCodec::decode(captured.pulses, decodeCount, captured.code);
    }
}

CaptureState SubGhzEngine::state() { return captureState; }
const CaptureResult& SubGhzEngine::result() { return captured; }

bool SubGhzEngine::transmitRaw(float mhz, const int16_t* pulses, size_t count, uint8_t repeats) {
    if (!ready || captureState == CaptureState::Recording || !PulseCodec::validFrequency(mhz) ||
        !PulseCodec::validRaw(pulses, count, repeats)) return false;
    idle();
    configure(mhz);
    digitalWrite(PIN_CC1101_GDO0, LOW);
    pinMode(PIN_CC1101_GDO0, OUTPUT);
    ELECHOUSE_cc1101.SpiWriteReg(CC1101_IOCFG0, 0x0D);
    ELECHOUSE_cc1101.SetTx();
    delayMicroseconds(1000); // Allow synthesizer calibration/settling before the first mark.
    for (uint8_t repeat = 0; repeat < repeats; ++repeat) {
        for (size_t i = 0; i < count; ++i) {
            int32_t p = pulses[i];
            digitalWrite(PIN_CC1101_GDO0, p > 0 ? HIGH : LOW);
            uint32_t us = static_cast<uint32_t>(p > 0 ? p : -p);
            if (p <= -5000) {
                // Only yield to the Wi-Fi/CONT stack during long OFF gaps (>= 5 ms),
                // compensating for elapsed time so bit-level spaces are never stretched.
                uint32_t startUs = micros();
                yield();
                uint32_t elapsedUs = micros() - startUs;
                if (elapsedUs < us) delayMicroseconds(us - elapsedUs);
            } else {
                delayMicroseconds(us);
            }
        }
        ESP.wdtFeed();
    }
    if (pulses[count - 1] < 0 && pulses[count - 1] >= -2000 && pulses[0] >= 2000) {
        // Back-to-back protocols without a trailing gap (e.g. Hormann HSM) need a
        // final rising edge + header mark to close the last bit of the final repeat.
        digitalWrite(PIN_CC1101_GDO0, HIGH);
        delayMicroseconds(static_cast<uint32_t>(pulses[0]));
    }
    digitalWrite(PIN_CC1101_GDO0, LOW);
    idle();
    return true;
}

bool SubGhzEngine::transmitFixed(float mhz, const PulseCodec::FixedCode& code, uint8_t repeats) {
    int16_t pulses[66];
    size_t count = PulseCodec::encode(code, pulses);
    return count && transmitRaw(mhz, pulses, count, repeats);
}
