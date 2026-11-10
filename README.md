# SubGHz-Remote-ESP8266

Secure ESP8266 (NodeMCU) + CC1101 Sub-GHz transceiver firmware and homelab Docker GUI for recording and replaying garage door RF signals over Tailscale.

**Status:** firmware, authenticated API, Docker GUI, and offline simulator implemented. Software tests pass; physical acceptance and local Docker runtime checks remain pending. No rolling-code support. Read [the plan](PLAN.md), [API contract](docs/API.md), and [verification checklist](docs/VERIFICATION.md) before hardware use.

## Try it without hardware

With Python 3.12+ and Node.js 22+ (Node is only needed for tests):
```powershell
cd gui
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:DEVICE_MODE = "simulated"
$env:SIGNALS_PATH = "$PWD\data\signals.json"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```
Open `http://127.0.0.1:8000`. The orange **SIMULATED** badge must be present. Tap **Unlock remote**, enter `0000`, and choose a new PIN (4–12 digits) in **Settings → Security**. Import or capture a simulated signal in Settings, then assign it to either remote button under **Settings → Signals**. Sending only increments an in-memory counter: this mode never opens a connection to an ESP8266. Simulated credentials reset on application restart; the signal library persists.

Docker alternative, from `gui/`:
```powershell
$env:DEVICE_MODE = "simulated"
docker compose up -d
```
By default, `gui/docker-compose.yml` pulls the published multi-architecture image (`ghcr.io/rh45-one/subghz-remote-esp8266-gui:latest`) from GitHub Container Registry. To build locally from source during development instead, include `docker-compose.dev.yml`:
```powershell
$env:DEVICE_MODE = "simulated"
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```
The GUI binds only to loopback (`127.0.0.1:8000:8000`). Do not publish port 8000 to the LAN or Internet.

### Container releases, updates, rollback & Watchtower

Pushes to `main` that pass `Offline checks` and modify GUI or deployment files automatically publish a multi-architecture (`linux/amd64`, `linux/arm64`) OCI image to **`ghcr.io/rh45-one/subghz-remote-esp8266-gui`**, create a matching **`ui-vX.Y.Z` GitHub Release** (with `docker-compose.yml`, `docker-compose.dev.yml`, and `.env.example` attached), and then promote that exact manifest digest to `:latest`.

- **Pull / manual update (preserving the `signals` volume):**
  ```sh
  docker compose -f gui/docker-compose.yml pull
  docker compose -f gui/docker-compose.yml up -d
  ```
- **Pin or roll back to a specific release (`ui-vX.Y.Z` or `sha-<12-hex>`):**
  Set `GUI_IMAGE_TAG=ui-v0.1.0` in `gui/.env` (or pass it in the environment) and recreate the container:
  ```sh
  GUI_IMAGE_TAG=ui-v0.1.0 docker compose -f gui/docker-compose.yml pull
  GUI_IMAGE_TAG=ui-v0.1.0 docker compose -f gui/docker-compose.yml up -d
  ```
  Recreating the container leaves `/app/data/signals.json` in the named `signals` volume untouched.
- **Automatic updates (Watchtower):**
  Watchtower polls the container registry tag (`ghcr.io/rh45-one/subghz-remote-esp8266-gui:latest`), not GitHub Releases. Because the GHCR package is public, no registry credentials are required. `gui/docker-compose.yml` includes opt-in labels (`com.centurylinklabs.watchtower.enable=true` and `com.centurylinklabs.watchtower.scope=subghz-remote`) so an existing homelab updater running with `--label-enable` (and optional `--scope subghz-remote`) will update the container when `GUI_IMAGE_TAG=latest`. Set `WATCHTOWER_ENABLE=false` or pin `GUI_IMAGE_TAG` to a `ui-vX.Y.Z` tag to pause automatic updates.
  > **Note on Watchtower compatibility:** This repository deliberately does not start a Watchtower container automatically (mounting `/var/run/docker.sock` grants host-equivalent privileges). In addition, the original `containrrr/watchtower` upstream project is archived and its older bundled Docker client can fail against newer Docker Engine API minimums unless configured or replaced with an actively maintained fork/image updater.

### Remote & appearance

The home screen is a compact **two-button remote**, on desktop and mobile. Tap Unlock (or a locked assigned button) to open PIN entry; unlocking never queues a transmission. Hold an assigned button for 0.8 seconds to send. The status display shows the 60-second armed countdown, and sending locks it again.

All signal management, conversion, recording, security, and appearance controls live behind the **Settings gear**. Choose **Night shift** (black/orange) or **Day shift** (white/orange) in **Settings → Appearance**; desktop also has a quick theme toggle. The UI uses a self-hosted Silkscreen pixel font (SIL OFL license included) and original line icons, with a plain background and no external font requests.

Only theme choice and the two assigned signal IDs are kept in browser storage. Initial slots use the first two available signals; deleting an assigned signal leaves its button unassigned rather than silently substituting another action. All saved signals remain in the server-side library. Browser-storage restrictions degrade to visit-only preferences, never insecure credential persistence.

