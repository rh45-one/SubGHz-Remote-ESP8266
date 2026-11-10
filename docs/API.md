# Authenticated API contract (v0.3.1)

The device uses a bounded HTTP/1.1 implementation: 1024-byte headers, 8192-byte body, one request per connection, 2.5-second receive deadline. `Host`, a single decimal `Content-Length`, and `Content-Type: application/json` are required for POST. Chunking, Expect/100-continue, duplicate lengths, and folded headers are rejected **before** body allocation. The backend relay never redirects or retries RF requests and bounds both response size and total timeout.

## Exact signed bytes

`GET /api/auth/challenge` returns lowercase hex `salt` (16 bytes), `nonce` (16 bytes), and `expires_in_ms: 10000`. Only one nonce is outstanding; issuing another replaces it. Serialize protected operations across tabs/clients or expect authentication failures.

Derive `pin_key = SHA256(bytes.fromhex(salt) + PIN.encode('utf-8'))`.

PINs are strings of 4–12 ASCII digits; leading zeroes are significant. The factory default is `0000` and must be changed before RF use. Existing `/auth.json` credentials survive firmware updates. The device receives derived key material, not plaintext PINs, so PIN length/digit restrictions are enforced by the client UI rather than inferred from a SHA256 hash. Status advertises `pin_min_length: 4` and `pin_max_length: 12`.

Protected requests use an envelope (the inner JSON is deliberately a **string**, not an object):
```json
{"nonce":"<32 lowercase hex>","signature":"<64 lowercase hex>","payload":"{\"frequency_hz\":433920000,\"pulses\":[350,-1050,350,-10850],\"repeats\":2}"}
```

The signature is lowercase hexadecimal `HMAC-SHA256(pin_key, UTF8(nonce + ':' + endpoint + ':' + payload))`, where `endpoint` is the exact **device path**, for example `/api/transmit`. This defines AGENTS.md's `action_string` as `endpoint + ':' + payload`. Every parameter, including frequency, pulse order, repeats, capture window, and replacement credential material, is bound. No JSON canonicalization or backend reserialization of the inner payload is permitted.

The device consumes the nonce before MAC comparison, even on malformed signatures. Expired/replayed/wrong-nonce attempts fail and count toward lockout. Five consecutive failures cause 60 seconds of HTTP 429; valid verification resets the failure counter. Timers handle unsigned rollover. Status remains readable during lockout. Lockout is volatile and does not survive a reboot.

## Device endpoints

| Method/path | Payload/result |
| --- | --- |
| GET `/api/status` | Radio/auth availability, version, lockout, mandatory PIN change, uptime |
| GET `/api/auth/challenge` | Salt, single-use nonce, 10-second TTL |
| POST `/api/auth/verify` | Signed `{}`; returns `must_change_pin` |
| POST `/api/auth/change-pin` | Signed `{"salt":"<new 32 hex>","new_pin_key":"<new 64 hex>"}` derived from the new PIN and fresh random salt |
| POST `/api/record/start` | Signed `{"frequency_hz":433920000,"window_ms":10000}`; window 1–10000 ms |
| GET `/api/record/status` | State 0 idle, 1 recording, 2 complete, 3 timeout, 4 overflow. Only complete captures include waveform |
| POST `/api/transmit` | Signed waveform, or fixed-code fields as below |

Raw waveform: integer `frequency_hz`, alternating signed integer `pulses` (2–512, abs 1–32767, positive first), integer `repeats` (1–20). Maximum sum(abs(pulses)) × repeats is 2,000,000 µs. Fixed alternative: `frequency_hz`, `code` (uint32), `bits` (24/32), `pulse_us` (100–1057), `protocol: 1`, `repeats`. Capture/TX are refused while already recording. Default credentials cannot perform RF operations. No stop endpoint is exposed: capture is bounded or cancellable over local serial.

Errors include 400 validation, 401 bad authentication, 403 mandatory PIN change, 409 recording busy, 429 lockout, 503 unavailable storage/radio. PIN material must be persisted successfully before a change is applied in RAM. LittleFS uses temporary-write/flush/rename and never automatically formats a damaged store. An orphan temporary provisioning file fails closed. Power-loss behavior still needs physical validation.

