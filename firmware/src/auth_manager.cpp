#include "auth_manager.h"
#include "auth_policy.h"
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <bearssl/bearssl.h>
extern "C" {
#include <user_interface.h>
}

namespace {
uint8_t pinKey[32];
char saltHex[33] = {};
char nonceHex[33] = {};
bool initialized = false, mustChange = true;
AuthPolicy policy;

bool unhex(const String& text, uint8_t* bytes, size_t n) {
    if (text.length() != n * 2) return false;
    for (size_t i = 0; i < n; ++i) {
        uint8_t value = 0;
        for (size_t j = 0; j < 2; ++j) {
            char c = text[i * 2 + j];
            if (c < '0' || (c > '9' && c < 'a') || c > 'f') return false;
            value = (value << 4) | (c <= '9' ? c - '0' : c - 'a' + 10);
        }
        bytes[i] = value;
    }
    return true;
}
void hex(const uint8_t* bytes, size_t n, char* out) {
    const char* digits = "0123456789abcdef";
    for (size_t i = 0; i < n; ++i) { out[2*i] = digits[bytes[i] >> 4]; out[2*i+1] = digits[bytes[i] & 15]; }
    out[n*2] = 0;
}
bool randomHex(char* out) {
    uint8_t bytes[16];
    if (os_get_random(bytes, sizeof(bytes)) != 0) return false;
    hex(bytes, sizeof(bytes), out);
    return true;
}
bool persist(const char* salt, const uint8_t* key, bool change) {
    char keyHex[65];
    hex(key, 32, keyHex);
    JsonDocument doc;
    doc["salt"] = salt;
    doc["pin_key"] = keyHex;
    doc["must_change_pin"] = change;
    File file = LittleFS.open("/auth.tmp", "w");
    if (!file) return false;
    bool ok = serializeJson(doc, file) == measureJson(doc);
    file.flush();
    file.close();
    if (!ok) { LittleFS.remove("/auth.tmp"); return false; }
    return LittleFS.rename("/auth.tmp", "/auth.json");
}
}

bool AuthManager::begin() {
    initialized = false;
    policy.active = false;
    LittleFS.setConfig(LittleFSConfig().setAutoFormat(false));
    if (!LittleFS.begin()) return false; // Never silently format or reset a damaged credential store.
    if (!LittleFS.exists("/auth.json")) {
        // An interrupted provision is not permission to restore the default PIN.
        if (LittleFS.exists("/auth.tmp") || !randomHex(saltHex)) return false;
        uint8_t saltBytes[16];
        if (!unhex(saltHex, saltBytes, 16)) return false;
        br_sha256_context ctx;
        br_sha256_init(&ctx);
        br_sha256_update(&ctx, saltBytes, sizeof(saltBytes));
        br_sha256_update(&ctx, "123456", 6);
        br_sha256_out(&ctx, pinKey);
        mustChange = true;
        if (!persist(saltHex, pinKey, mustChange)) return false;
    } else {
        File file = LittleFS.open("/auth.json", "r");
        if (!file || file.size() > 512) return false;
        JsonDocument doc;
        if (deserializeJson(doc, file) || !doc["must_change_pin"].is<bool>()) return false;
        String salt = doc["salt"].as<String>();
        uint8_t saltBytes[16];
        if (!unhex(salt, saltBytes, 16) || !unhex(doc["pin_key"].as<String>(), pinKey, 32)) return false;
        salt.toCharArray(saltHex, sizeof(saltHex));
        mustChange = doc["must_change_pin"].as<bool>();
    }
    initialized = true;
    return true;
}
bool AuthManager::ready() { return initialized; }
bool AuthManager::mustChangePin() { return mustChange; }
bool AuthManager::locked() { return policy.locked(millis()); }
uint32_t AuthManager::lockoutRemaining() { return policy.remaining(millis()); }
const char* AuthManager::salt() { return saltHex; }
bool AuthManager::challenge(char nonce[33]) {
    if (!initialized || locked()) return false;
    // A challenge replaces the one outstanding nonce; serialize operations in the GUI.
    policy.active = false;
    if (!randomHex(nonceHex)) return false;
    policy.issue(millis());
    memcpy(nonce, nonceHex, 33);
    return true;
}
bool AuthManager::verify(const String& nonce, const String& signature, const String& endpoint, const String& payload) {
    if (!initialized || locked()) return false;
    bool usable = policy.consume(millis()); // Consume even on malformed/wrong signatures.
    uint8_t supplied[32] = {}, expected[32];
    bool validHex = unhex(signature, supplied, sizeof(supplied));
    br_hmac_key_context key;
    br_hmac_context ctx;
    br_hmac_key_init(&key, &br_sha256_vtable, pinKey, sizeof(pinKey));
    br_hmac_init(&ctx, &key, 0);
    br_hmac_update(&ctx, nonceHex, 32);
    br_hmac_update(&ctx, ":", 1);
    br_hmac_update(&ctx, endpoint.c_str(), endpoint.length());
    br_hmac_update(&ctx, ":", 1);
    br_hmac_update(&ctx, payload.c_str(), payload.length());
    br_hmac_out(&ctx, expected);
    uint8_t difference = 0;
    for (size_t i = 0; i < sizeof(expected); ++i) difference |= expected[i] ^ supplied[i];
    // Both nonce and MAC comparisons are independent of the first differing byte.
    uint8_t nonceDifference = nonce.length() == 32 ? 0 : 1;
    for (size_t i = 0; i < 32; ++i) nonceDifference |= nonceHex[i] ^ (i < nonce.length() ? nonce[i] : 0);
    if (!usable || !validHex || difference || nonceDifference) { policy.failure(millis()); return false; }
    policy.success();
    return true;
}
bool AuthManager::changePin(const String& salt, const String& key) {
    uint8_t saltBytes[16], nextKey[32];
    if (!initialized || !unhex(salt, saltBytes, 16) || !unhex(key, nextKey, 32) || salt == saltHex) return false;
    if (!persist(salt.c_str(), nextKey, false)) return false;
    memcpy(pinKey, nextKey, sizeof(pinKey));
    salt.toCharArray(saltHex, sizeof(saltHex));
    mustChange = false;
    policy.active = false;
    return true;
}