## Real-device configuration (do not flash until ready)

1. Copy `firmware/include/secrets.example.h` to `firmware/include/secrets.h`, and enter **Wi-Fi credentials only**. This local file is gitignored. With no SSID configured, the HTTP listener remains disabled. No open AP fallback exists.
2. Build normally with `pio run -d firmware`. For build-only validation of network code without any secrets, use `pio run -d firmware -e nodemcuv2-network-check`; **never upload that fixture**.

   Optional fixed addressing: copy `firmware/include/network_config.example.h` to the gitignored `network_config.h` and enter your LAN IP, gateway, subnet, and DNS. This applies the static address before connecting and on every boot; it does not fall back to DHCP if static setup fails. Wi-Fi automatically reconnects after a drop. Without that local file, normal firmware uses DHCP. Reserve/exclude your chosen IP in the router's DHCP configuration to avoid collisions; the example uses non-routable TEST-NET values and must be replaced.
3. First boot with an unformatted filesystem fails closed. When physically ready, the local serial command **`F` explicitly formats LittleFS** only if authentication initialization failed. This is destructive credential recovery, not a normal startup step. A valid store is never autoformatted. Initial PIN `0000` is restricted to verification/PIN change until changed. PINs accept 4–12 digits; use a longer random PIN for better resistance to offline guessing. A firmware update preserves existing credentials rather than resetting them to the new factory default.
4. Set `DEVICE_MODE=real`, `ESP_BASE_URL=http://<device-LAN-IP>` and exact `ALLOWED_HOSTS` in `gui/.env` (copy `.env.example`). No PIN belongs in environment variables. Leave the URL empty to remain disconnected.
5. Run Docker Compose and serve its loopback port using **Tailscale Serve HTTPS**, preserving the public Host header. Add that exact tailnet hostname to `ALLOWED_HOSTS`, limit access using Tailscale ACLs, and keep the ESP8266 on an isolated/trusted LAN. Web Crypto requires HTTPS except on localhost.

For bounded USB boot verification, use the PlatformIO Python environment to run `scripts/verify_boot.py --port COM4 --reset`. For explicitly approved first setup/recovery only, `scripts/initialize_auth.py --port COM4 --confirm-format` formats unavailable LittleFS auth storage and provisions `0000`; it refuses a reported-ready credential store. Both helpers open directly at 115200 baud and respect the CP210x two-second reopen cooldown. Never format storage just to change an existing PIN; use the authenticated Settings flow instead.

The browser derives a non-extractable signing key and immediately discards the entered PIN; key references are removed after send, 60 seconds, blur, hide, or page exit. JavaScript cannot guarantee physical zeroization of immutable strings or garbage-collected memory. The backend never receives plaintext PINs. HMAC prevents request tampering/replay, **not eavesdropping, offline guessing of a short PIN, or LAN denial of service**. PIN-change key material crosses LAN HTTP and is password-equivalent. This is not an Internet-facing security design.

## Wiring (CC1101 V2.0 8-pin <-> NodeMCU ESP8266)

| CC1101 Pin | Signal | Wire Color | NodeMCU Pin | ESP8266 GPIO |
| :---: | :--- | :--- | :---: | :---: |
| **1** | `GND` | Black | `GND` | — |
| **2** | `VCC` | Red | `3V3` | — |
| **3** | `GDO0` | Blue | `D4` | `GPIO2` |
| **4** | `CSN` | Pink | `D8` | `GPIO15` |
| **5** | `SCK` | Green | `D5` | `GPIO14` |
| **6** | `MOSI` | Orange | `D7` | `GPIO13` |
| **7** | `MISO` | Yellow | `D6` | `GPIO12` |
| **8** | `GDO2` | Grey | `RX` | `GPIO3` |

> **Note on Pin 8 (`GDO2` -> `RX`):** `RX` (`GPIO3`) is shared with the NodeMCU's USB-to-UART chip. If your initial USB flash fails to connect to the ESP8266 bootloader, unplug the **Grey wire (`RX`)** during flashing and reconnect it afterward. Once Phase 1 firmware boots, it places `GDO2` into High-Impedance (`0x2E`) mode so subsequent uploads work seamlessly.

## Phase 1: Flashing the Wiring & Hardware Self-Test

