"""Optional responsive/interaction tests. Every device is an in-memory simulator."""
import os
from pathlib import Path
import re
import socket
import threading

import pytest
import uvicorn

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright

from app.device import SimulatedDevice
from app.main import create_app
from app.models import Signal


@pytest.fixture
def browser():
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def app_server(tmp_path):
    device = SimulatedDevice()
    app = create_app(tmp_path / "signals.json", device=device, allowed_hosts=["127.0.0.1"])
    for name in ("Garage door", "Porch light"):
        app.state.store.save(Signal(name=name, pulses=[350, -1050] * 24 + [350, -10850], repeats=3))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{sock.getsockname()[1]}", device, app.state.store
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


def open_page(browser, base, width=390, height=844):
    context = browser.new_context(viewport={"width": width, "height": height}, is_mobile=width <= 600, has_touch=width <= 600)
    page = context.new_page()
    page.goto(base, wait_until="load")
    expect(page.locator("#mode")).to_have_text("SIMULATED")
    expect(page.locator("#remote-name-1")).to_have_text("Garage door")
    page.evaluate("document.fonts.ready")
    return context, page


def unlock(page, pin):
    if not page.locator("#unlock-dialog").is_visible():
        page.locator("#settings-unlock" if page.locator("#settings-page").is_visible() else "#unlock-remote").click()
    page.locator("#pin").fill(pin)
    page.locator("#unlock-form").get_by_role("button", name="Unlock", exact=True).click()
    expect(page.locator("#unlock-dialog")).not_to_be_visible()


def configure_pin(page):
    unlock(page, "0000")
    expect(page).to_have_url(re.compile(r"#settings/security$"))
    page.locator("#new-pin").fill("9876")
    page.locator("#confirm-pin").fill("9876")
    page.locator("#change-form button").click()
    expect(page.locator("#message")).to_have_text("PIN changed. Unlock again.")
    unlock(page, "9876")
    page.locator("#back-remote").click()
    expect(page.locator("#armed")).to_contain_text("ARMED")


def test_pin_form_minimum_and_factory_pin(browser, app_server):
    context, page = open_page(browser, app_server[0])
    try:
        page.locator("#unlock-remote").click()
        page.locator("#pin").fill("000")
        assert not page.locator("#pin").evaluate("element => element.checkValidity()")
        page.locator("#pin").fill("0000")
        assert page.locator("#pin").evaluate("element => element.checkValidity()")
        unlock(page, "0000")
        expect(page.locator("#security")).to_be_visible()
        page.locator("#new-pin").fill("123")
        assert not page.locator("#new-pin").evaluate("element => element.checkValidity()")
        page.locator("#new-pin").fill("0123")
        assert page.locator("#new-pin").evaluate("element => element.checkValidity()")
    finally:
        context.close()


@pytest.mark.parametrize("name,width,height", [("desktop", 1440, 900), ("wide-desktop", 1920, 1080), ("laptop", 1280, 720), ("short-desktop", 1024, 600), ("phone", 390, 844), ("compact", 320, 568)])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_responsive_remote(browser, app_server, name, width, height, theme):
    context, page = open_page(browser, app_server[0], width, height)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        if theme == "light":
            page.locator("#theme-toggle").click()
        expect(page.locator("#theme-toggle")).to_be_visible()
        expect(page.locator("#status-indicator")).to_be_visible()
        expect(page.locator("#status-indicator")).to_have_attribute("data-status", "simulated")
        expect(page.locator("html")).to_have_attribute("data-theme", theme)
        assert page.locator("#remote-page .hold:visible").count() == 2
        expect(page.locator("#settings-page")).not_to_be_visible()
        expect(page.locator("#sub-text")).not_to_be_visible()
        remote_width = page.locator(".remote-body").bounding_box()["width"]
        label_size = page.locator(".key-copy strong").first.evaluate("element => parseFloat(getComputedStyle(element).fontSize)")
        if width >= 701:
            assert 390 <= remote_width <= 461
            assert label_size >= 16
        else:
            assert remote_width <= 329
            assert label_size == (12 if width <= 350 else 13)
        assert page.evaluate("document.fonts.check('12px Silkscreen')")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate("document.documentElement.scrollHeight <= innerHeight"), "Remote should fit without scrolling"
        folder = os.getenv("SUBGHZ_SCREENSHOTS")
        if folder:
            Path(folder).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(folder) / f"remote-{name}-{theme}.png"), full_page=True)
        page.locator("#open-settings").click()
        expect(page.locator("#library")).to_be_visible()
        if folder and name == "desktop":
            page.screenshot(path=str(Path(folder) / f"settings-{theme}.png"), full_page=True)
        for tab in ("converter", "recorder", "security"):
            page.locator(f'[data-tab="{tab}"]').click()
            expect(page.locator(f"#{tab}")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#back-remote").click()
        page.reload()
        expect(page.locator("html")).to_have_attribute("data-theme", theme)
        assert not errors
    finally:
        context.close()


def test_pin_tap_hold_and_relock(browser, app_server):
    context, page = open_page(browser, app_server[0])
    device = app_server[1]
    try:
        # A locked button opens PIN entry, never schedules a transmission after unlocking.
        page.locator("#remote-1").tap()
        expect(page.locator("#unlock-dialog")).to_be_visible()
        assert device.transmissions == 0
        configure_pin(page)
        expect(page.locator("#pin")).to_have_value("")
        page.locator("#remote-1").tap()
        assert device.transmissions == 0
        bounds = page.locator("#remote-1").bounding_box()
        touch = context.new_cdp_session(page)
        point = {"x": bounds["x"] + bounds["width"] / 2, "y": bounds["y"] + bounds["height"] / 2}
        touch.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
        expect(page.locator("#remote-1")).to_have_class(re.compile("holding"))
        touch.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
        expect(page.locator("#remote-1")).not_to_have_class(re.compile("holding"))
        assert device.transmissions == 0
        touch.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
        expect(page.locator("#message")).to_contain_text("SIMULATED send complete")
        touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        assert device.transmissions == 1
        expect(page.locator("#armed")).to_have_text("LOCKED")
        unlock(page, "9876")
        page.evaluate("window.dispatchEvent(new Event('blur'))")
        expect(page.locator("#armed")).to_have_text("LOCKED")
        assert page.evaluate("Object.keys(localStorage)") == ["subghz.display.v1"]
        stored = page.evaluate("JSON.parse(localStorage.getItem('subghz.display.v1'))")
        assert set(stored) == {"theme", "slots"}
    finally:
        context.close()


def test_assignment_persistence_and_hold_cancellation(browser, app_server):
    context, page = open_page(browser, app_server[0])
    device, store = app_server[1:]
    try:
        page.locator("#open-settings").click()
        signals = store.list()
        page.locator("#slot-1").select_option(signals[1]["id"])
        page.reload()
        page.locator("#back-remote").click()
        expect(page.locator("#remote-name-1")).to_have_text("Porch light")
        configure_pin(page)
        page.locator("#remote-1").focus()
        page.keyboard.down("Space")
        expect(page.locator("#remote-1")).to_have_class(re.compile("holding"))
        page.evaluate("location.hash = 'settings/library'")
        expect(page.locator("#settings-page")).to_be_visible()
        page.keyboard.up("Space")
        assert device.transmissions == 0
        # Deleting the assigned signal leaves a vacant slot, not a different action.
        store.delete(signals[1]["id"])
        page.locator("#refresh").click()
        page.locator("#back-remote").click()
        expect(page.locator("#remote-1")).to_be_disabled()
        expect(page.locator("#remote-name-1")).to_have_text("Not assigned")
    finally:
        context.close()
