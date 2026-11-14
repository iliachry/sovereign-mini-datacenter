"""Extended telemetry tests to close coverage gaps on POST handlers and fault injection."""

import io
import json
from unittest.mock import MagicMock

import pytest

from sovereign_dc.telemetry import MetricsHandler, fault_overrides, get_telemetry_metrics


class TestFaultOverrides:
    """Test fault injection via fault_overrides dict."""

    def setup_method(self):
        fault_overrides.clear()

    def teardown_method(self):
        fault_overrides.clear()

    def test_soc_override(self):
        fault_overrides["soc"] = 15.0
        metrics = get_telemetry_metrics()
        assert "sovereign_battery_soc_percent 15.00" in metrics

    def test_temp_override(self):
        fault_overrides["temp"] = 55.0
        metrics = get_telemetry_metrics()
        assert "sovereign_temp_coolant_celsius 55.00" in metrics
        # Verify rack temps are derived from coolant override
        assert "sovereign_temp_rack_inlet_celsius 47.00" in metrics
        assert "sovereign_temp_rack_exhaust_celsius 60.00" in metrics

    def test_load_shedding_activates_on_low_soc(self):
        fault_overrides["soc"] = 15.0
        metrics = get_telemetry_metrics()
        assert "sovereign_load_shedding_active 1" in metrics

    def test_load_shedding_inactive_on_high_soc(self):
        fault_overrides["soc"] = 85.0
        metrics = get_telemetry_metrics()
        assert "sovereign_load_shedding_active 0" in metrics


class TestMetricsHandlerPOST:
    """Test POST /fault and /fault/clear endpoints (enabled only via ENABLE_FAULT_INJECTION)."""

    @pytest.fixture(autouse=True)
    def _enable_fault_injection(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
        fault_overrides.clear()
        yield
        fault_overrides.clear()

    def test_post_fault_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("ENABLE_FAULT_INJECTION")
        handler = self._make_handler("/fault")
        payload = json.dumps({"soc": 5}).encode("utf-8")
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = io.BytesIO(payload)

        handler.do_POST()
        handler.send_response.assert_called_with(403)
        assert fault_overrides == {}

    @pytest.mark.parametrize(
        "body",
        [b'{"soc": "abc"}', b'{"soc": NaN}', b'{"soc": true}', b'{"voltage": 1}', b"[1, 2]", b'{"temp": Infinity}'],
    )
    def test_post_fault_rejects_non_finite_or_unknown(self, body):
        handler = self._make_handler("/fault")
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)

        handler.do_POST()
        handler.send_response.assert_called_with(400)
        assert fault_overrides == {}
        # /metrics must still render after a rejected payload
        assert "sovereign_battery_soc_percent" in get_telemetry_metrics()

    def _make_handler(self, path: str) -> MetricsHandler:
        handler = MetricsHandler.__new__(MetricsHandler)
        handler.path = path
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def test_post_fault_valid_json(self):
        handler = self._make_handler("/fault")
        payload = json.dumps({"soc": 10.0}).encode("utf-8")
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = io.BytesIO(payload)

        handler.do_POST()
        handler.send_response.assert_called_with(200)
        response_body = handler.wfile.getvalue().decode("utf-8")
        data = json.loads(response_body)
        assert data["status"] == "ok"
        assert data["overrides"]["soc"] == 10.0

    def test_post_fault_invalid_json(self):
        handler = self._make_handler("/fault")
        payload = b"not valid json"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = io.BytesIO(payload)

        handler.do_POST()
        handler.send_response.assert_called_with(400)

    def test_post_fault_clear(self):
        fault_overrides["soc"] = 10.0
        handler = self._make_handler("/fault/clear")
        handler.headers = {"Content-Length": "0"}
        handler.rfile = io.BytesIO(b"")

        handler.do_POST()
        handler.send_response.assert_called_with(200)
        assert len(fault_overrides) == 0

    def test_post_unknown_path(self):
        handler = self._make_handler("/unknown")
        handler.headers = {"Content-Length": "0"}
        handler.rfile = io.BytesIO(b"")

        handler.do_POST()
        handler.send_response.assert_called_with(404)


class TestMetricsHandlerGETAdditional:
    """Additional GET handler coverage."""

    def _make_handler(self, path: str) -> MetricsHandler:
        handler = MetricsHandler.__new__(MetricsHandler)
        handler.path = path
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def test_get_root_path(self):
        handler = self._make_handler("/")
        handler.do_GET()
        handler.send_response.assert_called_with(200)
        assert b"sovereign_battery_soc_percent" in handler.wfile.getvalue()

    def test_get_healthz(self):
        handler = self._make_handler("/healthz")
        handler.do_GET()
        handler.send_response.assert_called_with(200)
        assert handler.wfile.getvalue() == b"OK\n"

    def test_log_message_suppressed(self):
        handler = self._make_handler("/metrics")
        # log_message should do nothing (suppress default HTTP logging)
        handler.log_message("GET /metrics 200")  # Should not raise
