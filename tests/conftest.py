"""Pytest global fixtures for Sovereign Mini Datacenter test isolation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sovereign_dc.events import reset_event_bus
from sovereign_dc.mcp.tools import reset_enterprise_manager
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
    monkeypatch.delenv("SMDC_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("SMDC_CORS_ORIGIN", raising=False)

    reset_event_bus()
    reset_enterprise_manager()
    with _HARDWARE_LOCK:
        _HARDWARE_STATE["door_open"] = False
        _HARDWARE_STATE["pdu_outlets"] = [True, True, True, True, True, True, True, True]
        _HARDWARE_STATE["fan_rpm"] = 2400
        _HARDWARE_STATE["last_bundle_tx"] = 0

    yield

    reset_event_bus()
    reset_enterprise_manager()
