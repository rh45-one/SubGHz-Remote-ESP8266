"""Strict OOK-only Flipper .sub conversion. Never guess rolling-code/FSK formats."""
from collections import Counter, defaultdict
import re

from .models import Signal, Waveform

OOK_PRESETS = {"FuriHalSubGhzPresetOok270Async", "FuriHalSubGhzPresetOok650Async"}


def _number(value: str, low: int = 1, high: int = 2**32 - 1) -> int:
    if not re.fullmatch(r"[0-9]{1,10}", value):
        raise ValueError("Expected decimal integer")
    result = int(value)
    if not low <= result <= high:
        raise ValueError("Integer outside supported range")
    return result


def _normalize(values: list[int]) -> tuple[list[int], list[str]]:
    pulses: list[int] = []
    warnings = []
    for value in values:
        if not value:
            raise ValueError("Zero pulse duration")
        if pulses and (value > 0) == (pulses[-1] > 0):
            pulses[-1] += value
        else:
            pulses.append(value)
    if pulses and pulses[0] < 0:
        pulses.pop(0)
        warnings.append("Leading idle space removed")
    for i, value in enumerate(pulses):
        if value > 32767:
            raise ValueError("Mark exceeds ESP8266 int16 duration limit")
        if value < -32767:
            pulses[i] = -32767
            if "Long idle spaces capped at 32767 us" not in warnings:
                warnings.append("Long idle spaces capped at 32767 us")
    return pulses, warnings