1. Install [PlatformIO](https://platformio.org/install/cli) (or the PlatformIO IDE extension in VS Code / Antigravity).
2. Build, upload, and open the Serial Monitor (`115200` baud):
   ```powershell
   cd firmware
   pio run -t upload -t monitor
   ```
3. Check the `[1]`–`[5]` self-test lines in the Serial output. The current Phase 2 firmware then stays idle with both GDO pins High-Z; use the commands below to capture explicitly.

## Phase 2: Bounded Capture & Replay Engine

Open the serial monitor at **115200 baud**:

| Command | Action |
| --- | --- |
| `r` | Capture at the selected frequency for up to 10 seconds |
| `s` | Stop capture and return to idle High-Z |
| `t` | Re-run wiring diagnostics and initialize the engine |
| `1`, `2`, `3` | Select 433.92, 315.00, or 868.35 MHz while idle |

Capture prints signed `RAW_Data` microsecond timings and, when recognized, a 24/32-bit rc-switch protocol 1 fixed code. Capture states: `0` idle/cancelled, `1` recording, `2` complete, `3` timeout, `4` overflow. Overflow and timed-out data must not be treated as complete signals. The RAM buffer is replaced on the next capture; no signal library is written to flash.

The engine has raw/fixed replay primitives behind the **authenticated Phase 3 API**. There is no serial TX command. Replay limits: 512 alternating nonzero signed pulses, positive first mark, 1–20 repeats, two seconds total waveform duration, and the CC1101 frequency bands listed in `PLAN.md`. The hardware antenna is 433 MHz; frequency validation is not confirmation of antenna suitability or legal permission to transmit. Use only your own or explicitly authorized devices and comply with local RF regulations.

Segmentation uses a low interval of at least 5 ms observed at the next edge, or 12 ms of trailing silence. Longer idle intervals are capped at 32767 µs; oversized marks fail capture. Noise or starting midway through a frame may produce a partial burst; inspect it and recapture. Short-repeat-gap protocols may not segment correctly yet. Fixed-code decoding is limited to protocol 1; rolling codes are not supported.

### Software tests (no board, no RF)

Host tests compile the **actual engine source** against fake GPIO/radio/clock interfaces and cover codec round-trips, timing tolerance, invalid inputs, capture completion/timeout/overflow, timer rollover, replay refusal while recording, and idle High-Z cleanup.

With GCC/Clang:
```sh
g++ -std=c++14 -Ifirmware/tests/stubs -Ifirmware/include firmware/tests/test_engine.cpp firmware/src/subghz_engine.cpp -o engine-tests
./engine-tests
```

Or from a Visual Studio Developer PowerShell:
```powershell
cl /nologo /EHsc /std:c++14 /W4 /Ifirmware\tests\stubs /Ifirmware\include firmware\tests\test_engine.cpp firmware\src\subghz_engine.cpp /Feengine-tests.exe
.\engine-tests.exe
```

If existing `.pio` files are inaccessible, keep them untouched and build with separate directories:
```powershell
$env:PLATFORMIO_BUILD_DIR = "$env:TEMP\opencode\subghz-phase2-build"
$env:PLATFORMIO_LIBDEPS_DIR = "$env:TEMP\opencode\subghz-phase2-libdeps"
pio run -d firmware
```
The resulting binary is under `$env:PLATFORMIO_BUILD_DIR\nodemcuv2\firmware.bin`, not the default `.pio` path.

### Pending physical acceptance checks

- Explicitly flash only when ready; allow **2 seconds** between closing and reopening COM4, and open directly at **115200 baud**.
- Verify idle `IOCFG0=IOCFG2=0x2E`, clean resets, and USB reflashing with CC1101 attached.
- Capture an authorized fixed-code remote; check pulse signs, decoded value, timeout, cancellation, and overflow cleanup.
- Before exposing authenticated TX, inspect raw/fixed replay with a logic analyzer: first mark, timing/repeats, final LOW, and idle High-Z. Software simulations do not establish RF fidelity or physical reset safety.

## GUI tests & storage

From `gui/`, run `python -m pytest -q`; from the project root run `node --test gui/tests/safety.test.mjs`. Tests use simulated devices or HTTP mock transports only. Keep PlatformIO in a **separate Python environment**: its tooling dependency pins conflict with the GUI's current Starlette version. Firmware host tests are described above; CI also checks a network-enabled firmware build, dependency vulnerabilities, and the Docker image build.

Optional browser coverage: install `gui/requirements-browser.txt`, run `python -m playwright install chromium`, then run `python -m pytest browser_tests -q` from `gui/`. These tests start isolated local simulators and cover both themes at desktop, phone, and 320×568 sizes, touch tap/cancel/hold behavior, PIN entry, relocking, assignments, and navigation. Without Playwright, browser tests are skipped. CI installs Chromium and runs them too.

Signals are stored atomically in `/app/data/signals.json` in the Docker named volume. Back up that volume; do not use `docker compose down -v` unless intentionally deleting the library. Run **one worker/container per volume**: the store serializes threads but not multiple processes. A damaged JSON file is retained rather than silently replaced. The UI supports preview, save, metadata edits, delete, and RAW `.sub` export.

Import supports standard OOK RAW, BinRAW, and fixed Key protocols Princeton, CAME, Nice FLO, Linear, GateTX, and Holtek. FSK/custom presets and unknown/rolling protocols are rejected. Heuristic RAW burst selection displays warnings and never truncates oversized marks or a non-segmentable burst. Synthesized low-first frames are cyclically rotated to meet the positive-first RF engine contract. Long captured idle gaps may be capped with a warning; unsupported synthesized timings are rejected. Inspect every conversion before use. Protocol timing references: [Flipper firmware encoders](https://github.com/flipperdevices/flipperzero-firmware/tree/dev/lib/subghz/protocols).
