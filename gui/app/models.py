from pydantic import BaseModel, ConfigDict, Field, model_validator


def valid_frequency(hz: int) -> bool:
    return any(low <= hz <= high for low, high in (
        (300_000_000, 348_000_000), (387_000_000, 464_000_000), (779_000_000, 928_000_000)))


class Waveform(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    frequency_hz: int = 433_920_000
    pulses: list[int] = Field(min_length=2, max_length=512)
    repeats: int = Field(default=1, ge=1, le=20)

    @model_validator(mode="after")
    def validate_waveform(self):
        if not valid_frequency(self.frequency_hz):
            raise ValueError("Frequency outside CC1101 bands")
        if self.pulses[0] <= 0:
            raise ValueError("First pulse must be a positive mark")
        for i, pulse in enumerate(self.pulses):
            if not 1 <= abs(pulse) <= 32767:
                raise ValueError("Pulse durations must be nonzero signed int16 microseconds")
            if i and (pulse > 0) == (self.pulses[i - 1] > 0):
                raise ValueError("Pulse signs must alternate")
        if sum(map(abs, self.pulses)) * self.repeats > 2_000_000:
            raise ValueError("Waveform exceeds two-second transmission budget")
        return self


class Signal(Waveform):
    name: str = Field(min_length=1, max_length=80)
    category: str = Field(default="", max_length=80)
    source_protocol: str = Field(default="RAW", max_length=40)
    warnings: list[str] = Field(default_factory=list, max_length=10)


class ImportRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    text: str = Field(max_length=262144)
    name: str = Field(default="Imported signal", min_length=1, max_length=80)


class Envelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    nonce: str = Field(pattern=r"^[0-9a-f]{32}$")
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: str = Field(max_length=7000)
