"""Strict OOK-only Flipper .sub conversion. Never guess rolling-code/FSK formats."""
from collections import Counter, defaultdict
import re

from .models import Signal, Waveform

OOK_PRESETS = {
    "FuriHalSubGhzPresetOok270Async",
    "FuriHalSubGhzPresetOok650Async",
    "AM270",
    "AM650",
}

CAME_TWEE_MAGIC_XOR = (
    0x0E0E0E00, 0x1D1D1D11, 0x2C2C2C22, 0x3B3B3B33, 0x4A4A4A44,
    0x59595955, 0x68686866, 0x77777777, 0x86868688, 0x95959599,
    0xA4A4A4AA, 0xB3B3B3BB, 0xC2C2C2CC, 0xD1D1D1DD, 0xE0E0E0EE,
)

# Canonical Flipper Zero static OOK Key protocols -> (allowed_bits, default_te, allow_custom_te)
KEY_PROTOCOLS: dict[str, tuple[set[int], int, bool]] = {
    "Ansonic": ({12}, 555, False),
    "BETT": ({18}, 340, False),
    "CAME": ({12, 18, 24, 25, 42}, 320, False),
    "CAME TWEE": ({54}, 500, False),
    "Cham_Code": ({7, 8, 9}, 1000, False),
    "Clemsa": ({18}, 385, False),
    "Dickert_MAHS": (set(range(36, 65)), 400, False),
    "Doitrand": ({37}, 400, False),
    "Dooya": ({40}, 366, False),
    "Elplast": ({18}, 230, False),
    "Feron": ({32}, 350, False),
    "GangQi": ({34}, 500, False),
    "GateTX": ({24}, 350, False),
    "Hollarm": ({42}, 200, False),
    "Holtek": ({40}, 430, False),
    "Holtek_HT12X": ({12}, 320, True),
    "Honeywell": ({48}, 160, False),
    "Hormann HSM": ({44}, 500, False),
    "Intertechno_V3": ({32, 36}, 275, False),
    "Legrand": ({18}, 375, True),
    "Linear": ({10}, 500, False),
    "LinearDelta3": ({8}, 500, False),
    "Magellan": ({32}, 200, False),
    "Marantec": ({49}, 1000, False),
    "Marantec24": ({24}, 800, False),
    "Mastercode": ({36}, 1072, False),
    "MegaCode": ({24}, 1000, False),
    "Nero Radio": ({56, 57}, 200, False),
    "Nero Sketch": ({40}, 330, False),
    "Nice FLO": (set(range(12, 25)), 700, False),
    "Phoenix_V2": ({52}, 427, False),
    "Power Smart": ({64}, 225, False),
    "Princeton": ({24, 32}, 390, True),
    "Revers_RB2": ({64}, 250, False),
    "Roger": ({28}, 500, False),
    "SMC5326": ({25}, 300, True),
}

PROTOCOL_ALIASES: dict[str, str] = {
    **{re.sub(r"[\s_-]+", "", name).lower(): name for name in KEY_PROTOCOLS},
    "hormann": "Hormann HSM",
    "hoermann": "Hormann HSM",
    "hoermannhsm": "Hormann HSM",
    "hörmannhsm": "Hormann HSM",
    "chamberlain": "Cham_Code",
    "chamberlaincode": "Cham_Code",
    "honeywellwdb": "Honeywell",
    "holtekth12x": "Holtek_HT12X",
    "reversrb2m": "Revers_RB2",
}

ROLLING_CODE_PROTOCOLS = {
    "alutechat4n", "benincaarc", "cameatomo", "faacslh", "hay21",
    "ido", "ido117111", "jarolift", "keeloq", "kia", "kiaseed",
    "kinggatesstylo4k", "niceflors", "scherkhan", "secplusv1",
    "secplusv2", "security+1.0", "security+2.0", "somfykeytis",
    "somfytelis", "starline",
}


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


def _coalesce_and_rotate(raw: list[int], cap_idle: bool = False) -> tuple[list[int], list[str]]:
    pulses: list[int] = []
    warnings: list[str] = []
    for p in raw:
        if pulses and (p > 0) == (pulses[-1] > 0):
            pulses[-1] += p
        else:
            pulses.append(p)
    if pulses and pulses[0] < 0:
        lead = pulses.pop(0)
        if pulses and pulses[-1] < 0:
            pulses[-1] += lead
        else:
            pulses.append(lead)
    if cap_idle:
        for i, p in enumerate(pulses):
            if p < -32767:
                pulses[i] = -32767
                if "Long idle spaces capped at 32767 us" not in warnings:
                    warnings.append("Long idle spaces capped at 32767 us")
    return pulses, warnings


