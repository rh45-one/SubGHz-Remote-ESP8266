import hashlib
import hmac
import json
import asyncio
import httpx

import pytest
from fastapi.testclient import TestClient

from app.device import SimulatedDevice, DeviceRelay
from app.main import create_app
from app.models import Envelope

HEADERS = {"X-Requested-With": "subghz-gui"}


@pytest.fixture
def fixture(tmp_path):
    clock = [100.0]
    device = SimulatedDevice(lambda: clock[0])
    app = create_app(tmp_path / "signals.json", device, ["testserver"])
    with TestClient(app) as client:
        yield client, device, clock


def signed(client, endpoint, args=None, pin="123456", challenge=None):
    challenge = challenge or client.get("/api/device/auth/challenge").json()
    key = hashlib.sha256(bytes.fromhex(challenge["salt"]) + pin.encode()).digest()
    payload = json.dumps(args or {}, separators=(",", ":"))
    message = f'{challenge["nonce"]}:/api/{endpoint}:{payload}'.encode()
    return {"nonce": challenge["nonce"], "signature": hmac.new(key, message, hashlib.sha256).hexdigest(), "payload": payload}


def post(client, endpoint, envelope):
    return client.post(f"/api/device/{endpoint}", json=envelope, headers=HEADERS)


def change_pin(client):
    salt = "ab" * 16
    key = hashlib.sha256(bytes.fromhex(salt) + b"987654").hexdigest()
    assert post(client, "auth/change-pin", signed(client, "auth/change-pin", {"salt": salt, "new_pin_key": key})).status_code == 200


def test_auth_single_use_bound_body_and_action(fixture):
    client, device, _ = fixture
    envelope = signed(client, "auth/verify")
    assert post(client, "auth/verify", envelope).status_code == 200
    assert post(client, "auth/verify", envelope).status_code == 401
    envelope = signed(client, "auth/verify")
    envelope["payload"] = '{"tampered":true}'
    assert post(client, "auth/verify", envelope).status_code == 401
    assert post(client, "transmit", signed(client, "auth/verify")).status_code == 401
    assert device.transmissions == 0


def test_expiry_and_lockout(fixture):
    client, _, clock = fixture
    envelope = signed(client, "auth/verify")
    clock[0] += 10
    assert post(client, "auth/verify", envelope).status_code == 401
    for attempt in range(4):
        response = post(client, "auth/verify", signed(client, "auth/verify", pin="000000"))
        assert response.status_code == (429 if attempt == 3 else 401)
    assert client.get("/api/device/auth/challenge").status_code == 429
    assert client.get("/api/device/status").json()["locked_out"]
    clock[0] += 60
    assert post(client, "auth/verify", signed(client, "auth/verify")).status_code == 200


def test_default_pin_change_capture_and_simulated_send(fixture):
    client, device, _ = fixture
    wave = {"frequency_hz": 433920000, "pulses": [350, -1050, 350, -10850], "repeats": 2}
    assert post(client, "transmit", signed(client, "transmit", wave)).status_code == 403
    change_pin(client)
    assert post(client, "auth/verify", signed(client, "auth/verify")).status_code == 401
    assert post(client, "record/start", signed(client, "record/start", {"frequency_hz": 433920000, "window_ms": 10000}, "987654")).status_code == 200
    capture = client.get("/api/device/record/status").json()
    assert capture["state"] == 2 and len(capture["pulses"]) == 50
    assert post(client, "transmit", signed(client, "transmit", wave, "987654")).status_code == 200
    assert device.transmissions == 1
    wave["pulses"][0] = -350
    assert post(client, "transmit", signed(client, "transmit", wave, "987654")).status_code == 400
    assert device.transmissions == 1


def test_crud_converter_security(fixture):
    client, _, _ = fixture
    signal = {"name": "<script>alert(1)</script>", "pulses": [350, -10850]}
    assert client.post("/api/signals", json=signal).status_code == 403
    assert client.post("/api/signals", json=signal, headers={**HEADERS, "Origin": "https://evil.invalid"}).status_code == 403
    saved = client.post("/api/signals", json=signal, headers=HEADERS)
    assert saved.status_code == 201
    record = saved.json()
    exported = client.get(f'/api/signals/{record["id"]}/export')
    assert exported.status_code == 200 and "Protocol: RAW" in exported.text
    converted = client.post("/api/flipper/convert", json={"text": exported.text}, headers=HEADERS)
    assert converted.status_code == 200
    assert client.post("/api/flipper/export", json={"pulses": [350, -10850]}, headers=HEADERS).json()["text"].startswith("Filetype:")
    assert client.get("/").headers["content-security-policy"].startswith("default-src 'self'")
    assert client.get("/api/device/http://evil.invalid").status_code == 404
    assert client.get("/api/signals", headers={"Host": "evil.invalid"}).status_code == 400
    assert client.delete(f'/api/signals/{record["id"]}', headers=HEADERS).status_code == 200
    assert client.get("/api/signals").json() == []


def test_large_body_and_envelope(fixture):
    client, _, _ = fixture
    assert client.post("/api/signals", content="x" * 300001, headers={**HEADERS, "Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/device/transmit", json={"pin": "123456"}, headers=HEADERS).status_code == 422


@pytest.mark.parametrize("url", ["http://u:p@127.0.0.1", "file:///x", "http://localhost/path", "http://localhost?x=y"])
def test_relay_origin_restrictions(url):
    with pytest.raises(ValueError):
        DeviceRelay(url)


def test_mocked_relay_preserves_signed_payload_and_never_retries():
    calls = []
    envelope = Envelope(nonce="aa" * 16, signature="bb" * 32, payload='{"pulses":[350,-350],"label":"µs"}')

    def respond(request):
        calls.append(request)
        assert request.url == "http://device.invalid/api/transmit"
        assert json.loads(request.content) == envelope.model_dump()
        return httpx.Response(200, json={"ok": True})

    relay = DeviceRelay("http://device.invalid", transport=httpx.MockTransport(respond))
    assert asyncio.run(relay.request("transmit", envelope)) == {"ok": True}
    assert len(calls) == 1

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("Timeout")

    from app.device import DeviceError
    relay = DeviceRelay("http://device.invalid", transport=httpx.MockTransport(timeout))
    with pytest.raises(DeviceError, match="not retried"):
        asyncio.run(relay.request("transmit", envelope))
    assert len(calls) == 2


@pytest.mark.parametrize("status,content", [(200, b"x" * 16385), (200, b"[]"), (200, b"not json"), (302, b'{}')])
def test_mocked_relay_rejects_bad_responses(status, content):
    from app.device import DeviceError
    relay = DeviceRelay("http://device.invalid", transport=httpx.MockTransport(lambda request: httpx.Response(status, content=content)))
    with pytest.raises(DeviceError):
        asyncio.run(relay.request("status"))
