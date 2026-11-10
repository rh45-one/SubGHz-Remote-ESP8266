#include <Arduino.h>
#include "pin_config.h"
#include "cc1101_diag.h"
#include "subghz_engine.h"
#include "auth_manager.h"
#include "api_server.h"
#include <ESP8266WiFi.h>
#include <LittleFS.h>
#if __has_include("secrets.h")
#include "secrets.h"
#endif
#ifndef WIFI_SSID
#define WIFI_SSID ""
#define WIFI_PASSWORD ""
#endif

namespace {
bool radioReady = false;
bool reportPending = false;
float frequencyMhz = DEFAULT_SUBGHZ_FREQ_MHZ;

void initialize() {
    CC1101DiagResult res = CC1101Diagnostics::runSelfTest();
    CC1101Diagnostics::printReport(res);
    radioReady = res.allRequiredPassed() && SubGhzEngine::begin();
    ApiServer::setRadioReady(radioReady);
    Serial.println(radioReady ? F("[READY] Radio idle / High-Z.") : F("[ERROR] Check wiring; use 't' to retry."));
}
}

void setup() {
    Serial.begin(115200);
    // Release shared boot/UART pins before logging or waiting.
    initialize();
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA); // Hardware RNG is available with the RF subsystem enabled.
    bool authReady = AuthManager::begin();
    Serial.println(authReady ? F("[AUTH] Credential store ready.") : F("[AUTH] Unavailable. First setup/recovery: serial 'F' explicitly formats LittleFS."));
    if (strlen(WIFI_SSID)) {
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
        ApiServer::begin(radioReady);
        Serial.println(F("[NET] Connecting; DHCP address appears on your router. No AP fallback."));
    }
    Serial.println(F("Phase 3: r=capture (10s max), s=stop, t=self-test"));
    Serial.println(F("1=433.92MHz, 2=315MHz, 3=868.35MHz (idle only). No serial TX command."));
}

void loop() {
    SubGhzEngine::poll();
    if (strlen(WIFI_SSID)) ApiServer::poll();
    if (reportPending && SubGhzEngine::state() != CaptureState::Recording) {
        reportPending = false;
        const CaptureResult& result = SubGhzEngine::result();
        Serial.printf("[CAPTURE] state=%u, pulses=%u; radio idle / High-Z\r\n",
                      static_cast<unsigned>(SubGhzEngine::state()), static_cast<unsigned>(result.count));
        if (SubGhzEngine::state() == CaptureState::Complete) {
            Serial.print(F("RAW_Data:"));
            for (size_t i = 0; i < result.count; ++i) Serial.printf(" %d", result.pulses[i]);
            Serial.println();
            if (result.decoded) Serial.printf("[FIXED] value=%lu bits=%u TE=%u protocol=%u\r\n",
                static_cast<unsigned long>(result.code.value), result.code.bits,
                result.code.pulse_us, result.code.protocol);
        }
    }
    if (Serial.available()) {
        char cmd = Serial.read();
        if (cmd == 'F' && !AuthManager::ready() && SubGhzEngine::state() != CaptureState::Recording) {
            bool ok = LittleFS.format() && AuthManager::begin();
            Serial.println(ok ? F("[AUTH] Initialized; default PIN must be changed before RF API use.") : F("[AUTH] Initialization failed."));
        } else if (cmd == 's') SubGhzEngine::idle();
        else if (SubGhzEngine::state() == CaptureState::Recording) {
            if (cmd != '\r' && cmd != '\n') Serial.println(F("[BUSY] Stop capture first."));
        } else if (cmd == 't') initialize();
        else if (cmd == '1') frequencyMhz = 433.92f;
        else if (cmd == '2') frequencyMhz = 315.00f;
        else if (cmd == '3') frequencyMhz = 868.35f;
        else if (cmd == 'r') {
            reportPending = radioReady && SubGhzEngine::startCapture(frequencyMhz);
            Serial.println(reportPending ? F("[CAPTURE] Armed for up to 10 seconds.") : F("[ERROR] Capture refused."));
        }
    }
    yield();
}