def _manchester(bits: list[int], te_short: int, te_long: int, marantec_header: bool = False) -> list[int]:
    step = 0
    prev_bit = 0
    raw: list[int] = []

    def to_pulse(res: int) -> int:
        return (-te_short, -te_long, te_long, te_short)[res]

    def advance(curr_bit: int) -> tuple[bool, int]:
        nonlocal step, prev_bit
        if step == 0:
            prev_bit = curr_bit
            res = 0 if prev_bit else 3
            step = 1
            return True, res
        if step == 1:
            res = (prev_bit << 1) + curr_bit
            if curr_bit == prev_bit:
                step = 2
                return False, res
            prev_bit = curr_bit
            return True, res
        res = 0 if curr_bit else 3
        prev_bit = curr_bit
        step = 1
        return True, res

    start = 1 if marantec_header else 0
    if marantec_header:
        ok, res = advance(bits[0])
        if not ok:
            raw.append(to_pulse(res))
            advance(bits[0])
        raw.append(-te_long * 5)
    for b in bits[start:]:
        ok, res = advance(b)
        if not ok:
            raw.append(to_pulse(res))
            _, res = advance(b)
        raw.append(to_pulse(res))
    finish = to_pulse((prev_bit << 1) + prev_bit)
    if finish > 0:
        raw.append(finish)
    return raw


def _resolve_protocol(protocol: str) -> str:
    if protocol in {"RAW", "BinRAW"} or protocol in KEY_PROTOCOLS:
        return protocol
    key = re.sub(r"[\s_-]+", "", protocol).lower()
    if key == "raw":
        return "RAW"
    if key == "binraw":
        return "BinRAW"
    if key in PROTOCOL_ALIASES:
        return PROTOCOL_ALIASES[key]
    if key in ROLLING_CODE_PROTOCOLS:
        raise ValueError(f"Unsupported rolling-code protocol: {protocol}")
    raise ValueError(f"Unsupported key protocol: {protocol}")


