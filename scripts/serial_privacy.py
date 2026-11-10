"""Redact local Wi-Fi configuration values before displaying or saving serial output."""
import ast
from pathlib import Path
import re


def redact_network_secrets(text, config_path=None):
    config = Path(config_path) if config_path is not None else Path(__file__).resolve().parents[1] / "firmware/include/secrets.h"
    if not config.exists():
        return text
    try:
        source = config.read_text(encoding="utf-8")
        literals = re.findall(r'^\s*#define\s+WIFI_(?:SSID|PASSWORD)\s+("(?:\\.|[^"\\])*")\s*$', source, re.MULTILINE)
        if len(literals) != 2:
            raise ValueError("Unsupported local configuration format")
        values = [ast.literal_eval(literal) for literal in literals]
        if not all(isinstance(value, str) for value in values):
            raise ValueError("Configuration values must be strings")
        for value in sorted(values, key=len, reverse=True):
            if value:
                text = text.replace(value, "[REDACTED]")
        return text
    except (OSError, ValueError, SyntaxError):
        return "[Serial output withheld: local credential redaction unavailable.]"
