import pytest
from sovereign_dc.telemetry import get_telemetry_metrics

def test_telemetry_metrics_output():
    metrics_str = get_telemetry_metrics()
    assert isinstance(metrics_str, str)
    assert "sovereign_battery_soc_percent" in metrics_str
    assert "sovereign_solar_pv_power_watts" in metrics_str
    assert "sovereign_system_power_draw_watts" in metrics_str
    assert "sovereign_temp_coolant_celsius" in metrics_str
    assert "sovereign_load_shedding_active" in metrics_str

def test_telemetry_metrics_parseable():
    metrics_str = get_telemetry_metrics()
    parsed = {}
    for line in metrics_str.split("\n"):
        if line and not line.startswith("#"):
            parts = line.split()
            if len(parts) == 2:
                parsed[parts[0]] = float(parts[1])
                
    assert "sovereign_battery_soc_percent" in parsed
    assert 0.0 <= parsed["sovereign_battery_soc_percent"] <= 100.0
    assert parsed["sovereign_solar_pv_power_watts"] >= 0.0