## GUI routes

- `GET /api/config`: simulated/real mode; no credentials.
- `GET/POST /api/signals`, `PUT/DELETE /api/signals/{id}`: validated signal library CRUD.
- `GET /api/signals/{id}/export`: RAW `.sub` attachment.
- `POST /api/flipper/convert`: `{"text":"...","name":"..."}` → normalized signal and warnings.
- `POST /api/flipper/export`: waveform → `{"text":"..."}`.
- `GET/POST /api/device/{endpoint}`: fixed allowlist proxy. POST relays the signed envelope unchanged. It never derives keys or receives PINs from the UI.

GUI writes require `X-Requested-With: subghz-gui`, JSON, explicit bounded Content-Length, trusted Host, and a matching Origin when present. No CORS is enabled. These protect browser-origin requests, **not unauthorized direct tailnet clients**; signal-library access is governed by Tailscale ACLs. The firmware is the final gatekeeper for RF. The 60-second arm/hold controls are browser accident protection, not an additional firmware authorization token.

## Security limits

HMAC is not encryption or a password-hardening KDF. A captured MAC allows offline guessing of short numeric PINs; LAN HTTP exposes replacement `new_pin_key` during PIN changes. Restrict LAN access, use a long random PIN, secure the homelab, and use Tailscale HTTPS for the browser hop. Do not expose either service publicly. The firmware's failure limiter cannot stop Wi-Fi jamming, nonce replacement DoS, or physical credential replacement. Once a TX request has reached the device, locking/aborting the browser cannot retract it. A timeout is an **unknown outcome**, never a reason for automatic retry.

---

## Complete Request & Response Examples

### Part 1: ESP8266 Hardware API (`http://192.168.1.195`)

*(When calling via the Web GUI proxy instead of directly hitting the ESP8266, replace `/api/<endpoint>` with `/api/device/<endpoint>` and include header `X-Requested-With: subghz-gui`. Note that `signature` is still computed over the device path `/api/<endpoint>`.)*

#### 1. `GET /api/status` — Health & CC1101 Status (Unauthenticated)
Polled every 3 seconds by the Web UI status light to verify ESP8266 reachability and `cc1101_ok: true`.

**Request:**
```http
GET /api/status HTTP/1.1
Host: 192.168.1.195
```

**Response (`200 OK`):**
```json
{
  "version": "0.3.1",
  "pin_min_length": 4,
  "pin_max_length": 12,
  "cc1101_ok": true,
  "auth_ready": true,
  "must_change_pin": true,
  "locked_out": false,
  "lockout_remaining_s": 0,
  "uptime_s": 1420
}
```

#### 2. `GET /api/auth/challenge` — Issue Single-Use Nonce
**Request:**
```http
GET /api/auth/challenge HTTP/1.1
Host: 192.168.1.195
```

**Response (`200 OK`):**
```json
{
  "salt": "00112233445566778899aabbccddeeff",
  "nonce": "ffeeddccbbaa99887766554433221100",
  "expires_in_ms": 10000
}
```

#### 3. `POST /api/auth/verify` — Verify PIN (Unlock Remote)
Signs `"{}"` with `HMAC-SHA256(pin_key, "<nonce>:/api/auth/verify:{}")`.

**Request:**
```http
POST /api/auth/verify HTTP/1.1
Host: 192.168.1.195
Content-Type: application/json
Content-Length: 132

{
  "nonce": "ffeeddccbbaa99887766554433221100",
  "signature": "6c89c9e47f0a12b384e01c2d4a5b6c7d8e9f00112233445566778899aabbccdd",
  "payload": "{}"
}
```

**Response (`200 OK`):**
```json
{
  "must_change_pin": false
}
```

#### 4. `POST /api/auth/change-pin` — Change PIN
Signed with the **current** `pin_key`. Sends a new random 16-byte `salt` (32 hex chars) and `new_pin_key = SHA256(bytes.fromhex(new_salt) + new_pin.encode('utf-8')).hexdigest()` (64 hex chars).

