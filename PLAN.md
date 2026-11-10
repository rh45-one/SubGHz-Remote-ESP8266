# SubGHz-Remote-ESP8266 Development Plan

## 1. Project Overview & Goal
Connect a **NodeMCU (ESP8266MOD)** to a **CC1101 V2.0 (433 MHz)** transceiver to serve a cryptographically authenticated HTTP REST API capable of recording and transmitting Sub-GHz RF signals (garage door opener, RF light switches, etc.). Pair the ESP8266 with a **Dockerized Python FastAPI + Mobile Web GUI** hosted on a homelab and accessed via **Tailscale**, featuring a **Flipper Zero `.sub` file converter**, **anti-accidental-trigger UX**, and **persistent Docker volume signal storage**.

---

## 2. Security & Storage Architecture

### 2.1 Three-Layer Security & Anti-Accidental-Trigger Model
Because the ESP8266 communicates over LAN HTTP while the mobile browser connects to the homelab Docker GUI via Tailscale, security is enforced across three layers:

1. **Layer 1 — ESP8266 Hardware Challenge-Response (`HMAC-SHA256` + Single-Use Nonce)**:
   - **Zero Plaintext PIN Storage**: The ESP8266 stores only a 16-byte random `salt` and `pin_key = SHA256(salt || PIN)` in `LittleFS` (`/auth.json`).
   - **Single-Use Challenge (`nonce`)**: `GET /api/auth/challenge` issues the `salt` and a cryptographically random 16-byte `nonce` valid for 10 seconds.
   - **Replay-Proof Signature**: For any protected operation (`POST /api/transmit`, `POST /api/record/start`, `POST /api/auth/change-pin`), the client/GUI computes `pin_key = SHA256(salt || PIN)` and sends `signature = HMAC-SHA256(pin_key, nonce || action_payload)`.
   - **Constant-Time Verification & Brute-Force Lockout**: The ESP8266 verifies `signature` in constant time, immediately invalidates the `nonce` (preventing LAN packet sniffing or replay attacks), and locks out all requests for **60 seconds (`HTTP 429`)** after **5 consecutive failed attempts**.
2. **Layer 2 — 60-Second Armed Session & Instant Auto-Lock**:
   - Entering the PIN on the mobile keypad unlocks an **Armed Window for 60 seconds** (displayed with a visual countdown bar).
   - The UI **immediately wipes the PIN from browser memory and re-locks**:
     - After a signal transmission completes,
     - When the 60-second timer expires,
     - Whenever the phone screen locks, switches apps, or hides the browser tab (`document.visibilitychange` / `window.blur`).
3. **Layer 3 — Hold-for-0.8s to Transmit (Pocket-Dial Prevention)**:
   - Even while unlocked, a single tap on a signal button **never** triggers transmission.
   - The user must **press and hold the button for 0.8 seconds** (accompanied by a filling progress ring and `navigator.vibrate` haptic feedback) before the transmit request is dispatched.

### 2.2 Storage Split: Docker Container (Signals) vs. ESP8266 (Auth)
- **Docker Container Volume (`/app/data/signals.json`) — Primary Signal Library**:
  - Stores all recorded Sub-GHz signals, imported Flipper Zero `.sub` files, raw microsecond pulse arrays, fixed-code metadata, names, and categories.
  - Protects the ESP8266's limited `80 KB` RAM from JSON heap fragmentation, supports unlimited signals, and survives ESP8266 reflashes or board replacements.
- **ESP8266 `LittleFS` (`/auth.json`) — Security Gatekeeper**:
  - Stores only `/auth.json` (`salt`, `pin_key`, `must_change_pin`).
  - The ESP8266 operates as a lean, stateless RF transceiver: it receives one authenticated signal payload (`int16_t timings[512]` or fixed code) per `POST /api/transmit` request, transmits it over `GDO0` (`D4`), and returns the CC1101 to Idle High-Impedance mode.

---

## 3. Phased Roadmap

