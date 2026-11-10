# AGENTS.md — SubGHz-Remote-ESP8266 Developer & AI Agent Guide

This file is the authoritative technical reference for AI coding agents and developers working on **SubGHz-Remote-ESP8266**. Read this document completely before modifying firmware, hardware pin configurations, serial flashing scripts, authentication logic, or the GUI backend.

---

## 1. Project Mission & Architecture

- **Hardware**: NodeMCU 1.0 (`ESP-12E` / `ESP8266MOD` with Silicon Labs `CP2102` USB-UART bridge) wired to a **CC1101 V2.0 (8-pin, 433 MHz SMA)** Sub-GHz transceiver.
- **Firmware (`firmware/`)**: PlatformIO (`espressif8266`, `nodemcuv2`, Arduino framework) acting as a **stateless, cryptographically authenticated RF transceiver**. It stores only PIN authentication material (`/auth.json`) in `LittleFS`, verifies `HMAC-SHA256` challenge-response signatures, records Sub-GHz signals into a static RAM buffer, and transmits authenticated signal payloads over `GDO0` (`D4`).
- **Homelab GUI (`gui/`)**: Dockerized Python **FastAPI** backend + mobile-first Web UI accessed over **Tailscale**. Owns the **persistent signal library (`/app/data/signals.json`)**, includes a **Flipper Zero `.sub` converter/exporter** (`RAW`, `BinRAW`, and `Key` protocols), and implements **3-layer anti-accidental-trigger security**.

---

## 2. Hardware Pinout & Wiring Reference (`rysunek1.webp`)

| CC1101 Pin | Signal | Wire Color | NodeMCU Label | ESP8266 GPIO | Hardware Role |
| :---: | :--- | :--- | :---: | :---: | :--- |
| **1** | `GND` | Black | `GND` | — | Common Ground |
| **2** | `VCC` | Red | `3V3` | — | **3.3V ONLY** (CC1101 is **not** 5V tolerant) |
| **3** | `GDO0` | Blue | `D4` | `GPIO2` | Primary Async OOK/ASK TX & RX data + interrupts |
| **4** | `CSN` | Pink | `D8` | `GPIO15` | Hardware SPI Chip Select (`SS`) |
| **5** | `SCK` | Green | `D5` | `GPIO14` | Hardware SPI Clock (`SCK`) |
| **6** | `MOSI` | Orange | `D7` | `GPIO13` | Hardware SPI MOSI (`SI`) |
| **7** | `MISO` | Yellow | `D6` | `GPIO12` | Hardware SPI MISO (`SO` / `GDO1`) |
| **8** | `GDO2` | Grey | `RX` | `GPIO3` | Secondary CC1101 `GDO2` (shared with UART0 `RX`) |

---

## 3. Critical Hardware & Firmware Invariants (MUST FOLLOW)

These invariants were discovered and verified on the physical hardware (`COM4`, ESP8266 MAC `40:f5:20:27:78:c9`, CC1101 `PARTNUM=0x00, VERSION=0x14`):

### Invariant 1: `D4` (`GPIO2` / `GDO0`) Boot-Strap Protection
- **Why**: On the ESP8266, `GPIO2` (`D4`) is a hardware boot-strap pin (`boot_mode = (GPIO15 << 2) | (GPIO0 << 1) | GPIO2`). Normal SPI flash boot requires `boot mode:(3,x)` (`GPIO15=0, GPIO0=1, GPIO2=1`).
- **The Trap**: If the CC1101 is left in a state that drives `GDO0` (`D4`) `LOW` (`0V`)—such as `IOCFG0 = 0x06` (packet sync word) or active OOK RX mode with no carrier—and the ESP8266 resets (via `RST`, watchdog, or USB serial `DTR`/`RTS` toggle) while the CC1101 stays powered, the ESP8266 samples `GPIO2 = 0` and enters **`boot mode:(2,6)`**, immediately crashing in a loop with `Fatal exception (0): epc1=0x40100003`.
- **The Rule**:
  1. Whenever the CC1101 is **idle** (not actively inside a recording window or transmitting a signal burst), firmware **MUST** place the CC1101 in `SIDLE` (`0x36`) and write **`0x2E` (High-Impedance / 3-state)** to **`IOCFG0` (`0x02`)**.
  2. With `IOCFG0 = 0x2E`, the NodeMCU's onboard pull-up holds `D4` (`GPIO2`) safely `HIGH` (`3.3V`), guaranteeing clean `boot mode:(3,6)` startups on every reset.

