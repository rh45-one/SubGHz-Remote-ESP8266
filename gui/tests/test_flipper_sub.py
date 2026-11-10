import pytest
from pydantic import ValidationError

from app.flipper_sub import parse_sub, export_sub
from app.models import Waveform


def sub(protocol="RAW", data="RAW_Data: 350 -1050 1050 -350 350 -10850", filetype="RAW", frequency=433920000):
    return f"Filetype: Flipper SubGhz {filetype} File\nVersion: 1\nFrequency: {frequency}\nPreset: FuriHalSubGhzPresetOok650Async\nProtocol: {protocol}\n{data}\n"


def test_raw_repeats_and_roundtrip():
    burst = [350, -1050] * 24 + [350, -10850]
    signal = parse_sub(sub(data="RAW_Data: " + " ".join(map(str, burst * 3))))
    assert signal.pulses == burst and signal.repeats == 3
    restored = parse_sub(export_sub(signal))
    assert restored.pulses == burst and restored.repeats == 3


def test_multiline_merge_idle_and_cap():
    result = parse_sub(sub(data="RAW_Data: -99999 100 250 -300\nRAW_Data: -50 350 -99999"))
    assert result.pulses == [350, -350, 350, -32767]
    assert len(result.warnings) == 2


@pytest.mark.parametrize("protocol,bits,key,expected", [
    ("Princeton", 24, "00 00 00 00 00 00 00 01", ([390, -1170], [390, -11700])),
    ("CAME", 12, "00 00 00 00 00 00 00 01", ([320, -320], [320, -15040])),
    ("Nice FLO", 12, "00 00 00 00 00 00 00 01", ([700, -700], [700, -25200])),
    ("Linear", 10, "00 00 00 00 00 00 00 01", ([500, -1500], [1500, -21000])),
    ("GateTX", 24, "00 00 00 00 00 00 00 01", ([700, -350], [350, -17150])),
    ("Holtek", 40, "00 00 00 00 00 00 00 01", ([430, -430], [430, -15480])),
])
def test_key_known_timings(protocol, bits, key, expected):
    result = parse_sub(sub(protocol, f"Bit: {bits}\nKey: {key}", "Key"))
    assert result.pulses[:2] == expected[0]
    assert result.pulses[-2:] == expected[1]


def test_came24_header_and_princeton_guard():
    assert parse_sub(sub("CAME", "Bit: 24\nKey: 00 00 00 00 00 00 00 00", "Key")).pulses[-1] == -24320
    assert parse_sub(sub("Princeton", "Bit: 24\nKey: 00 00 00 00 00 00 00 00\nTE: 300\nGuard_time: 31", "Key")).pulses[-1] == -9300


def test_binraw_msb_and_blocks():
    result = parse_sub(sub("BinRAW", "Bit: 16\nTE: 100\nBit_RAW: 8\nData_RAW: CC\nBit_RAW: 8\nData_RAW: F0", "Key"))
    assert result.pulses == [200, -200, 200, -200, 400, -400]


@pytest.mark.parametrize("text", [
    sub(data="RAW_Data: 0 -1"), sub(data="RAW_Data: 35000 -350"), sub(data="RAW_Data: nope"),
    sub().replace("Version: 1", "Version: 2"), sub() + "Frequency: 433920000\n",
    sub().replace("Ook650Async", "2FSKDev238Async"), sub(frequency=500000000),
    sub("KeeLoq", "Bit: 64\nKey: 00 00 00 00 00 00 00 01", "Key"),
    sub("Princeton", "Bit: 24\nKey: 00 00 00 00 FF 00 00 01", "Key"),
    sub("BinRAW", "TE: 100\nBit_RAW: 9\nData_RAW: FF", "Key"),
    sub("BinRAW", "TE: 100\nBit: 9\nBit_RAW: 8\nData_RAW: F0", "Key"),
    sub(data="RAW_Data: " + "350 -350 " * 300),
    "A" * 262145,
], ids=["zero", "long-mark", "bad-token", "version", "duplicate", "fsk", "frequency", "rolling", "key-range", "binraw-size", "binraw-total", "too-many-pulses", "file-size"])
def test_reject_unsupported_or_invalid(text):
    with pytest.raises(ValueError):
        parse_sub(text)


@pytest.mark.parametrize("kwargs", [
    {"pulses": [100, 200]}, {"pulses": [-100, 200]}, {"pulses": [True, -100]},
    {"pulses": [100, -32768]}, {"pulses": [100, 0]}, {"pulses": [100, -100], "repeats": 0},
    {"pulses": [32767, -32767] * 20, "repeats": 2},
    {"pulses": [100, -100], "frequency_hz": 433920000.0},
])
def test_waveform_validation(kwargs):
    with pytest.raises(ValidationError):
        Waveform(**kwargs)
