import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location("serial_privacy", Path(__file__).resolve().parents[2] / "scripts/serial_privacy.py")
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


def test_redacts_network_name_and_password_without_hiding_ip(tmp_path):
    config = tmp_path / "secrets.h"
    config.write_text('#pragma once\n#define WIFI_SSID "fixture-network"\n#define WIFI_PASSWORD "fixture-password"\n', encoding="utf-8")
    output = privacy.redact_network_secrets("fixture-network fixture-password [NET] IP address: 192.0.2.1", config)
    assert output == "[REDACTED] [REDACTED] [NET] IP address: 192.0.2.1"


def test_unrecognized_config_withholds_output(tmp_path):
    config = tmp_path / "secrets.h"
    config.write_text("unexpected config", encoding="utf-8")
    assert "withheld" in privacy.redact_network_secrets("must not leak raw output", config)


def test_missing_config_preserves_normal_boot_output(tmp_path):
    assert privacy.redact_network_secrets("[BOOT] Ready", tmp_path / "missing.h") == "[BOOT] Ready"
