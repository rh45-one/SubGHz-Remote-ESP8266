import concurrent.futures
import json

import pytest

from app.models import Signal
from app.signal_store import SignalStore


def test_crud_and_reload(tmp_path):
    store = SignalStore(tmp_path / "signals.json")
    assert store.list() == []
    signal = Signal(name="Lamp", pulses=[350, -350])
    saved = store.save(signal)
    saved["pulses"][0] = 1
    assert store.get(saved["id"])["pulses"][0] == 350
    signal.name = "Updated"
    store.save(signal, saved["id"])
    assert SignalStore(store.path).list()[0]["name"] == "Updated"
    store.delete(saved["id"])
    assert store.list() == []
    with pytest.raises(KeyError):
        store.delete(saved["id"])


def test_threads_no_lost_updates(tmp_path):
    store = SignalStore(tmp_path / "signals.json")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: store.save(Signal(name=f"Signal {i}", pulses=[350, -350])), range(30)))
    assert len(store.list()) == 30
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_store_preserved(tmp_path):
    path = tmp_path / "signals.json"
    path.write_text('{"broken":', encoding="utf-8")
    store = SignalStore(path)
    with pytest.raises(json.JSONDecodeError):
        store.save(Signal(name="New", pulses=[350, -350]))
    assert path.read_text() == '{"broken":'
