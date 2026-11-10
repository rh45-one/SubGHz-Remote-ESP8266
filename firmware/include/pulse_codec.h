#pragma once
#include <stddef.h>
#include <stdint.h>

namespace PulseCodec {
constexpr size_t MAX_PULSES = 512;
struct FixedCode {
    uint32_t value = 0;
    uint8_t bits = 0;
    uint16_t pulse_us = 0;
    uint8_t protocol = 0;
};

inline bool validFrequency(float mhz) {
    return (mhz >= 300 && mhz <= 348) || (mhz >= 387 && mhz <= 464) ||
           (mhz >= 779 && mhz <= 928);
}

inline bool validRaw(const int16_t* pulses, size_t count, uint8_t repeats) {
    if (!pulses || count < 2 || count > MAX_PULSES || pulses[0] <= 0 ||
        repeats == 0 || repeats > 20) return false;
    uint32_t duration = 0;
    for (size_t i = 0; i < count; ++i) {
        const int32_t p = pulses[i];
        if (!p || p == -32768 || (i && ((p > 0) == (pulses[i - 1] > 0)))) return false;
        duration += p > 0 ? p : -p;
    }
    return duration * repeats <= 2000000UL;
}

// rc-switch protocol 1: MSB first, 0=(1T,-3T), 1=(3T,-1T), sync=(1T,-31T).
inline size_t encode(const FixedCode& code, int16_t* out) {
    if (!out || code.protocol != 1 || (code.bits != 24 && code.bits != 32) ||
        code.pulse_us < 100 || code.pulse_us > 1057 ||
        (code.bits < 32 && code.value >= (1UL << code.bits))) return 0;
    size_t n = 0;
    for (int bit = code.bits - 1; bit >= 0; --bit) {
        bool one = (code.value >> bit) & 1;
        out[n++] = code.pulse_us * (one ? 3 : 1);
        out[n++] = -static_cast<int16_t>(code.pulse_us * (one ? 1 : 3));
    }
    out[n++] = code.pulse_us;
    out[n++] = -static_cast<int16_t>(code.pulse_us * 31);
    return n;
}

inline bool near(uint32_t actual, uint32_t expected) {
    return actual >= expected * 65 / 100 && actual <= expected * 135 / 100;
}

inline bool decode(const int16_t* pulses, size_t count, FixedCode& result) {
    // A trailing sync space may be absent when capture closes on silence.
    if (!pulses || (count != 49 && count != 50 && count != 65 && count != 66)) return false;
    const size_t bits = (count - 1) / 2;
    uint32_t sum = 0;
    for (size_t i = 0; i < bits * 2; i += 2) {
        if (pulses[i] <= 0 || pulses[i + 1] >= 0) return false;
        sum += pulses[i] - static_cast<int32_t>(pulses[i + 1]);
    }
    const uint32_t te = sum / static_cast<uint32_t>(bits * 4);
    if (te < 100 || te > 1057 || !near(pulses[bits * 2], te)) return false;
    if (count % 2 == 0 && (pulses[count - 1] >= 0 ||
        !near(-static_cast<int32_t>(pulses[count - 1]), te * 31))) return false;
    uint32_t value = 0;
    for (size_t i = 0; i < bits * 2; i += 2) {
        uint32_t mark = pulses[i], space = -static_cast<int32_t>(pulses[i + 1]);
        bool one = near(mark, te * 3) && near(space, te);
        if (!one && !(near(mark, te) && near(space, te * 3))) return false;
        value = (value << 1) | static_cast<uint32_t>(one);
    }
    result.value = value;
    result.bits = static_cast<uint8_t>(bits);
    result.pulse_us = static_cast<uint16_t>(te);
    result.protocol = 1;
    return true;
}
} // namespace PulseCodec
