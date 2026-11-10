#pragma once
#include <Arduino.h>
#include "pulse_codec.h"

enum class CaptureState : uint8_t { Idle, Recording, Complete, Timeout, Overflow };

struct CaptureResult {
    int16_t pulses[PulseCodec::MAX_PULSES] = {};
    size_t count = 0;
    PulseCodec::FixedCode code;
    bool decoded = false;
    float frequency_mhz = 0;
};

class SubGhzEngine {
public:
    static bool begin();
    static void idle();
    static bool startCapture(float frequencyMhz, uint32_t windowMs = 10000);
    static void poll();
    static CaptureState state();
    // Read only after recording has stopped; buffer survives until next capture.
    static const CaptureResult& result();
    // Internal primitives; future HTTP callers MUST authenticate before invoking.
    static bool transmitRaw(float frequencyMhz, const int16_t* pulses, size_t count, uint8_t repeats);
    static bool transmitFixed(float frequencyMhz, const PulseCodec::FixedCode& code, uint8_t repeats);
};
