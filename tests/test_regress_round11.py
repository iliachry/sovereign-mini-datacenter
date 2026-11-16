"""Regression tests for tri-council review round 11.

1. Legacy relative-path migration in ``scan_and_index`` removes only the legacy
   relative spelling and preserves the freshly indexed absolute-path points.
2. ``backup.sh`` and ``restore.sh`` refuse multiline quoted ``.env`` values with
   non-identifier keys (``1K``, ``K.X``, ``K-X``, ``K[0]``) or backslash-escaped
   quotes (``\"``, ``\'``, ``\\\"``).
3. ``EnterpriseManager.start_app`` does not publish a stale ``.paused`` event after
   a synchronous ``.started`` subscriber triggers load-shedding recovery.
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
from sovereign_dc.enterprise.sdk import AppLifecycleHandler, SMDCClient
from sovereign_dc.events import Event, SovereignEventBus
from tests.test_regress_round7 import _env_file_value


def test_legacy_relative_path_migration_preserves_newly_indexed_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    doc = docs_dir / "note.md"
    doc.write_text("hello world", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ki, "WATCH_DIR", str(docs_dir))
    monkeypatch.setattr(ki, "INCLUDE_GLOBS", ())

    legacy_rel = os.path.join(".", "docs", "note.md")
    canonical_abs = os.path.abspath(str(doc))
    points: dict[str, str] = {"legacy-1": legacy_rel}

    def request(method: str, path: str, body: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        if "/points/delete" in path and body:
            target = body["filter"]["must"][0]["match"]["value"]
            keep = set(body["filter"].get("must_not", [{}])[0].get("has_id", []))
            for pid in [pid for pid, src in points.items() if src == target and pid not in keep]:
                del points[pid]
        elif "/points/scroll" in path:
            return {
                "result": {
                    "points": [{"id": pid, "payload": {"path": src}} for pid, src in points.items()],
                    "next_page_offset": None,
                },
                "status": "ok",
            }
        elif body and "points" in body:
            points.update({str(p["id"]): str(p["payload"]["path"]) for p in body["points"]})
        return {"result": {}, "status": "ok"}

    monkeypatch.setattr(ki, "_qdrant_request", request)
    monkeypatch.setattr(ki, "get_embedding", lambda _text: [0.0, 1.0])

    indexed: set[tuple[str, float]] = set()
    assert ki.seed_indexed_sources(indexed) is True
    assert indexed == {(legacy_rel, ki.UNKNOWN_MTIME)}

    assert ki.scan_and_index(indexed) == (1, 0)
    assert set(points.values()) == {canonical_abs}
    assert {path for path, _ in indexed} == {canonical_abs}

    # A second scan sees the document is up-to-date and keeps the indexed points intact.
    assert ki.scan_and_index(indexed) == (0, 0)
    assert set(points.values()) == {canonical_abs}


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
@pytest.mark.parametrize(
    ("name", "env_text"),
    [
        ("leading_digit_key", 'RESTIC_PASSWORD=actual\n1K="m\nRESTIC_PASSWORD=not-the-password\nZ=1"\n'),
        ("dot_key", 'RESTIC_PASSWORD=actual\nK.X="m\nRESTIC_PASSWORD=not-the-password\nZ=1"\n'),
        ("dash_key", 'RESTIC_PASSWORD=actual\nK-X="m\nRESTIC_PASSWORD=not-the-password\nZ=1"\n'),
        ("bracket_key", 'RESTIC_PASSWORD=actual\nK[0]="m\nRESTIC_PASSWORD=not-the-password\nZ=1"\n'),
        ("escaped_single_quote", "RESTIC_PASSWORD=actual\nOTHER='a multiline \\' value\nRESTIC_PASSWORD=wrong\nend'\n"),
        ("escaped_double_quote", 'RESTIC_PASSWORD=actual\nOTHER="a multiline \\" value\nRESTIC_PASSWORD=wrong\nend"\n'),
        ("triple_backslash_dq", 'RESTIC_PASSWORD=actual\nOTHER="abc\\\\\\"def\nRESTIC_PASSWORD=wrong\nend"\n'),
    ],
)
def test_backup_scripts_refuse_multiline_non_ident_keys_and_escaped_quotes(
    script: str, name: str, env_text: str, tmp_path: Path
) -> None:
    env_file = tmp_path / f"{name}.env"
    env_file.write_text(env_text, encoding="utf-8")
    result = _env_file_value(script, env_file, "RESTIC_PASSWORD")
    assert result.returncode == 2 and result.stdout == "" and "several lines" in result.stderr


def test_start_app_does_not_emit_stale_pause_after_recovery_during_started_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = SovereignEventBus()
    monkeypatch.setattr("sovereign_dc.events.get_event_bus", lambda: bus)

    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    assert registry.register_app(
        EnterpriseRegistry.scaffold_manifest("App", "app-x", power_tier=PowerPriority.L4_IDLE)
    )[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)

    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))

    events: list[str] = []
    bus.subscribe("enterprise.app-x.*", lambda event: events.append(event.event_type))

    def recover_on_started(_event: Event) -> None:
        bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 0, "battery_soc": 90.0}))

    bus.subscribe("enterprise.app-x.started", recover_on_started)

    handler = AppLifecycleHandler("app-x", client=SMDCClient(base_url="http://127.0.0.1:1"))
    try:
        ok, message = manager.start_app("app-x")
        assert ok is True
        assert "held paused" not in message
        assert events == ["enterprise.app-x.started"]
        state = manager.get_runtime_state("app-x")
        assert state is not None and state.status == AppStatus.RUNNING
        assert "app-x" not in manager._shed_paused
        assert handler.is_paused is False
    finally:
        handler.stop()
