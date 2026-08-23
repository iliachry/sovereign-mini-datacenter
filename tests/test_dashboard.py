"""Unit tests for Web Operations Dashboard and REST API module."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

from sovereign_dc.web.dashboard import (
    DashboardHandler,
    get_system_status_payload,
    run_dashboard_server,
)


class TestSystemStatusPayload:
    """Test REST API status assembly."""

    def test_get_system_status_structure(self):
        data = get_system_status_payload()

        assert "node_id" in data
        assert "role" in data
        assert "version" in data
        assert "power" in data
        assert "battery_soc" in data["power"]
        assert "solar_watts" in data["power"]
        assert "thermal" in data
        assert "coolant_celsius" in data["thermal"]
        assert "storage" in data
        assert "free_gb" in data["storage"]
        assert "space" in data
        assert "queued_bundles" in data["space"]
        assert "pqc" in data
        assert data["pqc"]["status"] == "OPERATIONAL"


class TestDashboardHandler:
    """Test HTTP request routing on DashboardHandler."""

    def _make_handler(self, path: str) -> DashboardHandler:
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = path
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def test_get_root_serves_html(self):
        handler = self._make_handler("/")
        handler.do_GET()

        handler.send_response.assert_called_with(200)
        output = handler.wfile.getvalue().decode("utf-8")
        assert "<!DOCTYPE html>" in output
        assert "Sovereign Mini Datacenter" in output
        assert "Operations Control Center" in output

    def test_get_api_status_serves_json(self):
        handler = self._make_handler("/api/status")
        handler.do_GET()

        handler.send_response.assert_called_with(200)
        output = handler.wfile.getvalue().decode("utf-8")
        data = json.loads(output)
        assert "power" in data
        assert "thermal" in data
        assert "pqc" in data

    def test_get_health(self):
        handler = self._make_handler("/health")
        handler.do_GET()

        handler.send_response.assert_called_with(200)
        assert handler.wfile.getvalue() == b"OK\n"

    def test_get_404_unknown_route(self):
        handler = self._make_handler("/unknown_endpoint")
        handler.do_GET()

        handler.send_response.assert_called_with(404)

    def test_log_message_suppression(self):
        handler = self._make_handler("/")
        handler.log_message("GET / 200")  # Should not raise or print


class TestRunDashboardServer:
    """Test dashboard launcher."""

    def test_run_dashboard_server_keyboard_interrupt(self):
        mock_server = MagicMock()
        mock_server.serve_forever.side_effect = KeyboardInterrupt

        with patch("sovereign_dc.web.dashboard.HTTPServer", return_value=mock_server):
            with patch("webbrowser.open") as mock_open:
                run_dashboard_server(port=8888, open_browser=True)
                mock_open.assert_called_with("http://localhost:8888")
                mock_server.server_close.assert_called_once()
