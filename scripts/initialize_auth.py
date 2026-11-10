"""Explicitly format unavailable auth storage; refuses a reported-ready credential store.

Requires --confirm-format. This erases the ESP8266 LittleFS filesystem, not the
Docker signal library. Use only after authorizing first setup/credential recovery.
Keep CC1101 disconnected during initial USB provisioning. No RF command is sent.
"""
import argparse
import sys
import time

import serial
from serial_privacy import redact_network_secrets


def receive_until(port, marker, seconds):
    data = bytearray()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and len(data) < 32768:
        data.extend(port.read(min(1024, 32768 - len(data))))
        if marker in data:
            break
    text = data.decode("utf-8", errors="replace")
    print(redact_network_secrets(text))
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--confirm-format", action="store_true")
    args = parser.parse_args()
    if not args.confirm_format:
        parser.error("Filesystem formatting requires --confirm-format")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    time.sleep(2.0)
    try:
        with serial.Serial(args.port, 115200, timeout=0.2) as port:
            port.dtr = False
            port.rts = True
            time.sleep(0.1)
            port.reset_input_buffer()
            port.rts = False
            boot = receive_until(port, b"No serial TX command.", 10)
            if "[BOOT] SubGHz-Remote v0.3.1; PIN policy: 4-12 digits." not in boot:
                raise RuntimeError("Unexpected firmware; no formatting command sent")
            if "[AUTH] Unavailable." not in boot:
                raise RuntimeError("Authentication is not reported unavailable; refusing to format")
            print("[SETUP] Explicit formatting approved; sending the local F setup command once.")
            port.write(b"F")
            port.flush()
            result = receive_until(port, b"[AUTH] Initialized;", 20)
            if "[AUTH] Initialized;" not in result:
                raise RuntimeError("Authentication provisioning did not report success; not retried")
            print("[SETUP] Authentication provisioned with factory PIN 0000; mandatory PIN change remains enabled.")
    finally:
        time.sleep(2.0)


if __name__ == "__main__":
    main()