def _key(protocol: str, fields: dict[str, list[str]]) -> tuple[list[int], list[str]]:
    def one(key, default=None):
        values = fields.get(key)
        if values is None and default is not None:
            return default
        if not values or len(values) != 1:
            raise ValueError(f"Missing/duplicate {key}")
        return values[0]

    if protocol not in KEY_PROTOCOLS:
        raise ValueError(f"Unsupported key protocol: {protocol}")
    allowed_bits, default_te, allow_custom_te = KEY_PROTOCOLS[protocol]
    bits = _number(one("Bit"), 1, 64)
    if bits not in allowed_bits:
        raise ValueError("Unsupported bit count for protocol")
    key = one("Key")
    if not re.fullmatch(r"[0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2}){7}", key):
        raise ValueError("Key must contain exactly eight hexadecimal bytes")
    value = int.from_bytes(bytes.fromhex(key), "big")
    if value >= 1 << bits:
        raise ValueError("Key has set bits outside declared Bit count")
    if "TE" in fields and "Te" in fields:
        raise ValueError("Duplicate TE timing field")
    te_raw = one("TE") if "TE" in fields else (one("Te") if "Te" in fields else str(default_te))
    te = _number(te_raw, 1, 2000)
    if not allow_custom_te and te != default_te:
        raise ValueError("This Flipper key protocol uses fixed timing; custom TE is unsupported")
    binary = [(value >> i) & 1 for i in range(bits - 1, -1, -1)]

    if protocol in {"Princeton", "Linear", "SMC5326"}:
        pulses = [p for bit in binary for p in ((3 * te, -te) if bit else (te, -3 * te))]
        if protocol == "Princeton":
            guard = _number(one("Guard_time", "30"), 15, 72)
            pulses += [te, -te * guard]
        elif protocol == "SMC5326":
            pulses += [te, -te * 25]
        else:
            pulses[-1] = -te * (42 if binary[-1] else 44)
        return pulses, []

    if protocol in {"CAME", "Nice FLO", "GateTX", "Holtek", "Holtek_HT12X", "Doitrand", "Phoenix_V2", "Dickert_MAHS"}:
        gap = {
            "CAME": {12: 47, 18: 47, 24: 76, 25: 36, 42: 76}.get(bits, 16),
            "Nice FLO": 36,
            "GateTX": 49,
            "Holtek": 36,
            "Holtek_HT12X": 36,
            "Doitrand": 62,
            "Phoenix_V2": 60,
            "Dickert_MAHS": 112,
        }[protocol]
        long_te = {
            "Holtek": 870,
            "Doitrand": 1100,
            "Phoenix_V2": 853,
        }.get(protocol, te * 2)
        start_mark = {
            "GateTX": long_te,
            "Doitrand": te * 2 - 100,
            "Phoenix_V2": te * 6,
        }.get(protocol, te)
        pulses = [start_mark]
        for bit in binary:
            if protocol == "Phoenix_V2":
                pulses.extend((-te, long_te) if bit else (-long_te, te))
            else:
                pulses.extend((-long_te, te) if bit else (-te, long_te))
        pulses.append(-te * gap)
        return _coalesce_and_rotate(pulses, cap_idle=(protocol == "Dickert_MAHS"))

    if protocol == "Ansonic":
        pulses = [555] + [p for bit in binary for p in ((-555, 1111) if bit else (-1111, 555))] + [-555 * 35]
        return pulses, []

    if protocol == "Hormann HSM":
        pulses = [500 * 24, -500] + [p for bit in binary for p in ((1000, -500) if bit else (500, -1000))]
        return pulses, []

    if protocol in {"BETT", "Clemsa", "Mastercode"}:
        short_te, long_te, extra = {
            "BETT": (340, 2000, 2000 * 7),
            "Clemsa": (385, 2695, 2695 * 7),
            "Mastercode": (1072, 2145, 1072 * 13),
        }[protocol]
        pulses = [p for bit in binary for p in ((long_te, -short_te) if bit else (short_te, -long_te))]
        pulses[-1] -= extra
        return pulses, []

    if protocol in {"Elplast", "Roger", "Marantec24"}:
        if protocol == "Elplast":
            pulses = [p for bit in binary for p in ((1550, -230) if bit else (230, -1550))]
            pulses[-1] = -1550 * 8
        elif protocol == "Roger":
            pulses = [p for bit in binary for p in ((1000, -500) if bit else (500, -1000))]
            pulses[-1] = -500 * 19
        else:
            pulses = [p for bit in binary for p in ((800, -3200) if bit else (1600, -2400))]
            pulses[-1] = -(1600 * 9 + 800)
        return pulses, []

    if protocol == "Feron":
        pulses = [p for bit in binary[:-1] for p in ((750, -350) if bit else (350, -750))]
        pulses += [750 if binary[-1] else 350, -500, 500, -4500]
        return pulses, []

    if protocol == "Dooya":
        raw = [-(733 * 12 + (733 if binary[-1] else 366)), 366 * 13, -733 * 2]
        raw += [p for bit in binary for p in ((733, -366) if bit else (366, -733))]
        return _coalesce_and_rotate(raw)

    if protocol == "Honeywell":
        pulses = [p for bit in binary for p in ((320, -160) if bit else (160, -320))] + [480, -480]
        return pulses, []

    if protocol == "Legrand":
        pulses = [3 * te if binary[0] else te]
        pulses += [p for bit in binary[1:] for p in ((-te, 3 * te) if bit else (-3 * te, te))]
        pulses.append(-16 * te)
        return pulses, []

    if protocol == "LinearDelta3":
        raw = [p for bit in binary for p in ((500, -3500) if bit else (2000, -2000))]
        raw[-1] = -500 * (73 if binary[-1] else 70)
        return _coalesce_and_rotate(raw, cap_idle=True)

    if protocol == "Magellan":
        raw = [800, -200] + [200, -200] * 12 + [200, -400, 1200, -400]
        raw += [p for bit in binary for p in ((200, -400) if bit else (400, -200))]
        raw += [200, -400 * 100]
        return _coalesce_and_rotate(raw, cap_idle=True)

    if protocol == "Nero Radio":
        pulses = [200, -200] * 49 + [800, -200]
        pulses += [p for bit in binary for p in ((400, -200) if bit else (200, -400))]
        pulses[-1] = -200 * 37
        return pulses, []

    if protocol == "Nero Sketch":
        pulses = [330, -330] * 47 + [1320, -330]
        pulses += [p for bit in binary for p in ((660, -330) if bit else (330, -660))]
        pulses += [990, -330]
        return pulses, []

    if protocol == "Intertechno_V3":
        pulses = [275, -275 * 38, 275, -275 * 10]
        for idx, bit in enumerate(binary):
            i = bits - idx
            if bits == 36 and i == 9:
                pulses += [275, -275, 275, -275]
            elif bit:
                pulses += [275, -1375, 275, -275]
            else:
                pulses += [275, -275, 275, -1375]
        return pulses, []

    if protocol == "GangQi":
        btn = (value >> 10) & 0xF
        serial = (value >> 18) & 0xFFFF
        bytesum = (0xC8 - (serial >> 8) - (serial & 0xFF) - (0xD0 | btn)) & 0xFF
        data = ((value >> 14) << 14) | (btn << 10) | (bytesum << 2)
        g_bits = [(data >> i) & 1 for i in range(bits - 1, -1, -1)]
        pulses = [p for bit in g_bits for p in ((1200, -500) if bit else (500, -1200))]
        pulses[-1] = -(500 * 4 + 200)
        return pulses, []

    if protocol == "Hollarm":
        btn = (value >> 8) & 0xF
        new_key = ((value >> 12) << 12) | (btn << 8)
        bytesum = (
            ((new_key >> 32) & 0xFF)
            + ((new_key >> 24) & 0xFF)
            + ((new_key >> 16) & 0xFF)
            + ((new_key >> 8) & 0xFF)
        ) & 0xFF
        shifted = (new_key | bytesum) << 2
        h_bits = [(shifted >> i) & 1 for i in range(bits - 1, -1, -1)]
        pulses = [p for bit in h_bits for p in ((200, -1600) if bit else (200, -1000))]
        pulses[-1] = -2400
        return pulses, []

    if protocol == "MegaCode":
        upload = [0] * (bits * 2)
        idx = len(upload) - 1
        upload[idx] = 1000
        idx -= 1
        last_bit = binary[-1]
        for i in range(1, bits):
            b = (value >> i) & 1
            if b:
                upload[idx] = -(5000 if last_bit else 2000)
                last_bit = 1
            else:
                upload[idx] = -(8000 if last_bit else 5000)
                last_bit = 0
            idx -= 1
            upload[idx] = 1000
            idx -= 1
        upload[0] = -(11000 if binary[-1] else 14000)
        return _coalesce_and_rotate(upload)

    if protocol == "Cham_Code":
        code = 0
        for bit in binary:
            code = (code << 4) | (0b0011 if bit else 0b0111)
        if bits == 7:
            code = ((code >> 4) << 16) | ((code & 0xF) << 4) | 0x10000001101
            nbits = 44
        elif bits == 8:
            code = ((code >> 12) << 16) | ((code & 0xFF) << 4) | 0x1000001001
            nbits = 40
        else:
            code = (code << 4) | 0x10000000001
            nbits = 44
        raw = [-1000 * 36] + [1000 if (code >> i) & 1 else -1000 for i in range(nbits - 1, -1, -1)]
        return _coalesce_and_rotate(raw, cap_idle=True)

    if protocol == "Marantec":
        raw = _manchester(binary, 1000, 2000, marantec_header=True)
        return _coalesce_and_rotate(raw)

    if protocol == "CAME TWEE":
        cnt_parcel = value & 0xF
        serial = (value & 0xFFFFFFFF) ^ CAME_TWEE_MAGIC_XOR[cnt_parcel if cnt_parcel < 15 else 0]
        parcel = 0x003FFF7200000000 | (serial ^ CAME_TWEE_MAGIC_XOR[cnt_parcel if cnt_parcel < 15 else 0])
        inv_bits = [1 - ((parcel >> i) & 1) for i in range(bits - 1, -1, -1)]
        raw = _manchester(inv_bits, 500, 1000) + [-500 * 102]
        return _coalesce_and_rotate(raw, cap_idle=True)

    if protocol == "Power Smart":
        inv_bits = [1 - b for b in binary]
        raw = _manchester(inv_bits, 225, 450) + [-450 * 1111]
        return _coalesce_and_rotate(raw, cap_idle=True)

    # Revers_RB2
    raw = _manchester(binary, 250, 500) + [-320]
    return _coalesce_and_rotate(raw)


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
    protocol = _resolve_protocol(one("Protocol"))
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
        pulses, key_warnings = _key(protocol, fields)
        warnings += key_warnings
        # Never cap synthesized timing: unsupported parameters must fail validation.
        if "Repeat" in fields:
            repeats = _number(one("Repeat"), 1, 20)
        elif protocol == "Hormann HSM":
            repeats = 10
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
