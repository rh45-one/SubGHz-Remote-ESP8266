#pragma once
#include <stdint.h>

namespace PinPolicy {
constexpr uint8_t MIN_LENGTH = 4;
constexpr uint8_t MAX_LENGTH = 12;
constexpr char DEFAULT_PIN[] = "0000";
static_assert(sizeof(DEFAULT_PIN) - 1 >= MIN_LENGTH && sizeof(DEFAULT_PIN) - 1 <= MAX_LENGTH, "Factory PIN must meet the PIN policy");
}

// Wrap-safe timers; no heap, filesystem, or platform-specific cryptography.
class AuthPolicy {
public:
    bool active = false;
    uint32_t issued = 0;
    uint32_t lockedAt = 0;
    uint8_t failures = 0;
    bool locked(uint32_t now) const { return failures >= 5 && now - lockedAt < 60000; }
    uint32_t remaining(uint32_t now) const { return locked(now) ? (60000 - (now - lockedAt) + 999) / 1000 : 0; }
    void issue(uint32_t now) { active = true; issued = now; }
    bool consume(uint32_t now) {
        bool valid = active && now - issued < 10000;
        active = false;
        return valid;
    }
    void failure(uint32_t now) {
        if (failures >= 5 && !locked(now)) failures = 0;
        if (++failures >= 5) { lockedAt = now; active = false; }
    }
    void success() { failures = 0; }
};
