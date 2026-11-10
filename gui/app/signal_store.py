"""Atomic JSON persistence. Run exactly one application worker per data volume."""
import copy
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path

from .models import Signal


class SignalStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        records = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("Signal store is not a JSON list; refusing to overwrite")
        ids = set()
        for record in records:
            signal_id = record["id"]
            if not isinstance(signal_id, str) or signal_id in ids:
                raise ValueError("Invalid/duplicate signal ID")
            ids.add(signal_id)
            Signal.model_validate({k: v for k, v in record.items() if k != "id"})
        return records

    def _write(self, records: list[dict]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix=".signals-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(records, file, ensure_ascii=False, separators=(",", ":"))
                file.flush()
                os.fsync(file.fileno())
            os.replace(filename, self.path)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def list(self) -> list[dict]:
        with self.lock:
            return copy.deepcopy(self._read())

    def get(self, signal_id: str) -> dict:
        with self.lock:
            for record in self._read():
                if record["id"] == signal_id:
                    return copy.deepcopy(record)
        raise KeyError(signal_id)

    def save(self, signal: Signal, signal_id: str | None = None) -> dict:
        with self.lock:
            records = self._read()
            record = {"id": signal_id or str(uuid.uuid4()), **signal.model_dump()}
            if signal_id:
                for index, old in enumerate(records):
                    if old["id"] == signal_id:
                        records[index] = record
                        break
                else:
                    raise KeyError(signal_id)
            else:
                records.append(record)
            self._write(records)
            return copy.deepcopy(record)

    def delete(self, signal_id: str):
        with self.lock:
            records = self._read()
            remaining = [r for r in records if r["id"] != signal_id]
            if len(records) == len(remaining):
                raise KeyError(signal_id)
            self._write(remaining)
