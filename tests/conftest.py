"""Pytest global fixtures for Sovereign Mini Datacenter test isolation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sovereign_dc import config as smdc_config
from sovereign_dc.economy import node_state as smdc_node_state
from sovereign_dc.events import reset_event_bus
from sovereign_dc.mcp.tools import reset_enterprise_manager
from sovereign_dc.space.dtn import router as smdc_dtn_router
from sovereign_dc.web import dashboard as smdc_dashboard
from sovereign_dc.web.dashboard import _HARDWARE_LOCK, _HARDWARE_STATE


@pytest.fixture(autouse=True)
def isolate_smdc_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolates HOME, SMDC_STATE_DIR, DTN_DB_PATH, and global singletons per test."""
    test_home = tmp_path / "smdc_home"
    test_home.mkdir(parents=True, exist_ok=True)
    os.chmod(test_home, 0o700)

    monkeypatch.setenv("HOME", str(test_home))
    monkeypatch.setenv("SMDC_STATE_DIR", str(test_home / ".smdc"))
    monkeypatch.setenv("DTN_DB_PATH", str(test_home / "test_dtn_bundles.db"))
    # Legacy-spool fallback must never pick up a real /tmp/dtn_spool.db from the host.
    monkeypatch.setattr(smdc_config, "LEGACY_TMP_DIR", str(tmp_path / "legacy_tmp"))
    monkeypatch.setattr(smdc_dtn_router, "LEGACY_TMP_DIR", str(tmp_path / "legacy_tmp"))
    # The node-identity migration must never adopt (move) a real legacy wallet from the host's temp dir.
    monkeypatch.setattr(smdc_node_state, "_legacy_cli_tmp_dir", lambda: None)
    monkeypatch.delenv("TEMP", raising=False)
    monkeypatch.delenv("SMDC_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("SMDC_CORS_ORIGIN", raising=False)
    monkeypatch.delenv("SMDC_DASHBOARD_ALLOWED_HOSTS", raising=False)
    # Hardware-mode exporter tests point this at a loopback fake; never at a real bridge.
    monkeypatch.delenv("ESP32_BRIDGE_URL", raising=False)

    # The config singleton caches DTN_DB_PATH; without a reset every test shares the first one's spool.
    smdc_config.reset_config()
    reset_event_bus()
    reset_enterprise_manager()
    # Explicit isolation: never serve one test a status payload assembled for another.
    smdc_dashboard.invalidate_status_cache()
    with _HARDWARE_LOCK:
        smdc_dashboard._METAVERSE_ORCH = None
        _HARDWARE_STATE["door_open"] = False
        _HARDWARE_STATE["pdu_outlets"] = [True, True, True, True, True, True, True, True]
        _HARDWARE_STATE["fan_rpm"] = 2400
        _HARDWARE_STATE["last_bundle_tx"] = 0
        smdc_dashboard._ACTUATORS.clear()

    yield

    smdc_config.reset_config()
    reset_event_bus()
    reset_enterprise_manager()
    smdc_dashboard.invalidate_status_cache()
