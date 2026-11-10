#include <Arduino.h>
#include "pin_config.h"
#include "cc1101_diag.h"
#include "subghz_engine.h"
#include "auth_manager.h"
#include "api_server.h"
#include <ESP8266WiFi.h>
#include <LittleFS.h>
#if !defined(SUBGHZ_NETWORK_CHECK) && __has_include("secrets.h")
#include "secrets.h"
#endif
#if !defined(SUBGHZ_NETWORK_CHECK) && __has_include("network_config.h")
#include "network_config.h"
#endif
#ifndef WIFI_SSID
#define WIFI_SSID ""
#define WIFI_PASSWORD ""
#endif
#ifndef NETWORK_STATIC_IP
#define NETWORK_STATIC_IP ""
#endif
#ifndef NETWORK_GATEWAY
#define NETWORK_GATEWAY ""
#endif
#ifndef NETWORK_SUBNET
#define NETWORK_SUBNET ""
#endif
#ifndef NETWORK_DNS
#define NETWORK_DNS NETWORK_GATEWAY
#endif

namespace {
bool radioReady = false;
bool reportPending = false;
float frequencyMhz = DEFAULT_SUBGHZ_FREQ_MHZ;
bool networkReported = false;
bool networkEnabled = false;

bool configureNetworkAddress() {
    if (!strlen(NETWORK_STATIC_IP)) return true; // DHCP unless static addressing was explicitly configured.
    IPAddress ip, gateway, subnet, dns;
    bool valid = ip.fromString(NETWORK_STATIC_IP) && gateway.fromString(NETWORK_GATEWAY) &&
                 subnet.fromString(NETWORK_SUBNET) && dns.fromString(NETWORK_DNS);
    if (!valid || !WiFi.config(ip, gateway, subnet, dns)) {
        WiFi.disconnect(false);
        Serial.println(F("[NET] Static IP configuration failed. API disabled; no DHCP fallback."));
        return false;
    }
    Serial.println(F("[NET] Static IP configuration enabled; no DHCP fallback."));
    return true;
}

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
    Serial.println(F("[BOOT] SubGHz-Remote v0.3.1; PIN policy: 4-12 digits."));
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA); // Hardware RNG is available with the RF subsystem enabled.
    bool authReady = AuthManager::begin();
    Serial.println(authReady ? F("[AUTH] Credential store ready.") : F("[AUTH] Unavailable. First setup/recovery: serial 'F' explicitly formats LittleFS."));
    if (strlen(WIFI_SSID)) {
        networkEnabled = configureNetworkAddress();
        if (networkEnabled) {
            WiFi.setAutoReconnect(true);
            WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
            ApiServer::begin(radioReady);
            Serial.println(F("[NET] Connecting to configured network. No AP fallback."));
        }
    } else {
        Serial.println(F("[NET] Disabled: configure Wi-Fi in secrets.h and rebuild to enable the API."));
    }
    Serial.println(F("Phase 3: r=capture (10s max), s=stop, t=self-test"));
    Serial.println(F("1=433.92MHz, 2=315MHz, 3=868.35MHz (idle only). No serial TX command."));
}

void loop() {
    SubGhzEngine::poll();
    if (networkEnabled) {
        bool connected = WiFi.status() == WL_CONNECTED;
        if (connected && !networkReported) {
            Serial.print(F("[NET] Connected; IP address: "));
            Serial.println(WiFi.localIP());
            networkReported = true;
        } else if (!connected && networkReported) {
            Serial.println(F("[NET] Disconnected; retrying configured network."));
            networkReported = false;
        }
        ApiServer::poll();
    }
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