def _segment(pulses: list[int]) -> tuple[list[int], int, list[str]]:
    frames, frame = [], []
    for pulse in pulses:
        frame.append(pulse)
        if pulse <= -5000:
            frames.append(frame)
            frame = []
    if frame:
        frames.append(frame)
    candidates = [f for f in frames if 2 <= len(f) <= 512 and f[0] > 0]
    if not candidates:
        raise ValueError("No complete burst within 512 pulses; refusing to truncate")

    def matches(a, b):
        return len(a) == len(b) and all((x > 0) == (y > 0) and abs(x-y) <= max(50, abs(x)//5) for x, y in zip(a, b))

    # Prefer repeat-supported full bursts over a partial tail or isolated noise.
    # Bound fuzzy comparisons even for adversarial files containing thousands of tiny frames.
    counts = Counter(tuple(f) for f in candidates)
    representatives = sorted(counts, key=lambda f: (counts[f], len(f)), reverse=True)[:32]
    scored = [(sum(matches(f, other) for other in candidates), len(f), list(f)) for f in representatives]
    repeats, _, best = max(scored, key=lambda score: score[:2])
    warnings = []
    if len(frames) > 1:
        warnings.append("Selected one burst using a 5 ms gap heuristic; inspect before use")
    if repeats > 20:
        warnings.append("Repeat count capped at 20")
    return best, min(repeats, 20), warnings


def _key(protocol: str, fields: dict[str, list[str]]) -> list[int]:
    def one(key, default=None):
        values = fields.get(key)
        if values is None and default is not None:
            return default
        if not values or len(values) != 1:
            raise ValueError(f"Missing/duplicate {key}")
        return values[0]

    sizes = {"Princeton": {24, 32}, "CAME": {12, 18, 24, 25, 42}, "Nice FLO": set(range(12, 25)),
             "Linear": {10}, "GateTX": {24}, "Holtek": {40}}
    if protocol not in sizes:
        raise ValueError(f"Unsupported key protocol: {protocol}")
    bits = _number(one("Bit"), 1, 64)
    if bits not in sizes[protocol]:
        raise ValueError("Unsupported bit count for protocol")
    key = one("Key")
    if not re.fullmatch(r"[0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2}){7}", key):
        raise ValueError("Key must contain exactly eight hexadecimal bytes")
    value = int.from_bytes(bytes.fromhex(key), "big")
    if value >= 1 << bits:
        raise ValueError("Key has set bits outside declared Bit count")
    defaults = {"Princeton": 390, "CAME": 320, "Nice FLO": 700, "Linear": 500, "GateTX": 350, "Holtek": 430}
    te = _number(one("TE", str(defaults[protocol])), 1, 2000)
    if protocol != "Princeton" and te != defaults[protocol]:
        raise ValueError("This Flipper key protocol uses fixed timing; custom TE is unsupported")
    binary = [(value >> i) & 1 for i in range(bits - 1, -1, -1)]
    if protocol in {"Princeton", "Linear"}:
        pulses = [p for bit in binary for p in ((3*te, -te) if bit else (te, -3*te))]
        if protocol == "Princeton":
            guard = _number(one("Guard_time", "30"), 15, 72)
            pulses += [te, -te * guard]
        else:
            pulses[-1] = -te * (42 if binary[-1] else 44)
        return pulses
    # Protocols with an initial LOW header are cyclically rotated to start on a mark.
    gap = {"CAME": {12: 47, 18: 47, 24: 76, 25: 36, 42: 76}.get(bits),
           "Nice FLO": 36, "GateTX": 49, "Holtek": 36}[protocol]
    long_te = 870 if protocol == "Holtek" and te == 430 else te * 2
    pulses = [long_te if protocol == "GateTX" else te]
    for bit in binary:
        pulses.extend((-long_te, te) if bit else (-te, long_te))
    pulses.append(-te * gap)
    return pulses


def parse_sub(text: str, name: str = "Imported signal") -> Signal:
    if len(text.encode("utf-8")) > 262144:
        raise ValueError("File exceeds 256 KiB")
    fields: dict[str, list[str]] = defaultdict(list)
    for line in text.lstrip("\ufeff").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError("Malformed .sub line")
        key, value = line.split(":", 1)
        fields[key.strip()].append(value.strip())

    def one(key):
        if len(fields[key]) != 1:
            raise ValueError(f"Missing/duplicate {key}")
        return fields[key][0]

    filetype = one("Filetype")
    if filetype not in {"Flipper SubGhz RAW File", "Flipper SubGhz Key File"} or one("Version") != "1":
        raise ValueError("Unsupported Flipper file type/version")
    frequency = _number(one("Frequency"))
    if one("Preset") not in OOK_PRESETS:
        raise ValueError("Only standard OOK presets are supported (not FSK/custom registers)")
    protocol = one("Protocol")
    warnings = []
    repeats = 1
    if protocol == "RAW":
        if filetype != "Flipper SubGhz RAW File" or not fields["RAW_Data"]:
            raise ValueError("RAW requires RAW file type and RAW_Data")
        tokens = " ".join(fields["RAW_Data"]).split()
        if len(tokens) > 65536 or any(not re.fullmatch(r"[+-]?[0-9]{1,9}", t) for t in tokens):
            raise ValueError("Invalid/oversized RAW_Data")
        values = [int(t) for t in tokens]
        pulses, warnings = _normalize(values)
        pulses, repeats, segmented = _segment(pulses)
        warnings += segmented
    elif protocol == "BinRAW":
        if "TE" in fields and "Te" in fields:
            raise ValueError("Duplicate BinRAW timing field")
        te = _number(one("TE") if "TE" in fields else one("Te"), 1, 32767)
        counts, chunks = fields["Bit_RAW"], fields["Data_RAW"]
        if not counts or len(counts) != len(chunks):
            raise ValueError("BinRAW needs paired Bit_RAW/Data_RAW blocks")
        values, total = [], 0
        for count, data in zip(counts, chunks):
            count = _number(count, 1, 65536)
            total += count
            if total > 65536 or not re.fullmatch(r"[0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})*", data):
                raise ValueError("Invalid/oversized BinRAW data")
            raw = bytes.fromhex(data)
            if len(raw) != (count + 7) // 8:
                raise ValueError("Bit_RAW and Data_RAW size mismatch")
            values.extend(te if raw[i // 8] & (1 << (7-i % 8)) else -te for i in range(count))
        if "Bit" in fields and _number(one("Bit"), 1, 65536) != total:
            raise ValueError("BinRAW Bit total mismatch")
        pulses, warnings = _normalize(values)
        if len(pulses) > 512:
            pulses, repeats, segmented = _segment(pulses)
            warnings += segmented
    else:
        if filetype != "Flipper SubGhz Key File":
            raise ValueError("Key protocol requires Key file type")
        pulses = _key(protocol, fields)
        # Never cap synthesized timing: unsupported parameters must fail validation.
        if "Repeat" in fields:
            repeats = _number(one("Repeat"), 1, 20)
    return Signal(name=name, frequency_hz=frequency, pulses=pulses, repeats=repeats,
                  source_protocol=protocol, warnings=warnings)


def export_sub(signal: Waveform) -> str:
    if signal.repeats > 1 and signal.pulses[-1] > 0:
        raise ValueError("Repeated export requires an explicit trailing OFF interval")
    pulses = signal.pulses * signal.repeats
    lines = ["Filetype: Flipper SubGhz RAW File", "Version: 1", f"Frequency: {signal.frequency_hz}",
             "Preset: FuriHalSubGhzPresetOok650Async", "Protocol: RAW"]
    lines += ["RAW_Data: " + " ".join(map(str, pulses[i:i+128])) for i in range(0, len(pulses), 128)]
    return "\n".join(lines) + "\n"
