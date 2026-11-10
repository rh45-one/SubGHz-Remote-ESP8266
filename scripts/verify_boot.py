"""Bounded serial boot check; no serial commands, filesystem writes, or RF operations.

Use the PlatformIO Python environment (pyserial is required). --reset performs
the usual RTS hard reset; keep the CC1101 disconnected for initial flashing.
The CP210x driver requires a two-second close/reopen cooldown on this host.
"""
import argparse
from pathlib import Path
import sys
import time

import serial
from serial_privacy import redact_network_secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60:
        parser.error("--seconds must be between 1 and 60")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    received = bytearray()
    time.sleep(2.0)
    try:
        # Set the target baud in the constructor, never via a 9600-baud intermediate open.
        with serial.Serial(args.port, 115200, timeout=0.2) as port:
            if args.reset:
                port.dtr = False
                port.rts = True
                time.sleep(0.1)
                port.reset_input_buffer()
                port.rts = False
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline and len(received) < 32768:
                received.extend(port.read(min(1024, 32768 - len(received))))
    finally:
        time.sleep(2.0)
    text = received.decode("utf-8", errors="replace")
    safe_text = redact_network_secrets(text)
    print(safe_text)
    if args.log:
        args.log.write_text(safe_text, encoding="utf-8")
    if "[BOOT] SubGHz-Remote v0.3.1; PIN policy: 4-12 digits." not in text:
        print("[VERIFY] Expected firmware boot marker not observed.")
        return 1
    if "[AUTH]" not in text:
        print("[VERIFY] Authentication initialization status not observed.")
        return 1
    print("[VERIFY] Firmware v0.3.1 boot and authentication status observed; no RF requested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