### Invariant 2: `RX` (`GPIO3` / `GDO2`) USB-UART Conflict Protection
- **Why**: The Grey wire connects CC1101 `GDO2` (Pin 8) directly (`0 Ω`) to the NodeMCU `RX` (`GPIO3`) pin, whereas the onboard `CP2102` USB-UART bridge connects to `RX` through a `470 Ω` isolation resistor.
- **The Trap**: On cold power-on reset, the CC1101 defaults `IOCFG2` (`0x00`) to `0x29` (`CHIP_RDYn`), which actively drives `GDO2` `LOW` (`0V`). This clamps `RX` (`GPIO3`) to ground and blocks `esptool.py` packets during USB flashing (`Timed out waiting for packet header`).
- **The Rule**:
  1. Firmware **MUST** write **`0x2E` (High-Impedance / 3-state)** to **`IOCFG2` (`0x00`)** immediately upon boot and after every `ELECHOUSE_cc1101.Init()` or `setCCMode()` call (because `ELECHOUSE_cc1101.setCCMode()` overwrites `IOCFG2` with `0x0B` or `0x0D`!).
  2. Once the running firmware has set `IOCFG0 = 0x2E` and `IOCFG2 = 0x2E`, USB flashing works without unplugging the CC1101. If the CC1101 was just cold-powered and blocks USB upload, temporarily unplugging the CC1101 quick-disconnect (or the Grey `RX` wire) allows `esptool.py` to flash immediately.

### Invariant 3: `SmartRC-CC1101-Driver-Lib` Async Mode (`setCCMode(0)`)
- Always call `ELECHOUSE_cc1101.setCCMode(0)` before `ELECHOUSE_cc1101.setModulation(2)` for Sub-GHz garage/remote OOK operation.
- `ccmode = 1` (the library default) configures packet mode (`PKTCTRL0 = 0x05`, `IOCFG0 = 0x06`), which suppresses raw pulse transitions on `GDO0`. `ccmode = 0` enables transparent asynchronous serial mode (`PKTCTRL0 = 0x32`, `IOCFG0 = 0x0D`), routing demodulated OOK pulses directly to `GDO0` (`D4`) during RX and accepting pulse modulation from `GDO0` (`D4`) during TX.
- Always call `writeReg(CC1101_IOCFG2, 0x2E)` **after** `setCCMode(0)` so `GDO2` (`RX`) remains High-Z.

### Invariant 4: Single Interrupt Handler on `D4` (`GPIO2`)
- The ESP8266 Arduino core allows only **one** `attachInterrupt()` ISR per GPIO pin (`attachInterrupt` overwrites any previous ISR on that pin, including `RCSwitch::enableReceive`).
- Do **not** attach both `RCSwitch::enableReceive(D4)` and a separate `attachInterrupt(D4, ...)` simultaneously. Instead, use a single unified `IRAM_ATTR` interrupt handler in `subghz_engine.cpp` that captures raw microsecond edge deltas (`+mark` / `-space`) and decodes fixed-code frames from the same edge stream.

### Invariant 5: Windows `COM4` (`CP2102`) Serial Port Handling
- On this Windows host, the CP210x driver rejects `460800` baud (`upload_speed = 115200` is required in `platformio.ini`) and raises `WinError 433` / `WinError 31` if `COM4` is reopened within `< 1.5 seconds` of closing or if `SetCommState` is called back-to-back at `9600` then `115200`.
- Always wait `2.0 seconds` after closing `COM4` before reopening it, and open `serial.Serial('COM4', 115200)` with the target baud rate in the constructor.

---

## 4. Security, Storage & UX Architecture (MUST FOLLOW)

### 4.1 Where Data Lives
1. **Signals Live in the Docker Container (`/app/data/signals.json`)**:
   - Do **not** store the signal library on the ESP8266's `LittleFS`. Keeping signals in the Docker container avoids ESP8266 RAM/heap exhaustion on large Flipper `.sub` RAW arrays, allows unlimited signals and metadata, and survives ESP8266 reflashes.
   - When transmitting, the Docker container sends the target signal's pulse array (or fixed code) inside the authenticated `POST /api/transmit` request to the ESP8266.
2. **Authentication State Lives on the ESP8266 (`LittleFS` `/auth.json`)**:
   - The ESP8266 is the final security gatekeeper on the LAN.
   - `/auth.json` stores `salt` (16 random bytes, hex) and `pin_key = SHA256(salt || PIN)` (32 bytes, hex). Never store a plaintext PIN in flash or in Docker environment variables/volumes.

### 4.2 Challenge-Response HMAC Protocol
1. Client/GUI requests `GET /api/auth/challenge` from the ESP8266 -> receives `{"salt": "<32-hex>", "nonce": "<32-hex>", "expires_in_ms": 10000}`.
2. Using the user's entered PIN, client/GUI derives `pin_key = SHA256(bytes.fromhex(salt) + pin.encode('utf-8'))`.
3. Client/GUI computes `signature = HMAC_SHA256(pin_key, nonce_ascii + ":" + action_string)` and includes `nonce` and `signature` in the request to the ESP8266.
4. ESP8266 verifies that `nonce` is active and unexpired, invalidates `nonce` immediately (single-use to prevent Wi-Fi replay), and checks `signature` in constant time using BearSSL `br_hmac`.
5. After **5 consecutive failed verifications**, the ESP8266 locks out all authenticated endpoints for **60 seconds** (`HTTP 429 Too Many Requests`).
6. PIN entry and changes accept **4–12 ASCII digits**, preserving leading zeroes. The factory default is **`0000`**, with mandatory change before RF operations. Updating firmware must preserve already-provisioned authentication material rather than silently resetting a configured PIN.

