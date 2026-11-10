#include "api_server.h"
#include "auth_manager.h"
#include "subghz_engine.h"
#include "http_framing.h"
#include <ESP8266WiFi.h>
#include <ArduinoJson.h>

namespace {
WiFiServer server(80);
WiFiClient client;
String header, body, path;
bool radioOk = false, readingBody = false;
size_t expected = 0;
uint32_t acceptedAt = 0;

void closeRequest() {
    client.stop();
    header = String(); body = String(); path = String();
    readingBody = false; expected = 0;
}
void send(int code, const JsonDocument& doc) {
    client.printf("HTTP/1.1 %d Response\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: %u\r\n\r\n", code, static_cast<unsigned>(measureJson(doc)));
    serializeJson(doc, client);
    closeRequest();
}
void error(int code, const char* message) {
    JsonDocument doc; doc["error"] = message; send(code, doc);
}
void dispatch(bool post) {
    JsonDocument response;
    if (!post && path == "/api/status") {
        response["version"] = "0.3.0";
        response["cc1101_ok"] = radioOk;
        response["auth_ready"] = AuthManager::ready();
        response["must_change_pin"] = AuthManager::mustChangePin();
        response["locked_out"] = AuthManager::locked();
        response["lockout_remaining_s"] = AuthManager::lockoutRemaining();
        response["uptime_s"] = millis() / 1000;
        send(200, response); return;
    }
    if (!AuthManager::ready()) { error(503, "Authentication store unavailable"); return; }
    if (AuthManager::locked()) { error(429, "Authentication locked for up to 60 seconds"); return; }
    if (!post && path == "/api/auth/challenge") {
        char nonce[33];
        if (!AuthManager::challenge(nonce)) { error(503, "Random source unavailable"); return; }
        response["salt"] = AuthManager::salt(); response["nonce"] = nonce; response["expires_in_ms"] = 10000;
        send(200, response); return;
    }
    if (!post && path == "/api/record/status") {
        CaptureState state = SubGhzEngine::state();
        response["state"] = static_cast<unsigned>(state);
        if (state == CaptureState::Complete) {
            const auto& result = SubGhzEngine::result();
            response["frequency_hz"] = static_cast<uint32_t>(result.frequency_mhz * 1000000.0f + 0.5f);
            response["repeats"] = 1;
            JsonArray pulses = response["pulses"].to<JsonArray>();
            for (size_t i = 0; i < result.count; ++i) pulses.add(result.pulses[i]);
        }
        send(200, response); return;
    }
    if (!post || (path != "/api/auth/verify" && path != "/api/auth/change-pin" &&
        path != "/api/record/start" && path != "/api/transmit")) { error(404, "Unknown endpoint"); return; }

    JsonDocument envelope;
    if (deserializeJson(envelope, body, DeserializationOption::NestingLimit(4)) ||
        !envelope["nonce"].is<String>() || !envelope["signature"].is<String>() || !envelope["payload"].is<String>()) {
        // Malformed envelopes still consume the challenge and count toward lockout.
        AuthManager::verify("", "", path, "");
        error(AuthManager::locked() ? 429 : 400, "Invalid authenticated envelope"); return;
    }
    body = String(); // ArduinoJson copied envelope strings; release the bounded HTTP buffer now.
    String payload = envelope["payload"].as<String>();
    if (!AuthManager::verify(envelope["nonce"].as<String>(), envelope["signature"].as<String>(), path, payload)) {
        error(AuthManager::locked() ? 429 : 401, "Authentication failed"); return;
    }
    envelope.clear();
    JsonDocument args;
    if (deserializeJson(args, payload, DeserializationOption::NestingLimit(4)) || !args.is<JsonObject>()) {
        error(400, "Invalid payload JSON"); return;
    }
    if (path == "/api/auth/verify") {
        response["must_change_pin"] = AuthManager::mustChangePin(); send(200, response); return;
    }
    if (path == "/api/auth/change-pin") {
        if (!AuthManager::changePin(args["salt"].as<String>(), args["new_pin_key"].as<String>())) {
            error(400, "Invalid PIN material or credential write failed"); return;
        }
        response["ok"] = true; send(200, response); return;
    }
    if (AuthManager::mustChangePin()) { error(403, "Change default PIN before RF operations"); return; }
    if (!radioOk) { error(503, "Radio unavailable"); return; }
    if (!args["frequency_hz"].is<uint32_t>()) { error(400, "Integer frequency_hz required"); return; }
    uint32_t hz = args["frequency_hz"].as<uint32_t>();
    bool supported = (hz >= 300000000 && hz <= 348000000) || (hz >= 387000000 && hz <= 464000000) || (hz >= 779000000 && hz <= 928000000);
    float mhz = hz / 1000000.0f;
    if (!supported) { error(400, "Unsupported frequency"); return; }
    if (SubGhzEngine::state() == CaptureState::Recording) { error(409, "Capture already active"); return; }
    if (path == "/api/record/start") {
        if (!args["window_ms"].is<uint32_t>() || !SubGhzEngine::startCapture(mhz, args["window_ms"].as<uint32_t>())) {
            error(400, "Invalid capture window"); return;
        }
    } else {
        if (!args["repeats"].is<uint8_t>()) { error(400, "Integer repeats required"); return; }
        uint8_t repeats = args["repeats"].as<uint8_t>();
        if (args["pulses"].is<JsonArray>()) {
            JsonArray pulses = args["pulses"];
            if (pulses.size() > PulseCodec::MAX_PULSES) { error(400, "Too many pulses"); return; }
            int16_t timings[PulseCodec::MAX_PULSES];
            size_t count = 0;
            for (JsonVariant p : pulses) {
                if (!p.is<int16_t>()) { error(400, "Invalid pulse"); return; }
                timings[count++] = p.as<int16_t>();
            }
            if (!SubGhzEngine::transmitRaw(mhz, timings, count, repeats)) { error(400, "Invalid waveform"); return; }
        } else {
            if (!args["code"].is<uint32_t>() || !args["bits"].is<uint8_t>() || !args["pulse_us"].is<uint16_t>() || !args["protocol"].is<uint8_t>()) {
                error(400, "Invalid fixed code"); return;
            }
            PulseCodec::FixedCode code;
            code.value = args["code"].as<uint32_t>(); code.bits = args["bits"].as<uint8_t>();
            code.pulse_us = args["pulse_us"].as<uint16_t>(); code.protocol = args["protocol"].as<uint8_t>();
            if (!SubGhzEngine::transmitFixed(mhz, code, repeats)) { error(400, "Invalid fixed waveform"); return; }
        }
    }
    response["ok"] = true; send(200, response);
}

bool parseHeader() {
    auto request = HttpFraming::parse(header.c_str(), header.length());
    if (request.error) { error(request.error, "Invalid or unsupported HTTP framing"); return false; }
    path = request.path;
    expected = request.length;
    if (!request.post) { dispatch(false); return false; }
    readingBody = true;
    if (!body.reserve(expected)) { error(503, "Insufficient memory"); return false; }
    return true;
}
}

void ApiServer::begin(bool ready) { radioOk = ready; server.begin(); server.setNoDelay(true); }
void ApiServer::setRadioReady(bool ready) { radioOk = ready; }
void ApiServer::poll() {
    if (!client) {
        client = server.accept();
        if (!client) return;
        acceptedAt = millis();
    }
    if (millis() - acceptedAt > 2500) { closeRequest(); return; }
    for (size_t budget = 0; budget < 256 && client && client.available(); ++budget) {
        char c = client.read();
        if (!readingBody) {
            header += c;
            if (header.length() > 1024) { error(431, "Headers too large"); return; }
            if (header.endsWith("\r\n\r\n") && !parseHeader()) return;
        } else {
            body += c;
            if (body.length() == expected) { dispatch(true); return; }
        }
    }
    if (!client.connected() && !client.available()) closeRequest();
}
