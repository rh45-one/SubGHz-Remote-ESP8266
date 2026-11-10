#include <assert.h>
#include <stdio.h>
#include <limits>
#include <initializer_list>
#include "subghz_engine.h"
#include "auth_policy.h"
#include "http_framing.h"
#include <string>
#include <ELECHOUSE_CC1101_SRC_DRV.h>

uint32_t clockUs = 0;
int dataLevel = LOW;
void (*edgeHandler)() = nullptr;
FakeRadio ELECHOUSE_cc1101;

void edge(uint32_t duration, int level) {
    clockUs += duration;
    dataLevel = level;
    if (edgeHandler) edgeHandler();
}
void checkIdle() {
    assert(ELECHOUSE_cc1101.idle);
    assert(ELECHOUSE_cc1101.regs[0] == 0x2E);
    assert(ELECHOUSE_cc1101.regs[2] == 0x2E);
    assert(!edgeHandler);
}

int main() {
    AuthPolicy policy;
    assert(!policy.consume(0));
    policy.issue(0); assert(policy.consume(9999)); assert(!policy.consume(9999));
    policy.issue(0); assert(!policy.consume(10000));
    policy.issue(0xFFFFFF00UL); assert(policy.consume(50));
    for (int i = 0; i < 4; ++i) { policy.failure(100); assert(!policy.locked(100)); }
    policy.failure(100); assert(policy.locked(100)); assert(policy.remaining(100) == 60);
    assert(policy.locked(60099)); assert(!policy.locked(60100));
    policy.failure(60100); assert(policy.failures == 1);
    policy.success(); assert(policy.failures == 0);
    for (int i = 0; i < 5; ++i) policy.failure(0xFFFFFF00UL);
    assert(policy.locked(50));

    const std::string validHeader = "POST /api/transmit HTTP/1.1\r\nHost: esp\r\nContent-Type: application/json\r\nContent-Length: 8192\r\n\r\n";
    auto request = HttpFraming::parse(validHeader.c_str(), validHeader.size());
    assert(!request.error && request.post && request.length == 8192);
    for (const char* field : {"Content-Length: 2\r\nContent-Length: 2", "Transfer-Encoding: chunked", "Content-Length: -1", "Content-Length: 2x", "Content-Length : 2", "Content-Length: 8193", "Expect: 100-continue", " folded: value"}) {
        std::string invalidHeader = std::string("POST /api/transmit HTTP/1.1\r\nHost: esp\r\nContent-Type: application/json\r\n") + field + "\r\n\r\n";
        assert(HttpFraming::parse(invalidHeader.c_str(), invalidHeader.size()).error);
    }
    const char* get = "GET /api/status HTTP/1.1\r\nHost: esp\r\n\r\n";
    assert(!HttpFraming::parse(get, strlen(get)).error);
    assert(HttpFraming::parse("", 0).error);
    // Deterministic malformed-input mutations: parser must never walk outside the header.
    for (size_t i = 0; i < validHeader.size(); ++i) {
        std::string mutated = validHeader; mutated[i] = '\0';
        assert(HttpFraming::parse(mutated.c_str(), mutated.size()).error);
    }
    using namespace PulseCodec;
    int16_t pulses[512] = {};
    FixedCode code;
    code.value = 0xABCDEF;
    code.bits = 24;
    code.pulse_us = 350;
    code.protocol = 1;
    FixedCode decoded;
    for (int bits : {24, 32}) {
        code.bits = static_cast<uint8_t>(bits);
        code.value = bits == 24 ? 0xABCDEF : 0xFEDCBA98;
        size_t n = encode(code, pulses);
        assert(n == static_cast<size_t>(bits * 2 + 2));
        assert(validRaw(pulses, n, 5));
        assert(decode(pulses, n, decoded));
        assert(decoded.value == code.value && decoded.bits == bits && decoded.pulse_us == 350);
        assert(decode(pulses, n - 1, decoded));
        pulses[1] = -2000;
        assert(!decode(pulses, n, decoded));
    }
    assert(!decode(nullptr, 50, decoded));
    for (uint32_t value : {0UL, 1UL, 0xFFFFFFUL, 0xFFFFFFFFUL}) {
        code.bits = 32;
        code.value = value;
        size_t n = encode(code, pulses);
        // Deterministic +/-10% jitter should still decode.
        for (size_t i = 0; i < n; ++i) pulses[i] = static_cast<int16_t>(pulses[i] * (i % 2 ? 90 : 110) / 100);
        assert(decode(pulses, n, decoded) && decoded.value == value);
    }
    assert(!validRaw(nullptr, 50, 1));
    int16_t invalid[] = {350, -32768};
    assert(!validRaw(invalid, 2, 1));
    for (size_t i = 0; i < 512; ++i) pulses[i] = i % 2 ? -32767 : 32767;
    assert(!validRaw(pulses, 512, 1)); // >2 second request budget
    invalid[1] = 350;
    assert(!validRaw(invalid, 2, 1));
    invalid[1] = 0;
    assert(!validRaw(invalid, 2, 1));
    code.protocol = 2;
    assert(!encode(code, pulses));
    code.protocol = 1;
    assert(!validFrequency(349) && !validFrequency(465));
    assert(!validFrequency(std::numeric_limits<float>::quiet_NaN()));
    for (float f : {300.f, 348.f, 387.f, 464.f, 779.f, 928.f}) assert(validFrequency(f));

    assert(SubGhzEngine::begin());
    checkIdle();
    assert(!SubGhzEngine::startCapture(500));
    assert(!SubGhzEngine::startCapture(433.92f, 10001));
    assert(SubGhzEngine::startCapture(433.92f));
    assert(!SubGhzEngine::startCapture(433.92f));
    clockUs += 10000001;
    SubGhzEngine::poll();
    assert(SubGhzEngine::state() == CaptureState::Timeout);
    checkIdle();

    code.bits = 24;
    code.value = 0xABCDEF;
    size_t n = encode(code, pulses);
    assert(SubGhzEngine::startCapture(433.92f));
    edge(100, HIGH);
    for (size_t i = 0; i < n; ++i) edge(pulses[i] > 0 ? pulses[i] : -pulses[i], i % 2 ? HIGH : LOW);
    SubGhzEngine::poll();
    assert(SubGhzEngine::state() == CaptureState::Complete);
    assert(SubGhzEngine::result().count == n);
    assert(SubGhzEngine::result().decoded);
    assert(SubGhzEngine::result().code.value == code.value);
    checkIdle();

    assert(SubGhzEngine::startCapture(433.92f));
    edge(100, HIGH);
    edge(350, LOW);
    clockUs += 12001;
    SubGhzEngine::poll();
    assert(SubGhzEngine::state() == CaptureState::Complete);
    assert(SubGhzEngine::result().pulses[1] == -12001);
    checkIdle();

    assert(SubGhzEngine::startCapture(433.92f));
    edge(100, HIGH);
    for (size_t i = 0; i < 513; ++i) edge(200, i % 2 ? HIGH : LOW);
    SubGhzEngine::poll();
    assert(SubGhzEngine::state() == CaptureState::Overflow);
    assert(SubGhzEngine::result().count == 512);
    checkIdle();

    assert(SubGhzEngine::startCapture(433.92f));
    edge(100, HIGH);
    edge(32768, LOW);
    SubGhzEngine::poll();
    assert(SubGhzEngine::state() == CaptureState::Overflow);
    checkIdle();

    clockUs = 0xFFFFFF00UL;
    assert(SubGhzEngine::startCapture(433.92f));
    edge(100, HIGH);
    edge(350, LOW); // micros() rollover must preserve pulse duration
    clockUs += 12001;
    SubGhzEngine::poll();
    assert(SubGhzEngine::state() == CaptureState::Complete);
    assert(SubGhzEngine::result().pulses[0] == 350);
    checkIdle();

    assert(!SubGhzEngine::transmitRaw(500, pulses, n, 1));
    assert(!SubGhzEngine::transmitRaw(433.92f, pulses, n, 0));
    assert(!SubGhzEngine::transmitRaw(433.92f, pulses, n, 21));
    assert(ELECHOUSE_cc1101.transmissions == 0);
    assert(SubGhzEngine::transmitFixed(433.92f, code, 3));
    assert(ELECHOUSE_cc1101.transmissions == 1 && dataLevel == LOW);
    checkIdle();
    assert(SubGhzEngine::startCapture(433.92f));
    assert(!SubGhzEngine::transmitFixed(433.92f, code, 1));
    SubGhzEngine::idle();
    checkIdle();
    puts("PASS: auth policy, HTTP framing, codec, capture/replay validation and High-Z cleanup");
}
