import io
import pytest
from unittest.mock import patch, MagicMock
from sovereign_dc import telemetry
from sovereign_dc.telemetry import get_telemetry_metrics, MetricsHandler

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

def test_telemetry_metrics_non_simulation():
    with patch("sovereign_dc.telemetry.SIMULATION", False):
        metrics_str = get_telemetry_metrics()
        assert "sovereign_battery_soc_percent 85.00" in metrics_str
        assert "sovereign_battery_voltage_volts 53.20" in metrics_str
        assert "sovereign_system_power_draw_watts 300.00" in metrics_str

def test_metrics_handler_routes():
    class DummyRequest:
        def makefile(self, *args, **kwargs):
            return io.BytesIO()

    # Test /metrics
    handler = MetricsHandler.__new__(MetricsHandler)
    handler.path = "/metrics"
    handler.wfile = io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.do_GET()
    handler.send_response.assert_called_with(200)
    assert b"sovereign_battery_soc_percent" in handler.wfile.getvalue()

    # Test /health
    handler.path = "/health"
    handler.wfile = io.BytesIO()
    handler.do_GET()
    handler.send_response.assert_called_with(200)
    assert handler.wfile.getvalue() == b"OK\n"

    # Test 404
    handler.path = "/unknown"
    handler.wfile = io.BytesIO()
    handler.do_GET()
    handler.send_response.assert_called_with(404)

def test_telemetry_run():
    mock_server = MagicMock()
    mock_server.serve_forever.side_effect = KeyboardInterrupt
    with patch("sovereign_dc.telemetry.HTTPServer", return_value=mock_server):
        telemetry.run(port=9101, simulation=True)
        mock_server.server_close.assert_called_once()
