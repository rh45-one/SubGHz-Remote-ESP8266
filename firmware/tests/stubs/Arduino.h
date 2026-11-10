#pragma once
#include <stdint.h>
#include <stddef.h>
#define IRAM_ATTR
constexpr int LOW = 0, HIGH = 1, INPUT = 0, OUTPUT = 1, CHANGE = 2;
extern uint32_t clockUs;
extern int dataLevel;
extern void (*edgeHandler)();
inline uint32_t micros() { return clockUs; }
inline uint32_t millis() { return clockUs / 1000; }
inline int digitalRead(uint8_t) { return dataLevel; }
inline void digitalWrite(uint8_t, int level) { dataLevel = level; }
inline void pinMode(uint8_t, int) {}
inline int digitalPinToInterrupt(uint8_t pin) { return pin; }
inline void attachInterrupt(int, void (*handler)(), int) { edgeHandler = handler; }
inline void detachInterrupt(int) { edgeHandler = nullptr; }
inline void noInterrupts() {}
inline void interrupts() {}
inline void delayMicroseconds(uint32_t us) { clockUs += us; }
inline void yield() {}
struct EspClass {
    void wdtFeed() const {}
};
constexpr EspClass ESP{};
