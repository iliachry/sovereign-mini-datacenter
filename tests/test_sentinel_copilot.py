import os
import sys
import json
import logging
import pytest
from unittest.mock import patch, MagicMock
import urllib.error

from sovereign_dc.agents import sentinel_copilot

def test_sentinel_get_telemetry_success():
    sample_metrics = "sovereign_battery_soc_percent 84.5\nsovereign_solar_pv_power_watts 1250.0\n# comment\n"
    mock_resp = MagicMock()
    mock_resp.read.return_value = sample_metrics.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    
    with patch("urllib.request.urlopen", return_value=mock_resp):
        metrics = sentinel_copilot.get_telemetry()
        assert metrics.get("sovereign_battery_soc_percent") == 84.5
        assert metrics.get("sovereign_solar_pv_power_watts") == 1250.0

def test_sentinel_get_telemetry_failure():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        metrics = sentinel_copilot.get_telemetry()
        assert metrics == {}

def test_sentinel_run_copilot_state_transitions(caplog):
    caplog.set_level(logging.INFO)
    # Test transitions: NORMAL -> ECO_PRESERVATION -> SOLAR_SURPLUS_COMPUTE -> NORMAL
    telemetry_sequence = [
        {"sovereign_battery_soc_percent": 18.0, "sovereign_solar_pv_power_watts": 100.0},
        {"sovereign_battery_soc_percent": 85.0, "sovereign_solar_pv_power_watts": 1400.0},
        {"sovereign_battery_soc_percent": 60.0, "sovereign_solar_pv_power_watts": 500.0},
    ]
    
    call_count = 0
    def mock_get_telemetry():
        nonlocal call_count
        if call_count < len(telemetry_sequence):
            res = telemetry_sequence[call_count]
            call_count += 1
            return res
        raise StopIteration("End test loop")

    with patch("sovereign_dc.agents.sentinel_copilot.get_telemetry", side_effect=mock_get_telemetry):
        with patch("time.sleep", return_value=None):
            with pytest.raises(StopIteration):
                sentinel_copilot.run_copilot()

    assert "Sentinel Trigger: Battery SoC at 18.0%" in caplog.text
    assert "Solar Surplus (1400W, 85.0% SoC)" in caplog.text
    assert "Nominal operating conditions" in caplog.text
