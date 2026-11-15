"""Regression tests for tri-council review round 10.

1. A shedding action re-checks the plan generation under the lock that changes the app state.
2. backup.sh / restore.sh refuse a .env with a multi-line quoted value or an embedded CR.
3. remove_file removes the absolute-path entry that index_file wrote.
4. AGENTS.md describes what hal/gpu.py and hal/storage.py actually read.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.agents import knowledge_indexer as ki
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, SovereignEventBus
from tests.test_regress_round7 import _env_file_value

ROOT = Path(__file__).resolve().parents[1]


def _paused_app(tmp_path: Path) -> tuple[EnterpriseManager, SovereignEventBus]:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    assert registry.register_app(
        EnterpriseRegistry.scaffold_manifest("App", "app-x", power_tier=PowerPriority.L4_IDLE)
    )[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    assert manager.start_app("app-x")[0]
    return manager, bus


def _shed(bus: SovereignEventBus, level: int, soc: float) -> None:
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": level, "battery_soc": soc}))


def test_resume_of_an_older_plan_is_refused_under_the_lock(tmp_path: Path) -> None:
    manager, bus = _paused_app(tmp_path)
    _shed(bus, 4, 5.0)
    old_generation = manager._shed_generation
    _shed(bus, 4, 5.0)  # a newer event: the app stays paused
    ok, message = manager.resume_app("app-x", reason="stale recovery", plan_generation=old_generation)
    assert ok is False and "newer load-shedding event" in message
    state = manager.get_runtime_state("app-x")
    assert state is not None and state.status == AppStatus.PAUSED
    assert "app-x" in manager._shed_paused


def test_pause_of_an_older_plan_is_refused_under_the_lock(tmp_path: Path) -> None:
    manager, bus = _paused_app(tmp_path)
    _shed(bus, 0, 90.0)
    old_generation = manager._shed_generation
    _shed(bus, 0, 90.0)
    ok, _ = manager._pause("app-x", "stale shed", by_shedding=True, plan_generation=old_generation)
    assert ok is False
    state = manager.get_runtime_state("app-x")
    assert state is not None and state.status == AppStatus.RUNNING


def test_operator_actions_are_never_treated_as_stale(tmp_path: Path) -> None:
    manager, bus = _paused_app(tmp_path)
    _shed(bus, 0, 90.0)
    assert manager.pause_app("app-x")[0] is True
    assert manager.resume_app("app-x")[0] is True


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_backup_scripts_refuse_multiline_values_and_embedded_cr(script: str, tmp_path: Path) -> None:
    multiline = tmp_path / "ml.env"
    multiline.write_text(
        "RESTIC_PASSWORD=actual\nOTHER='a multiline value\nRESTIC_PASSWORD=not-the-password\nend'\n", encoding="utf-8"
    )
    result = _env_file_value(script, multiline, "RESTIC_PASSWORD")
    assert result.returncode == 2 and result.stdout == "" and "several lines" in result.stderr

    crlf = tmp_path / "cr.env"
    crlf.write_bytes(b'A=abc\rdef\r\nB=crlf\r\nC="q"\r\n')
    assert _env_file_value(script, crlf, "A").returncode == 2
    for key, expected in (("B", "crlf"), ("C", "q")):
        result = _env_file_value(script, crlf, key)
        assert result.returncode == 0 and result.stdout == expected, (key, result.stdout, result.stderr)


def test_remove_file_removes_what_index_file_wrote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = tmp_path / "docs" / "note.md"
    doc.parent.mkdir()
    doc.write_text("hello world", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    points: dict[str, str] = {}

    def request(method: str, path: str, body: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        if "/points/delete" in path and body:
            target = body["filter"]["must"][0]["match"]["value"]
            keep = set(body["filter"].get("must_not", [{}])[0].get("has_id", []))
            for point_id in [pid for pid, source in points.items() if source == target and pid not in keep]:
                del points[point_id]
        elif body and "points" in body:
            points.update({p["id"]: p["payload"]["path"] for p in body["points"]})
        return {"result": {}, "status": "ok"}

    monkeypatch.setattr(ki, "_qdrant_request", request)
    monkeypatch.setattr(ki, "get_embedding", lambda _text: [0.0, 1.0])
    relative = os.path.join("docs", "note.md")
    assert ki.index_file(relative) is True and len(points) == 1
    doc.unlink()
    assert ki.remove_file(relative) is True
    assert points == {}

    # A point an earlier release stored under the relative spelling is removed as well.
    points["legacy"] = relative
    assert ki.remove_file(relative) is True and points == {}


def test_agents_md_describes_what_the_hal_reads() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for claim in (
        "parsing power draw, temperature, and utilization",
        "wear-out percentage",
        "S.M.A.R.T. & IOPS telemetry",
    ):
        assert claim not in agents
    gpu = (ROOT / "src" / "sovereign_dc" / "hal" / "gpu.py").read_text(encoding="utf-8")
    assert "--query-gpu=name,memory.total" in gpu
    storage = (ROOT / "src" / "sovereign_dc" / "hal" / "storage.py").read_text(encoding="utf-8")
    assert "shutil.disk_usage" in storage and "smartctl" not in storage
