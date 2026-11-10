# Verification and remaining acceptance work

## Verified without physical hardware

- Both `nodemcuv2` (no Wi-Fi credentials) and `nodemcuv2-network-check` build/link successfully. The latter explicitly retains the network/API code, uses fake Wi-Fi strings, and must never be flashed.
- Host C++ tests compile actual engine code against fake clocks/GPIO/radio interfaces: 24/32-bit codec round-trips, jitter, validation, busy rejection, completion/silence/timeout/overflow, micros rollover, final LOW and High-Z cleanup. Pure authentication-policy and HTTP-framing code is also exercised (expiry, replay consumption, 5-failure lockout, rollover, malformed/ambiguous HTTP).
- Python tests exercise all supported `.sub` encoders, RAW/BinRAW validation, repeated exports, storage atomicity/threading/corruption preservation, CRUD/security guards, mock relay bounds/timeouts/no retries, and signed operations against the in-memory device simulator.
- Node tests independently check SHA256/HMAC byte compatibility, 60-second expiry, reference clearing, stale-unlock rejection, and the exact 800 ms hold threshold/cancellation.
- Browser smoke checks against the simulator: default-PIN restriction, PIN change and clearing, unlock, capture/preview, tap refusal, 800 ms hold completing a **simulated** send, post-send relock, blur relock, no local/session storage secrets, and no JavaScript console errors.
- Runtime Python dependency audit: no known vulnerabilities in the selected versions at verification time.
- Docker Compose configuration validates. Local Docker build/runtime could not be exercised because the daemon was not running; CI builds the image and runs `gui/tests/docker_smoke.py` with networking completely disabled to verify non-root/read-only operation and named-volume persistence across container recreation.

These are not claims that the simulated device executes the ESP8266 HTTP/crypto/filesystem implementation. Firmware BearSSL/LittleFS/Wi-Fi integration is compiled, while the policy/parser/engine are host-tested. Memory pressure, power-loss atomicity, RF timing, and physical GPIO states remain unverified on hardware.

## Repeat software checks

```sh
python -m pip install -r gui/requirements-dev.txt
cd gui
python -m pytest -q
```
From the project root:
```sh
node --test gui/tests/safety.test.mjs
g++ -std=c++14 -Wall -Wextra -Werror -fsanitize=address,undefined -Ifirmware/tests/stubs -Ifirmware/include firmware/tests/test_engine.cpp firmware/src/subghz_engine.cpp -o engine-tests
./engine-tests
pio run -d firmware -e nodemcuv2 -e nodemcuv2-network-check
pip-audit -r gui/requirements.txt
docker compose -f gui/docker-compose.yml config --quiet
docker compose -f gui/docker-compose.yml -f gui/docker-compose.dev.yml config --quiet
```

GitHub Actions performs these non-hardware checks plus the Docker image build and network-isolated container smoke test. It never uploads firmware or connects to an RF device. To repeat the container check locally, build `subghz-gui` and run `python gui/tests/docker_smoke.py`; only that script's randomly named test containers and volume are removed.

## Initial physical USB provisioning — 2026-11-10

- User authorized flashing and disconnected CC1101 first. COM4 enumerated as the Silicon Labs CP210x bridge; esptool identified ESP8266EX, MAC `40:f5:20:27:78:c9`.
- Flashed the normal `nodemcuv2` firmware v0.3.1 at 115200 baud, address `0x0`; esptool verified the written data hash. The build-only network fixture was **not** flashed.
- Firmware binary SHA256: `662b9efbd93e533fce99e9d4362d38cf257d45033d6e286a2e4169761c3d4d41` (335984 bytes).
- Serial reset/boot verification observed the v0.3.1 / 4–12 digit PIN-policy marker. CC1101 diagnostics failed as expected with the transceiver disconnected; no capture or transmit command was sent.
- Auth storage initially reported unavailable. After **separate explicit user approval**, sent the local `F` initialization command once. Firmware reported successful provisioning with factory PIN `0000` and mandatory PIN change. A subsequent reset confirmed `[AUTH] Credential store ready.`; this verifies provisioning survives reset, not yet HTTP/HMAC interoperability on hardware.
- No `secrets.h` is configured; the flashed firmware explicitly reports the Wi-Fi API disabled. LAN configuration and real-device GUI integration remain pending. COM4 was closed after verification with the required cooldown.
- Repeat bounded boot checks with `scripts/verify_boot.py`. `scripts/initialize_auth.py --confirm-format` is destructive recovery and requires explicit approval; it refuses to send `F` unless the expected firmware reports authentication unavailable.

## CC1101 reconnection & static IP verification — 2026-11-10

- After the user reconnected the CC1101, a bounded USB reset/boot check passed all five wiring diagnostics: MISO ready, SPI identity (`PARTNUM=0x00`, `VERSION=0x14`), register read/write, GDO0 continuity, and GDO2 continuity/release.
- Firmware reported radio idle/High-Z and auth storage ready. A read-only LAN status request plus authenticated challenge/verify check confirmed real-device HMAC interoperability, `cc1101_ok=true`, `auth_ready=true`, and `must_change_pin=true`.
- Configured gitignored `firmware/include/network_config.h` for static LAN address `192.168.1.195`, reflashed, verified after reboot that `http://192.168.1.195/api/status` is reachable, and confirmed existing LittleFS authentication state survived the update without re-provisioning.
- No record or transmit endpoint was invoked. This does not yet establish capture quality, TX timing, or reset safety during active RF operation. Private Wi-Fi credentials are never included in this repository or documentation.

## Remaining physical acceptance (requires explicit authorization)

1. Check wiring and supply, then deliberately flash the normal configured environment. Respect COM4's 115200 baud and 2-second reopen delay. Never flash the network-check fixture.
2. Verify both idle GDO registers are High-Z, D4 stays HIGH, clean watchdog/manual resets work, and USB flashing works without detaching CC1101 after firmware startup. Cold-power GDO2/UART conflict remains a hardware limitation.
3. Provision LittleFS deliberately; verify no plaintext PIN is stored, default-PIN RF refusal, real-client HMAC interoperability, nonce expiry/replay rejection, 5-failure lockout/expiry, malformed envelopes, and PIN persistence across power cycles.
4. Exercise maximum HTTP bodies and captures under Wi-Fi load; monitor free heap/stack and watchdog behavior. The receive deadline must not leave the radio active after a capture window expires.
5. Verify known authorized fixed-code remote capture and compare synthesized/recorded pulses on a logic analyzer. Characterize noisy/partial frames and shorter inter-frame gaps; inspect conversion warnings.
6. Before any real actuation, verify TX with an appropriate non-actuating test setup: exact timing, frequency, repeats, final LOW, and idle High-Z. Then explicitly authorize any operational replay. Never test on third-party/rolling-code devices.
7. Test LittleFS PIN-change interruptions/power failures and recovery; never silently restore default credentials on damaged data.
8. On the homelab, test Docker volume ownership, persistence after recreation, backup/restore, Tailscale HTTPS/ACLs/Host forwarding, and real mobile blur/hide/cancel behavior. The UI must remain inaccessible off the intended tailnet.
