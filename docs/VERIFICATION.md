# Verification and remaining acceptance work

## Verified without physical hardware

- Both `nodemcuv2` (no Wi-Fi credentials) and `nodemcuv2-network-check` build/link successfully. The latter explicitly retains the network/API code, uses fake Wi-Fi strings, and must never be flashed.
- Host C++ tests compile actual engine code against fake clocks/GPIO/radio interfaces: 24/32-bit codec round-trips, jitter, validation, busy rejection, completion/silence/timeout/overflow, micros rollover, final LOW and High-Z cleanup. Pure authentication-policy and HTTP-framing code is also exercised (expiry, replay consumption, 5-failure lockout, rollover, malformed/ambiguous HTTP).
- Python tests exercise all supported `.sub` encoders, RAW/BinRAW validation, repeated exports, storage atomicity/threading/corruption preservation, CRUD/security guards, mock relay bounds/timeouts/no retries, and signed operations against the in-memory device simulator.
- Node tests independently check SHA256/HMAC byte compatibility, 60-second expiry, reference clearing, stale-unlock rejection, and the exact 800 ms hold threshold/cancellation.
- Browser smoke checks against the simulator: default-PIN restriction, PIN change and clearing, unlock, capture/preview, tap refusal, 800 ms hold completing a **simulated** send, post-send relock, blur relock, no local/session storage secrets, and no JavaScript console errors.
- Runtime Python dependency audit: no known vulnerabilities in the selected versions at verification time.
- Docker Compose configuration validates. Local Docker build/runtime could not be exercised because the daemon was not running; CI builds the image.

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
```

GitHub Actions performs these non-hardware checks plus a Docker image build. It never uploads firmware or connects to an RF device.

## Physical acceptance (requires explicit future authorization)

1. Check wiring and supply, then deliberately flash the normal configured environment. Respect COM4's 115200 baud and 2-second reopen delay. Never flash the network-check fixture.
2. Verify both idle GDO registers are High-Z, D4 stays HIGH, clean watchdog/manual resets work, and USB flashing works without detaching CC1101 after firmware startup. Cold-power GDO2/UART conflict remains a hardware limitation.
3. Provision LittleFS deliberately; verify no plaintext PIN is stored, default-PIN RF refusal, real-client HMAC interoperability, nonce expiry/replay rejection, 5-failure lockout/expiry, malformed envelopes, and PIN persistence across power cycles.
4. Exercise maximum HTTP bodies and captures under Wi-Fi load; monitor free heap/stack and watchdog behavior. The receive deadline must not leave the radio active after a capture window expires.
5. Verify known authorized fixed-code remote capture and compare synthesized/recorded pulses on a logic analyzer. Characterize noisy/partial frames and shorter inter-frame gaps; inspect conversion warnings.
6. Before any real actuation, verify TX with an appropriate non-actuating test setup: exact timing, frequency, repeats, final LOW, and idle High-Z. Then explicitly authorize any operational replay. Never test on third-party/rolling-code devices.
7. Test LittleFS PIN-change interruptions/power failures and recovery; never silently restore default credentials on damaged data.
8. On the homelab, test Docker volume ownership, persistence after recreation, backup/restore, Tailscale HTTPS/ACLs/Host forwarding, and real mobile blur/hide/cancel behavior. The UI must remain inaccessible off the intended tailnet.
