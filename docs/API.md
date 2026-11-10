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
