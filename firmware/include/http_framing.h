#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace HttpFraming {
struct Request {
    int error = 400;
    bool post = false;
    size_t length = 0;
    char path[64] = {};
};
inline bool equals(const char* value, size_t length, const char* expected) {
    if (strlen(expected) != length) return false;
    for (size_t i = 0; i < length; ++i) {
        char c = value[i];
        if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
        if (c != expected[i]) return false;
    }
    return true;
}
// Strict single-request HTTP subset: no chunking, pipelining, folded headers or ambiguous lengths.
inline Request parse(const char* header, size_t size) {
    Request result;
    if (!header || size < 4 || size > 1024 || memcmp(header + size - 4, "\r\n\r\n", 4)) return result;
    for (size_t i = 0; i < size; ++i) if (!header[i] || static_cast<unsigned char>(header[i]) > 127) return result;
    const char* end = header + size;
    const char* lineEnd = strstr(header, "\r\n");
    if (!lineEnd) return result;
    size_t method = 0;
    if (!strncmp(header, "GET ", 4)) method = 4;
    else if (!strncmp(header, "POST ", 5)) { method = 5; result.post = true; }
    else { result.error = 405; return result; }
    const char* space = static_cast<const char*>(memchr(header + method, ' ', lineEnd - header - method));
    if (!space || lineEnd - space != 9 || memcmp(space, " HTTP/1.1", 9)) return result;
    size_t pathLength = space - header - method;
    if (!pathLength || pathLength >= sizeof(result.path) || header[method] != '/') return result;
    memcpy(result.path, header + method, pathLength);
    bool hasLength = false, host = false, json = false;
    for (const char* field = lineEnd + 2; field < end - 2;) {
        lineEnd = strstr(field, "\r\n");
        if (!lineEnd) return result;
        const char* colon = static_cast<const char*>(memchr(field, ':', lineEnd - field));
        if (!colon || colon == field) return result;
        for (const char* p = field; p < colon; ++p) {
            if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') || (*p >= '0' && *p <= '9') || *p == '-')) return result;
        }
        const char* value = colon + 1;
        while (value < lineEnd && (*value == ' ' || *value == '\t')) ++value;
        const char* valueEnd = lineEnd;
        while (valueEnd > value && (valueEnd[-1] == ' ' || valueEnd[-1] == '\t')) --valueEnd;
        if (equals(field, colon - field, "transfer-encoding") || equals(field, colon - field, "expect")) return result;
        if (equals(field, colon - field, "host")) {
            if (host || value == valueEnd) return result;
            host = true;
        }
        if (equals(field, colon - field, "content-type")) {
            if (json || !equals(value, valueEnd - value, "application/json")) return result;
            json = true;
        }
        if (equals(field, colon - field, "content-length")) {
            if (hasLength || value == valueEnd || valueEnd - value > 5) return result;
            hasLength = true;
            for (const char* p = value; p < valueEnd; ++p) {
                if (*p < '0' || *p > '9') return result;
                result.length = result.length * 10 + (*p - '0');
            }
        }
        field = lineEnd + 2;
    }
    if (!host) return result;
    if (result.length > 8192) { result.error = 413; return result; }
    if (result.post && (!hasLength || !result.length)) { result.error = 411; return result; }
    if (result.post && !json) { result.error = 415; return result; }
    if (!result.post && result.length) return result;
    result.error = 0;
    return result;
}
}