**Request:**
```http
POST /api/auth/change-pin HTTP/1.1
Host: 192.168.1.195
Content-Type: application/json
Content-Length: 263

{
  "nonce": "ffeeddccbbaa99887766554433221100",
  "signature": "91a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f80",
  "payload": "{\"salt\":\"a1b2c3d4e5f60718293a4b5c6d7e8f90\",\"new_pin_key\":\"4e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f\"}"
}
```

**Response (`200 OK`):**
```json
{
  "ok": true
}
```

#### 5A. `POST /api/transmit` — Transmit Raw Pulse Timings (`RAW_Data`)
Sends alternating signed microsecond pulses (`+mark` ON, `-space` OFF, 2–512 pulses, positive first) and `repeats` (1–20).

**Request:**
```http
POST /api/transmit HTTP/1.1
Host: 192.168.1.195
Content-Type: application/json
Content-Length: 205

{
  "nonce": "ffeeddccbbaa99887766554433221100",
  "signature": "3f4e5d6c7b8a9900112233445566778899aabbccddeeff001122334455667788",
  "payload": "{\"frequency_hz\":433920000,\"pulses\":[350,-1050,1050,-350,350,-10850],\"repeats\":3}"
}
```

**Response (`200 OK`):**
```json
{
  "ok": true
}
```

#### 5B. `POST /api/transmit` — Transmit Fixed Code (`rc-switch` Protocol 1)
Alternative `payload` format for 24-bit or 32-bit fixed codes (`Princeton` / PT2262 / EV1527):

**Request:**
```http
POST /api/transmit HTTP/1.1
Host: 192.168.1.195
Content-Type: application/json
Content-Length: 215

{
  "nonce": "ffeeddccbbaa99887766554433221100",
  "signature": "7a8b9c0d1e2f3a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3",
  "payload": "{\"frequency_hz\":433920000,\"code\":5592405,\"bits\":24,\"pulse_us\":350,\"protocol\":1,\"repeats\":5}"
}
```

**Response (`200 OK`):**
```json
{
  "ok": true
}
```

#### 6. `POST /api/record/start` — Start RF Capture Window
Arms the CC1101 receiver at `frequency_hz` (`300–348 MHz`, `387–464 MHz`, or `779–928 MHz`) for up to `10000` ms (`window_ms`).

**Request:**
```http
POST /api/record/start HTTP/1.1
Host: 192.168.1.195
Content-Type: application/json
Content-Length: 178

{
  "nonce": "ffeeddccbbaa99887766554433221100",
  "signature": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
  "payload": "{\"frequency_hz\":433920000,\"window_ms\":10000}"
}
```

**Response (`200 OK`):**
```json
{
  "ok": true
}
```

#### 7. `GET /api/record/status` — Poll RF Capture Result
`state` values: `0` = idle, `1` = recording, `2` = complete, `3` = timeout, `4` = overflow.

**Request:**
```http
GET /api/record/status HTTP/1.1
Host: 192.168.1.195
```

**Response while recording (`state: 1`):**
```json
{
  "state": 1
}
```

**Response when complete (`state: 2`):**
```json
{
  "state": 2,
  "frequency_hz": 433920000,
  "repeats": 1,
  "pulses": [350, -1050, 350, -1050, 1050, -350, 350, -10850]
}
```

---

### Part 2: Docker Web GUI API

*(All write requests `POST`, `PUT`, `DELETE` require header `X-Requested-With: subghz-gui`.)*

#### 8. `GET /api/config` — Check Simulated vs. Real Mode
**Request:**
```http
GET /api/config HTTP/1.1
Host: localhost:7915
```

**Response (`200 OK`):**
```json
{
  "simulated": false
}
```

#### 9. `GET /api/signals` — List Saved Signals in Docker Volume
**Request:**
```http
GET /api/signals HTTP/1.1
Host: localhost:7915
```

**Response (`200 OK`):**
```json
[
  {
    "id": "a1b2c3d4e5f6",
    "name": "Garage Door",
    "category": "Home",
    "frequency_hz": 433920000,
    "pulses": [350, -1050, 1050, -350, 350, -10850],
    "repeats": 3,
    "source_protocol": "Princeton"
  }
]
```

