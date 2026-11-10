#pragma once
#include <Arduino.h>

namespace AuthManager {
bool begin();
bool ready();
bool mustChangePin();
bool locked();
uint32_t lockoutRemaining();
const char* salt();
bool challenge(char nonce[33]);
// action = endpoint + ":" + exact UTF-8 payload JSON string.
bool verify(const String& nonce, const String& signature, const String& endpoint, const String& payload);
bool changePin(const String& saltHex, const String& keyHex);
}