### 4.3 Mobile Anti-Accidental-Trigger UX Rules
1. **60-Second Armed Window + Auto-Lock**:
   - Entering a valid PIN unlocks the UI for 60 seconds with a visible countdown timer.
   - Immediately wipe the PIN from memory and lock the UI upon: (a) completing a transmission, (b) 60s timeout expiration, or (c) `document.visibilitychange` (`hidden`) / `window.blur`.
2. **Hold-for-0.8s to Transmit**:
   - Single taps on signal buttons must **never** trigger transmission. Require a continuous `800 ms` press-and-hold gesture (with visual progress feedback and `navigator.vibrate` haptic confirmation) while unlocked.
3. **Minimal Remote Home & Settings**:
   - Home is a compact two-button remote on desktop and mobile. Put signal management, conversion, recording, security, and appearance behind Settings; PIN entry is a transient dialog, never a queued transmission.
   - Use plain black/orange and white/orange themes with self-hosted pixel typography. Browser persistence may contain only theme and assigned signal IDs, never PINs or signing keys. Removing an assigned signal must leave a vacant slot rather than silently remapping an action.

---

## 5. Flipper Zero `.sub` Compatibility Standards

- Signed microsecond conventions follow Flipper Zero `.sub` `RAW_Data`:
  - **Positive integers (`+400`)** = RF carrier ON (`HIGH` / mark) in `µs`.
  - **Negative integers (`-400`)** = RF carrier OFF (`LOW` / space) in `µs`.
- Every transmitted burst must begin with a positive mark (`+`) and end with `GDO0` driven `LOW` before returning the CC1101 to `SIDLE` + `IOCFG0 = 0x2E` (High-Z).
- Supported `.sub` input types in `gui/app/flipper_sub.py`:
  1. `Filetype: Flipper SubGhz RAW File` (`Protocol: RAW` — auto-segmented to single burst `<= 512` pulses + repeat count).
  2. `Filetype: Flipper SubGhz Key File` (`Princeton`, `CAME`, `Nice FLO`, `Linear`, `GateTX`, `Holtek` — synthesized to microsecond pulse arrays).
  3. `Protocol: BinRAW` (`Te` + `Data_RAW` bit-run expansion).

---

## 6. Repository Structure

```text
SubGHz-Remote-ESP8266/
├── AGENTS.md                 # This technical reference for agents & developers
├── PLAN.md                   # Phased development plan & architecture
├── README.md                 # Quickstart & wiring guide
├── rysunek1.webp             # Physical wiring diagram (CC1101 <-> NodeMCU)
├── firmware/                 # PlatformIO ESP8266 firmware project
│   ├── platformio.ini        # nodemcuv2 config (115200 baud, LittleFS, libs)
│   ├── include/
│   │   ├── pin_config.h      # Hardware pin definitions matching rysunek1.webp
│   │   ├── cc1101_diag.h     # Hardware & wiring self-test suite
│   │   ├── subghz_engine.h   # Stateless capture, replay & Idle High-Z engine
│   │   ├── auth_manager.h    # Salted SHA-256 + Nonce HMAC & brute-force limiter
│   │   └── api_server.h      # ESP8266 HTTP JSON REST API
│   └── src/
│       ├── main.cpp
│       ├── cc1101_diag.cpp
│       ├── subghz_engine.cpp
│       ├── auth_manager.cpp
│       └── api_server.cpp
└── gui/                      # Dockerized Homelab Web GUI (FastAPI)
    ├── Dockerfile
    ├── docker-compose.yml
    ├── requirements.txt
    ├── data/
    │   └── signals.json      # Persistent Docker volume storing all signals
    ├── app/
    │   ├── main.py           # FastAPI proxy, signal CRUD & Flipper endpoints
    │   ├── signal_store.py   # Persistent signal library manager
    │   ├── flipper_sub.py    # Flipper Zero .sub parser, synthesizer & exporter
    │   └── static/
    │       └── index.html    # Mobile-first UI (PIN pad, 0.8s hold, .sub converter)
    └── tests/
        └── test_flipper_sub.py
```

---

## 7. Build, Flash & Verification Commands

### Build Firmware
```powershell
$env:PLATFORMIO_CORE_DIR = "C:\Users\hugot\.gemini\antigravity\scratch\.platformio"
& "C:\Users\hugot\.gemini\antigravity\scratch\pio-venv\Scripts\pio.exe" run -d "firmware"
```

### Flash Firmware to `COM4`
```powershell
& "C:\Users\hugot\.gemini\antigravity\scratch\pio-venv\Scripts\python.exe" `
  "C:\Users\hugot\.gemini\antigravity\scratch\.platformio\packages\tool-esptoolpy\esptool.py" `
  --chip esp8266 --port COM4 --baud 115200 `
  write_flash 0x0 "firmware\.pio\build\nodemcuv2\firmware.bin"
```