#### 10. `POST /api/signals` — Save a New Signal
**Request:**
```http
POST /api/signals HTTP/1.1
Host: localhost:7915
X-Requested-With: subghz-gui
Content-Type: application/json
Content-Length: 147

{
  "name": "Garage Door",
  "category": "Home",
  "frequency_hz": 433920000,
  "pulses": [350, -1050, 1050, -350, 350, -10850],
  "repeats": 3,
  "source_protocol": "RAW"
}
```

**Response (`201 Created`):**
```json
{
  "id": "a1b2c3d4e5f6",
  "name": "Garage Door",
  "category": "Home",
  "frequency_hz": 433920000,
  "pulses": [350, -1050, 1050, -350, 350, -10850],
  "repeats": 3,
  "source_protocol": "RAW"
}
```

#### 11. `PUT /api/signals/{signal_id}` — Update / Rename a Signal
**Request:**
```http
PUT /api/signals/a1b2c3d4e5f6 HTTP/1.1
Host: localhost:7915
X-Requested-With: subghz-gui
Content-Type: application/json
Content-Length: 151

{
  "name": "Main Garage",
  "category": "Driveway",
  "frequency_hz": 433920000,
  "pulses": [350, -1050, 1050, -350, 350, -10850],
  "repeats": 3,
  "source_protocol": "RAW"
}
```

**Response (`200 OK`):** Returns the updated signal object.

#### 12. `DELETE /api/signals/{signal_id}` — Delete a Signal
**Request:**
```http
DELETE /api/signals/a1b2c3d4e5f6 HTTP/1.1
Host: localhost:7915
X-Requested-With: subghz-gui
```

**Response (`200 OK`):**
```json
{
  "ok": true
}
```

#### 13. `GET /api/signals/{signal_id}/export` — Download Signal as Flipper `.sub`
**Request:**
```http
GET /api/signals/a1b2c3d4e5f6/export HTTP/1.1
Host: localhost:7915
```

**Response (`200 OK`, `text/plain`):**
```text
Filetype: Flipper SubGhz RAW File
Version: 1
Frequency: 433920000
Preset: FuriHalSubGhzPresetOok650Async
Protocol: RAW
RAW_Data: 350 -1050 1050 -350 350 -10850
```

#### 14. `POST /api/flipper/convert` — Parse & Synthesize a Flipper Zero `.sub` File
Supports `RAW`, `BinRAW`, `Princeton`, `CAME`, `Nice FLO`, `Linear`, `GateTX`, and `Holtek`.

**Request:**
```http
POST /api/flipper/convert HTTP/1.1
Host: localhost:7915
X-Requested-With: subghz-gui
Content-Type: application/json
Content-Length: 215

{
  "name": "Imported Key",
  "text": "Filetype: Flipper SubGhz Key File\nVersion: 1\nFrequency: 433920000\nPreset: FuriHalSubGhzPresetOok650Async\nProtocol: Princeton\nBit: 24\nKey: 00 00 00 00 00 55 55 55\nTE: 350\n"
}
```

**Response (`200 OK`):**
```json
{
  "name": "Imported Key",
  "category": "",
  "frequency_hz": 433920000,
  "pulses": [350, -1050, 1050, -350, 350, -10850],
  "repeats": 3,
  "source_protocol": "Princeton",
  "warnings": []
}
```

#### 15. `POST /api/flipper/export` — Convert Waveform Array to `.sub` Text
**Request:**
```http
POST /api/flipper/export HTTP/1.1
Host: localhost:7915
X-Requested-With: subghz-gui
Content-Type: application/json
Content-Length: 73

{
  "frequency_hz": 433920000,
  "pulses": [350, -1050, 350, -10850],
  "repeats": 2
}
```

**Response (`200 OK`):**
```json
{
  "text": "Filetype: Flipper SubGhz RAW File\nVersion: 1\nFrequency: 433920000\nPreset: FuriHalSubGhzPresetOok650Async\nProtocol: RAW\nRAW_Data: 350 -1050 350 -10850 350 -1050 350 -10850\n"
}
```