### Phase 1: CC1101 Wiring & Hardware Diagnostic Firmware ✅ (Completed)
- **Objective**: Verify physical wiring between the 8-pin CC1101 V2.0 module and the NodeMCU ESP8266, validate SPI register reads/writes, test `GDO0` (`D4`) and `GDO2` (`RX`) continuity, and confirm 433.92 MHz OOK reception.
- **Verified Results on COM4**:
  - `[1]` Power & MISO Ready (`D6` Yellow + `D8` Pink + `3V3`/`GND`): **PASS**
  - `[2]` SPI Chip Identity (`D5` Green SCK + `D7` Orange MOSI): **PASS** (`PARTNUM=0x00, VERSION=0x14`)
  - `[3]` SPI Bidirectional Register R/W (`IOCFG0`): **PASS**
  - `[4]` `GDO0` Wire Test (`Pin 3` Blue -> `D4`/`GPIO2`): **PASS**
  - `[5]` `GDO2` Wire Test (`Pin 8` Grey -> `RX`/`GPIO3`): **PASS** (and set to `0x2E` High-Impedance)
  - Live RF test with 433.92 MHz light remote: **PASS** (RSSI jumped from `-92 dBm` to `-52 dBm`, pulse widths `125..9681 µs` captured on `D4`).

---

### Phase 2: Sub-GHz Capture & Replay Engine (Stateless ESP8266 RF Engine) (Implemented; hardware verification pending)
- **Idle High-Impedance (`High-Z`) Protection**:
  - Keep CC1101 in `SIDLE` with `IOCFG0 = 0x2E` and `IOCFG2 = 0x2E` whenever idle so `D4` (`GPIO2`) stays `HIGH` (preventing ESP8266 `boot mode:(2,6)` crashes on reset) and `RX` (`GPIO3`) stays free (allowing USB firmware uploads without unplugging the CC1101).
- **Unified Capture Engine (`subghz_engine`)**:
  - Single `IRAM_ATTR` interrupt handler on `D4` (`GDO0`) that records signed microsecond pulse timings (`+mark`, `-space`, up to 512 transitions per frame) into a static RAM buffer and also decodes standard 24/32-bit fixed codes (`Princeton` / PT2262 / EV1527).
- **Stateless Transmit Engine**:
   - Replays either signed microsecond raw pulse arrays (`int16_t` `+mark`/`-space` durations) or `rc-switch` fixed codes over `GDO0` (`D4`) at the requested frequency (`300–348 MHz`, `387–464 MHz`, `779–928 MHz`), then immediately returns CC1101 to Idle High-Z.

#### Implementation & verification status
- Added `subghz_engine` with one ISR, a static 512-pulse capture buffer, bounded (maximum 10-second) recording windows, timeout/overflow reporting, and High-Z cleanup.
- Added 24/32-bit rc-switch **protocol 1** encoding/decoding (Princeton / compatible PT2262 / EV1527 waveforms). Other rc-switch protocols are not yet supported.
- Raw/fixed replay primitives validate frequency, pulse signs/ranges, 1–20 repeats, and a maximum two-second total pulse duration. The Phase 3 HTTP entrypoint requires authentication; no serial TX command exists.
- Replaced perpetual diagnostic RX with explicit serial capture commands. Nothing records or transmits automatically at boot.
- ESP8266 firmware build and host-side simulated engine/codec tests pass. Physical capture, TX waveform timing, reset/High-Z behavior, and USB reflash checks remain pending; no hardware was flashed during implementation.
- Capture segmentation currently uses a 5 ms low interval observed at an edge, or 12 ms trailing silence. Noisy/partial frames and protocols with shorter repeat gaps need further hardware characterization.
- Phase 3 authentication now gates network replay. Physical acceptance remains pending.

---

### Phase 3: Secure ESP8266 REST API & Challenge-Response PIN Auth (Implemented; device integration verification pending)
- **Challenge-Response Auth Manager (`auth_manager`)**:
   - Persists `salt` (16 bytes) and `pin_key = SHA256(salt || PIN)` in `/auth.json` on `LittleFS` (initial default PIN `0000`, UI policy 4–12 digits, with `must_change_pin = true`). Firmware updates preserve already-provisioned credentials.
  - Generates 16-byte single-use `nonce` tokens with a 10-second TTL.
  - Verifies `HMAC-SHA256(pin_key, nonce || payload)` using BearSSL (`br_hmac_key_context` / `br_hmac_context`) and constant-time comparison.
  - Enforces a 60-second hardware lockout after 5 consecutive failed attempts.
