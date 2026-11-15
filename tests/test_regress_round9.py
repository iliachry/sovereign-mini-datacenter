"""Regression tests for tri-council review round 9.

1. backup.sh / restore.sh: key spellings and comment rule of Docker Compose's .env reader.
2. An older load-shedding plan is not applied on top of a newer event.
3. A document has one index identity whether it is given by relative or absolute path.
4. The chain audit reads rows and anchor in one read transaction.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.agents import knowledge_indexer as ki
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.wallet import NodeWallet
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, SovereignEventBus
from tests.test_regress_round7 import _env_file_value

# What `docker compose config --environment` (Compose 5.5.0) reports for this file.
ENV_TEXT = (
    "A=old\nexport A=new\nB=old\nB =new2\nC: yamlform\nD=abc\t#def\nE=abc\t #def\n"
    "F=abc  # two spaces\nG=abc #x #y\n  H=indented\nI=first\nI=second\nK=a=b\n"
    'L = "q" # c\nexport   M  =  spaced\n'
)
EXPECTED = {
    "A": "new",
    "B": "new2",
    "C": "yamlform",
    "D": "abc\t#def",
    "E": "abc",
    "F": "abc",
    "G": "abc",
    "H": "indented",
    "I": "second",
    "K": "a=b",
    "L": "q",
    "M": "spaced",
}


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_backup_scripts_read_key_spellings_and_comments_like_compose(script: str, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_TEXT, encoding="utf-8")
    for key, expected in EXPECTED.items():
        result = _env_file_value(script, env_file, key)
        assert result.returncode == 0 and result.stdout == expected, (key, result.stdout, result.stderr)


def test_older_shedding_plan_is_dropped_when_a_newer_event_arrives(tmp_path: Path) -> None:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    for app_id in ("app-a", "app-b"):
        manifest = EnterpriseRegistry.scaffold_manifest(app_id, app_id, power_tier=PowerPriority.L4_IDLE)
        assert registry.register_app(manifest)[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    for app_id in ("app-a", "app-b"):
        assert manager.start_app(app_id)[0]

    def shed(level: int, soc: float) -> None:
        bus.publish(
            Event(event_type="load_shedding.changed", source="test", payload={"level": level, "battery_soc": soc})
        )

    shed(4, 5.0)
    assert {s.status for s in manager.list_runtime_states()} == {AppStatus.PAUSED}

    fired: list[str] = []

    def critical_again(event: Event) -> None:
        if not fired:
            fired.append(event.event_type)
            shed(4, 5.0)

    bus.subscribe("enterprise.app-a.resumed", critical_again)
    shed(0, 90.0)
    assert fired, "the resumed event of the first app was expected"
    states = {s.manifest.app_id: s.status for s in manager.list_runtime_states()}
    assert states == {"app-a": AppStatus.PAUSED, "app-b": AppStatus.PAUSED}


def test_document_has_one_identity_for_relative_and_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "docs" / "note.md"
    doc.parent.mkdir()
    doc.write_text("hello world", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    upserted: list[dict[str, Any]] = []
    deleted: list[str] = []

    def request(method: str, path: str, body: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        if "/points/delete" in path and body:
            deleted.append(body["filter"]["must"][0]["match"]["value"])
        elif body and "points" in body:
            upserted.extend(body["points"])
        return {"result": {}, "status": "ok"}

    monkeypatch.setattr(ki, "_qdrant_request", request)
    monkeypatch.setattr(ki, "get_embedding", lambda _text: [0.0, 1.0])
    monkeypatch.setattr(ki, "get_current_mode", lambda: "NORMAL", raising=False)

    assert ki.index_file(os.path.join("docs", "note.md")) is True
    assert ki.index_file(str(doc)) is True
    assert {p["id"] for p in upserted} == {ki.chunk_point_id(os.path.abspath(str(doc)), 0)}
    assert {p["payload"]["path"] for p in upserted} == {os.path.abspath(str(doc))}
    assert set(deleted) == {os.path.abspath(str(doc))}
    assert ki.list_documents("docs") == [os.path.abspath(str(doc))]


def test_chain_audit_reads_rows_and_anchor_in_one_transaction(tmp_path: Path) -> None:
    db = str(tmp_path / "ledger.db")
    auditor = Ledger(db_path=db)
    writer = Ledger(db_path=db)
    address = NodeWallet.create("round9-node").address
    writer.mint(address, 1.0)

    statements: list[str] = []
    with auditor._get_connection() as conn:
        conn.set_trace_callback(statements.append)
        auditor._check_chain(conn, None)
        conn.set_trace_callback(None)
    reads = [
        s
        for s in statements
        if s.startswith(("BEGIN", "SELECT * FROM transactions", "SELECT * FROM chain_anchor", "COMMIT"))
    ]
    assert reads[:4] == [
        "BEGIN",
        "SELECT * FROM transactions ORDER BY rowid ASC",
        "SELECT * FROM chain_anchor WHERE id = 1",
        "COMMIT",
    ]
    assert auditor.verify_chain() is True
