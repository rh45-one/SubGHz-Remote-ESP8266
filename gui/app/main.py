import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .device import DeviceError, DeviceRelay, SimulatedDevice, GET_ENDPOINTS, POST_ENDPOINTS
from .flipper_sub import export_sub, parse_sub
from .models import Envelope, ImportRequest, Signal, Waveform
from .signal_store import SignalStore

STATIC = Path(__file__).parent / "static"
TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _host_allowed(raw_host: str, patterns: list[str], allow_private_networks: bool) -> bool:
    if not raw_host:
        return False
    try:
        parsed = urlsplit(f"//{raw_host}")
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if not hostname:
        return False
    for pattern in patterns:
        candidate = pattern.strip().lower().strip("[]")
        if not candidate:
            continue
        if hostname == candidate or (candidate.startswith("*.") and hostname.endswith(candidate[1:])):
            return True
    if allow_private_networks:
        if hostname.endswith(".ts.net") or hostname.endswith(".local"):
            return True
        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_loopback or addr.is_private or (addr.version == 4 and addr in TAILSCALE_CGNAT):
                return True
        except ValueError:
            pass
    return False


def create_app(store_path: Path | None = None, device=None, allowed_hosts=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    mode = os.getenv("DEVICE_MODE", "real")
    if mode not in {"real", "simulated"}:
        raise ValueError("DEVICE_MODE must be real or simulated")
    app.state.device = device if device is not None else (
        SimulatedDevice() if mode == "simulated" else DeviceRelay(os.getenv("ESP_BASE_URL", "")))
    app.state.store = SignalStore(store_path or Path(os.getenv("SIGNALS_PATH", "/app/data/signals.json")))
    hosts = [h.strip() for h in (allowed_hosts or os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],*.ts.net,*.local").split(",")) if h.strip()]
    if "*" in hosts:
        raise ValueError("Explicit ALLOWED_HOSTS required; wildcard origin exposure is unsafe")
    allow_private = allowed_hosts is None

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not _host_allowed(request.headers.get("host", ""), hosts, allow_private):
            return PlainTextResponse("Invalid host header", status_code=400)
        if request.method in {"POST", "PUT", "DELETE"}:
            if request.headers.get("x-requested-with") != "subghz-gui":
                return JSONResponse({"error": "Same-origin application header required"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.url.netloc:
                return JSONResponse({"error": "Cross-origin request rejected"}, status_code=403)
            if request.method != "DELETE":
                length = request.headers.get("content-length", "")
                if not length.isdecimal():
                    return JSONResponse({"error": "Content-Length required"}, status_code=411)
                if int(length) > 300000:
                    return JSONResponse({"error": "Request too large"}, status_code=413)
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"error": "JSON required"}, status_code=415)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                                 "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        return response

    @app.exception_handler(DeviceError)
    async def device_error(_request, exc):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(KeyError)
    async def missing(_request, _exc):
        return JSONResponse({"error": "Signal not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_request, _exc):
        return JSONResponse({"error": "Invalid data or damaged signal store; original file retained"}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    def config():
        return {"simulated": isinstance(app.state.device, SimulatedDevice)}

    @app.get("/api/signals")
    def signals():
        return app.state.store.list()

    @app.post("/api/signals", status_code=201)
    def add_signal(signal: Signal):
        return app.state.store.save(signal)

    @app.put("/api/signals/{signal_id}")
    def update_signal(signal_id: str, signal: Signal):
        return app.state.store.save(signal, signal_id)

    @app.delete("/api/signals/{signal_id}")
    def delete_signal(signal_id: str):
        app.state.store.delete(signal_id)
        return {"ok": True}

    @app.get("/api/signals/{signal_id}/export")
    def export(signal_id: str):
        record = app.state.store.get(signal_id)
        signal = Signal.model_validate({k: v for k, v in record.items() if k != "id"})
        return PlainTextResponse(export_sub(signal), headers={"Content-Disposition": 'attachment; filename="signal.sub"'})

    @app.post("/api/flipper/convert")
    def convert(data: ImportRequest):
        try:
            return parse_sub(data.text, data.name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/flipper/export")
    def export_preview(signal: Waveform):
        return {"text": export_sub(signal)}

    @app.get("/api/device/{endpoint:path}")
    async def device_get(endpoint: str):
        if endpoint not in GET_ENDPOINTS:
            raise HTTPException(404)
        return await app.state.device.request(endpoint)

    @app.post("/api/device/{endpoint:path}")
    async def device_post(endpoint: str, envelope: Envelope):
        if endpoint not in POST_ENDPOINTS:
            raise HTTPException(404)
        # Reject malformed/oversized envelopes before forwarding; never log/store these bodies.
        encoded = json.dumps(envelope.model_dump(), separators=(",", ":"), ensure_ascii=False).encode()
        if len(encoded) > 8192:
            raise HTTPException(413, "Device envelope exceeds 8192 bytes")
        return await app.state.device.request(endpoint, envelope)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