- **ESP8266 REST API Endpoints (`api_server`)**:
  - `GET /api/status` — Unauthenticated status (`cc1101_ok`, `version`, `locked_out`, `lockout_remaining_s`, `must_change_pin`, `uptime_s`).
  - `GET /api/auth/challenge` — Returns `{"salt": "<hex>", "nonce": "<hex>", "expires_in_ms": 10000}`.
  - `POST /api/auth/verify` — Verifies a signed challenge to unlock the GUI session and check PIN validity.
  - `POST /api/auth/change-pin` — Verifies current PIN signature and updates `/auth.json` with a new random `salt` and `new_pin_key`.
  - `POST /api/record/start` & `GET /api/record/status` — Arms CC1101 receiver for a capture window (e.g., 10s) and returns captured raw timings + decoded fixed code so the Docker GUI can save it.
  - `POST /api/transmit` — Verifies challenge-response HMAC signature and transmits the provided raw pulse array or fixed code.

---

### Phase 4: Dockerized Homelab GUI, Signal Library & Flipper `.sub` Converter (Implemented; homelab/mobile acceptance pending)
- **Docker Signal Store (`gui/app/signal_store.py` -> `/app/data/signals.json`)**:
  - Thread-safe JSON/SQLite persistence in a mounted Docker volume (`/app/data`) for all recorded signals and imported Flipper `.sub` files.
- **Flipper Zero `.sub` Parser, Converter & Exporter (`gui/app/flipper_sub.py`)**:
  - **`Flipper SubGhz RAW File` (`Protocol: RAW`)**: Parses `Frequency`, `Preset`, and `RAW_Data:`, isolates the cleanest single burst across inter-frame gaps, normalizes `+mark`/`-space` timings to `<= 512` pulses, and configures repeat counts.
  - **`Flipper SubGhz Key File` (`Princeton`, `CAME`, `Nice FLO`, `Linear`, `GateTX`, `Holtek`)**: Synthesizes exact microsecond `+mark`/`-space` pulse arrays (and fixed-code metadata) from `Key`, `Bit`, and `TE`.
  - **`Protocol: BinRAW`**: Expands `Te`, `Bit_RAW`, and `Data_RAW` hex payloads into signed microsecond timings.
  - **Export to `.sub`**: Exports any saved signal in the Docker library as a standard `.sub` file.
- **FastAPI Backend & Mobile Web UI (`gui/`)**:
   - **Two-button Remote Home**: Compact daily-use screen, PIN dialog, visible 60s armed window, auto-lock on send/hide, and **Hold-for-0.8s** trigger buttons. No management tools on the mobile home screen.
   - **Settings → Signals**: Library CRUD, preview/export, and assignments for the two remote buttons.
   - **Settings → Converter**: Upload/paste `.sub` files, preview waveform, test-send, or save to the Docker signal library.
   - **Settings → Recorder**: Trigger ESP8266 RF capture, preview waveform, test replay, save to the Docker signal library, or export `.sub`.
   - **Settings → Security / Appearance**: Change PIN, inspect CC1101 status, and select black/orange or white/orange mode. Self-hosted pixel typography and plain backgrounds, inspired by Flipper-style interfaces.

## 4. Current software delivery & next work

- Firmware stores only salted PIN material; BearSSL signs/verifies the exact contract in `docs/API.md`. Every protected operation consumes a nonce, has brute-force lockout, and blocks RF use of the default credential. PIN changes bind replacement material to the old-key signature.
- Bounded HTTP framing is checked before allocating request bodies; Wi-Fi is opt-in through gitignored `secrets.h`. LittleFS fails closed without autoformat; local serial `F` is explicit recovery only when initialization failed.
- Docker GUI owns signal persistence. Browser-only Web Crypto signing means no backend PIN environment variable, volume, or server session secret. Arming retains a non-extractable signing key, not the plaintext PIN, and drops references on send/timeout/hide/blur/page exit. Test-send and assigned remote buttons share the same 800 ms hold gate. Browser storage contains only display preferences and assigned signal IDs.
- Converter supports OOK RAW, BinRAW, Princeton, CAME, Nice FLO, Linear, GateTX, and Holtek, with validation and warnings rather than unsupported protocol guessing. GUI has library CRUD, conversion, waveform previews, explicit recording, export, and diagnostics/PIN change.
- Explicit offline simulator cannot connect to hardware. Tests use only fake GPIO/radio clocks, simulator requests, and mock HTTP transports. Dependency versions are pinned; runtime audit passes. CI builds both firmware variants and the Docker image.
- **Remaining:** execute the physical/homelab acceptance checklist in `docs/VERIFICATION.md`, characterize RF capture segmentation/noise, validate ESP8266 heap/watchdog/flash behavior under load, and test on real mobile browsers. The simulator is not an emulator or proof of hardware safety. Local Docker daemon was unavailable; no hardware interaction occurred during this implementation.
