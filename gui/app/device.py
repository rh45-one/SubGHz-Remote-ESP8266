"""Bounded device relay and explicitly selected, entirely in-memory simulator."""
import asyncio
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from .models import Envelope, Waveform, valid_frequency

GET_ENDPOINTS = {"status", "auth/challenge", "record/status"}
POST_ENDPOINTS = {"auth/verify", "auth/change-pin", "record/start", "transmit"}


class DeviceError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


class DeviceRelay:
    def __init__(self, base_url: str, *, transport=None):
        self.base_url = base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        if self.base_url and (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                              parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("ESP_BASE_URL must be a fixed http(s) origin without credentials/path")
        self.lock = asyncio.Lock()
        self.transport = transport

    async def request(self, endpoint: str, envelope: Envelope | None = None):
        if endpoint not in (POST_ENDPOINTS if envelope else GET_ENDPOINTS):
            raise DeviceError(404, "Unknown device endpoint")
        if not self.base_url:
            raise DeviceError(503, "No device configured; use DEVICE_MODE=simulated for offline use")
        try:
            async with asyncio.timeout(6), self.lock, httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False, transport=self.transport) as client:
                url = f"{self.base_url}/api/{endpoint}"
                # Stream with a hard bound even if the device sends a dishonest Content-Length.
                async with client.stream("POST" if envelope else "GET", url,
                                         **({"json": envelope.model_dump()} if envelope else {})) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 16384:
                            raise DeviceError(502, "Device response too large")
                    status = response.status_code
                    data = json.loads(content)
                if not isinstance(data, dict):
                    raise DeviceError(502, "Invalid device response")
                if status != 200:
                    raise DeviceError(status if 400 <= status < 600 else 502,
                                      data.get("error", "Device rejected request"))
                return data
        except (httpx.HTTPError, ValueError, TimeoutError):
            raise DeviceError(502, "Device unavailable or invalid response; not retried") from None


class SimulatedDevice:
    """Not a firmware emulator: protocol/UX fixture only. Never opens a socket."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.salt = secrets.token_hex(16)
        self.key = hashlib.sha256(bytes.fromhex(self.salt) + b"0000").digest()
        self.must_change = True
        self.nonce = None
        self.expires = 0.0
        self.failures = 0
        self.locked_until = 0.0
        self.transmissions = 0
        self.capture = {"state": 0}

    async def request(self, endpoint: str, envelope: Envelope | None = None):
        now = self.clock()
        locked = now < self.locked_until
        if endpoint == "status":
            return {"version": "simulated-0.3.1", "simulated": True, "cc1101_ok": True,
                    "pin_min_length": 4, "pin_max_length": 12,
                    "auth_ready": True, "must_change_pin": self.must_change, "locked_out": locked,
                    "lockout_remaining_s": max(0, int(self.locked_until-now+0.999)), "uptime_s": int(now)}
        if locked:
            raise DeviceError(429, "Simulated authentication lockout")
        if endpoint == "auth/challenge":
            self.nonce = secrets.token_hex(16)
            self.expires = now + 10
            return {"salt": self.salt, "nonce": self.nonce, "expires_in_ms": 10000}
        if endpoint == "record/status":
            return self.capture.copy()
        nonce, self.nonce = self.nonce, None
        valid = False
        if envelope and nonce and now < self.expires:
            message = f"{nonce}:/api/{endpoint}:{envelope.payload}".encode()
            valid = hmac.compare_digest(nonce, envelope.nonce) and hmac.compare_digest(
                hmac.new(self.key, message, hashlib.sha256).hexdigest(), envelope.signature)
        if not valid:
            if self.failures >= 5 and now >= self.locked_until:
                self.failures = 0
            self.failures += 1
            if self.failures >= 5:
                self.locked_until = now + 60
            raise DeviceError(429 if self.failures >= 5 else 401, "Authentication failed")
        self.failures = 0
        try:
            args = json.loads(envelope.payload)
            if not isinstance(args, dict):
                raise ValueError("Object required")
            if endpoint == "auth/verify":
                return {"must_change_pin": self.must_change}
            if endpoint == "auth/change-pin":
                salt, key = args["salt"], args["new_pin_key"]
                if len(salt) != 32 or len(key) != 64 or salt == self.salt:
                    raise ValueError("Invalid credential material")
                if any(c not in "0123456789abcdef" for c in salt + key):
                    raise ValueError("Invalid hexadecimal credentials")
                self.salt, self.key, self.must_change = salt, bytes.fromhex(key), False
                return {"ok": True}
            if self.must_change:
                raise DeviceError(403, "Change default PIN before RF operations")
            if endpoint == "transmit":
                Waveform.model_validate(args)
                self.transmissions += 1  # Counter only; no RF or network code exists here.
            elif endpoint == "record/start":
                hz, window = args["frequency_hz"], args["window_ms"]
                if type(hz) is not int or not valid_frequency(hz) or type(window) is not int or not 1 <= window <= 10000:
                    raise ValueError("Invalid capture arguments")
                self.capture = {"state": 2, "frequency_hz": hz, "repeats": 1,
                                "pulses": [350, -1050] * 24 + [350, -10850]}
            else:
                raise DeviceError(404, "Unknown simulated endpoint")
        except (KeyError, ValueError, TypeError, ValidationError):
            raise DeviceError(400, "Invalid signed payload") from None
        return {"ok": True, "simulated": True}
