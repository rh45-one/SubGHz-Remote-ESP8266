import pytest
from pydantic import ValidationError

from app.flipper_sub import KEY_PROTOCOLS, parse_sub, export_sub
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
    ("Hormann HSM", 44, "00 00 0F F4 48 1A 7C 03", ([12000, -500], [1000, -500])),
    ("Ansonic", 12, "00 00 00 00 00 00 00 01", ([555, -1111], [1111, -19425])),
    ("BETT", 18, "00 00 00 00 00 00 00 01", ([340, -2000], [2000, -14340])),
    ("Clemsa", 18, "00 00 00 00 00 00 00 01", ([385, -2695], [2695, -19250])),
    ("Doitrand", 37, "00 00 00 00 00 00 00 01", ([700, -400], [400, -24800])),
    ("Dooya", 40, "00 00 00 00 00 00 00 01", ([4758, -1466], [733, -9895])),
    ("Elplast", 18, "00 00 00 00 00 00 00 01", ([230, -1550], [1550, -12400])),
    ("Feron", 32, "00 00 00 00 00 00 00 01", ([350, -750], [500, -4500])),
    ("Holtek_HT12X", 12, "00 00 00 00 00 00 00 01", ([320, -320], [320, -11520])),
    ("Honeywell", 48, "00 00 00 00 00 00 00 01", ([160, -320], [480, -480])),
    ("Intertechno_V3", 32, "00 00 00 00 00 00 00 01", ([275, -10450], [275, -275])),
    ("Legrand", 18, "00 00 00 00 00 00 00 01", ([375, -1125], [1125, -6000])),
    ("Marantec24", 24, "00 00 00 00 00 00 00 01", ([1600, -2400], [800, -15200])),
    ("Mastercode", 36, "00 00 00 00 00 00 00 01", ([1072, -2145], [2145, -15008])),
    ("Nero Radio", 56, "00 00 00 00 00 00 00 01", ([200, -200], [400, -7400])),
    ("Nero Sketch", 40, "00 00 00 00 00 00 00 01", ([330, -330], [990, -330])),
    ("Phoenix_V2", 52, "00 00 00 00 00 00 00 01", ([2562, -853], [853, -25620])),
    ("Roger", 28, "00 00 00 00 00 00 00 01", ([500, -1000], [1000, -9500])),
    ("SMC5326", 25, "00 00 00 00 00 00 00 01", ([300, -900], [300, -7500])),
])
def test_key_known_timings(protocol, bits, key, expected):
    result = parse_sub(sub(protocol, f"Bit: {bits}\nKey: {key}", "Key"))
    assert result.pulses[:2] == expected[0]
    assert result.pulses[-2:] == expected[1]


def test_hormann_hsm_full_frame_and_868mhz():
    raw_sub = (
        "Filetype: Flipper SubGhz Key File\n"
        "Version: 1\n"
        "Frequency: 868350000\n"
        "Preset: FuriHalSubGhzPresetOok650Async\n"
        "Protocol: Hormann HSM\n"
        "Bit: 44\n"
        "Key: 00 00 0F F4 48 1A 7C 03\n"
    )
    signal = parse_sub(raw_sub, name="Hormann Garage")
    assert signal.source_protocol == "Hormann HSM"
    assert signal.frequency_hz == 868_350_000
    assert signal.repeats == 10
    assert len(signal.pulses) == 90
    assert signal.pulses[:2] == [12000, -500]
    # Top 8 bits of 44-bit pattern 0xFF4481A7C03 are 11111111 -> 8x (+1000, -500)
    assert signal.pulses[2:18] == [1000, -500] * 8
    # Bottom 2 bits are 11 -> 2x (+1000, -500)
    assert signal.pulses[-4:] == [1000, -500, 1000, -500]
    # Alias and roundtrip export check
    alias_signal = parse_sub(raw_sub.replace("Protocol: Hormann HSM", "Protocol: Hormann"))
    assert alias_signal.pulses == signal.pulses


@pytest.mark.parametrize("protocol", sorted(KEY_PROTOCOLS))
def test_all_flipper_static_key_protocols_synthesize_valid_waveforms(protocol):
    allowed_bits, default_te, allow_custom_te = KEY_PROTOCOLS[protocol]
    bits = min(allowed_bits)
    sample_val = (1 << (bits - 1)) | 1 if bits > 1 else 1
    key_hex = " ".join(f"{b:02X}" for b in sample_val.to_bytes(8, "big"))
    extra = f"\nTE: {default_te}" if allow_custom_te else ""
    signal = parse_sub(sub(protocol, f"Bit: {bits}\nKey: {key_hex}{extra}", "Key"))
    assert signal.source_protocol == protocol
    assert 2 <= len(signal.pulses) <= 512
    assert signal.pulses[0] > 0
    assert signal.pulses[-1] < 0


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
