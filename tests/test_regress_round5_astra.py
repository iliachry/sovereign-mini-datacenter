"""Regression tests for the round-5 independent review (GPT-6 Astra) findings.

1. ``smdc app init`` / ``create_project_scaffold`` never overwrite an application silently.
2. Requested package signing that fails is a packaging failure (fail closed).
3. A first Qdrant upsert whose outcome is unknown leaves the document tracked.
4. The documented ``.env`` form of the Traefik basic-auth hash (regression gate present).
7. The MCP ``spool_dtn_bundle`` schema accepts the binary-only request the handler supports.
8. Every ``smdc ...`` command named in the documents exists.
9. The pricing rule stated in README.md / AGENTS.md is the one ``market.py`` implements.

No external network: Qdrant is a fake, every file lives under ``tmp_path``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sovereign_dc import cli
from sovereign_dc.agents import knowledge_indexer
from sovereign_dc.economy import market, node_state
from sovereign_dc.economy.market import BASE_SERVICE_PRICING, ComputeMarket, ServiceType
from sovereign_dc.enterprise import registry as registry_module
from sovereign_dc.enterprise.manager import EnterpriseManager, package_sidecar_path, verify_package
from sovereign_dc.enterprise.registry import EnterpriseRegistry, ScaffoldConflictError
from sovereign_dc.mcp import tools as mcp_tools
from sovereign_dc.security.pqc import PQCAlgorithm, PQCSigner, get_node_pqc_keypair

ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD_FILES = {"smdc-app.json", "smdc-app.yaml", "app.py", "Dockerfile", "README.md"}
USER_CODE = "IMPORTANT_USER_LOGIC = 42\n"


def _manifest(app_id: str = "demo-app") -> Any:
    return EnterpriseRegistry.scaffold_manifest(name="Demo App", app_id=app_id)


def _run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Runs ``smdc <argv>`` in-process and returns its exit status."""
    monkeypatch.setattr(sys, "argv", ["smdc", *argv])
    try:
        cli.main()
    except SystemExit as e:
        return int(e.code or 0)
    return 0


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


# ── 1. Scaffolding never destroys an application under development ────────────────────────


def test_scaffold_refuses_to_overwrite_and_writes_nothing(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.py").write_text(USER_CODE, encoding="utf-8")
    (app_dir / "notes.txt").write_text("mine", encoding="utf-8")
    before = _snapshot(app_dir)

    with pytest.raises(ScaffoldConflictError) as excinfo:
        EnterpriseRegistry.create_project_scaffold(app_dir, _manifest())

    assert excinfo.value.existing == [app_dir / "app.py"]
    assert "app.py" in str(excinfo.value)
    # was: app.py replaced by the sample, four more files written
    assert _snapshot(app_dir) == before


@pytest.mark.parametrize("existing", sorted(SCAFFOLD_FILES))
def test_any_single_existing_scaffold_file_blocks_the_whole_scaffold(tmp_path: Path, existing: str) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / existing).write_text("keep me", encoding="utf-8")
    with pytest.raises(ScaffoldConflictError):
        EnterpriseRegistry.create_project_scaffold(app_dir, _manifest())
    assert _snapshot(app_dir) == {existing: b"keep me"}


def test_scaffold_into_a_directory_without_scaffold_files_still_works(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "notes.txt").write_text("mine", encoding="utf-8")
    EnterpriseRegistry.create_project_scaffold(app_dir, _manifest())
    assert {p.name for p in app_dir.iterdir()} == SCAFFOLD_FILES | {"notes.txt"}
    # Manifest-only scaffolding does not look at (or write) the sample files.
    other = tmp_path / "other"
    other.mkdir()
    (other / "app.py").write_text(USER_CODE, encoding="utf-8")
    EnterpriseRegistry.create_project_scaffold(other, _manifest(), create_sample_code=False)
    assert (other / "app.py").read_text(encoding="utf-8") == USER_CODE
    assert {p.name for p in other.iterdir()} == {"app.py", "smdc-app.json", "smdc-app.yaml"}


def test_force_moves_every_existing_file_to_a_backup_before_replacing_it(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    EnterpriseRegistry.create_project_scaffold(app_dir, _manifest())
    (app_dir / "app.py").write_text(USER_CODE, encoding="utf-8")
    originals = {name: (app_dir / name).read_bytes() for name in SCAFFOLD_FILES}

    backups: list[tuple[Path, Path]] = []
    EnterpriseRegistry.create_project_scaffold(app_dir, _manifest(), force=True, backups=backups)

    assert {original.name for original, _ in backups} == SCAFFOLD_FILES
    for original, backup in backups:
        assert re.fullmatch(re.escape(original.name) + r"\.bak-\d{8}T\d{6}Z(-\d+)?", backup.name)
        assert backup.read_bytes() == originals[original.name]  # nothing was destroyed
    assert (app_dir / "app.py").read_text(encoding="utf-8") != USER_CODE
    assert "Sample Enterprise Workload" in (app_dir / "app.py").read_text(encoding="utf-8")

    # A second forced run in the same second must not overwrite the first backups.
    again: list[tuple[Path, Path]] = []
    with patch.object(registry_module.time, "strftime", return_value="20260101T000000Z"):
        EnterpriseRegistry.create_project_scaffold(app_dir, _manifest(), force=True, backups=again)
        third: list[tuple[Path, Path]] = []
        EnterpriseRegistry.create_project_scaffold(app_dir, _manifest(), force=True, backups=third)
    assert {b for _, b in again}.isdisjoint({b for _, b in third})
    assert all(b.exists() for _, b in backups + again + third)


def test_scaffold_writes_are_atomic_and_leave_no_temp_file(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    replaced: list[str] = []
    real_replace = os.replace

    def failing_replace(src: Any, dst: Any) -> None:
        replaced.append(os.path.basename(dst))
        if os.path.basename(dst) == "app.py":
            raise OSError("disk full")
        real_replace(src, dst)

    with patch.object(registry_module.os, "replace", side_effect=failing_replace), pytest.raises(OSError):
        EnterpriseRegistry.create_project_scaffold(app_dir, _manifest())
    # Every file goes through temp-file + rename; the failed one left neither itself nor its temp file.
    assert "app.py" in replaced
    assert not (app_dir / "app.py").exists()
    assert [p.name for p in app_dir.iterdir() if p.name.endswith(".tmp")] == []


def test_cli_app_init_refuses_then_forces_with_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.py").write_text(USER_CODE, encoding="utf-8")

    rc = _run_cli(monkeypatch, "app", "init", "Demo", "--target-dir", str(app_dir))
    out = capsys.readouterr().out
    assert rc == 1  # was 0, with app.py replaced
    assert str(app_dir / "app.py") in out and "--force" in out and "Nothing was written" in out
    assert _snapshot(app_dir) == {"app.py": USER_CODE.encode()}

    rc = _run_cli(monkeypatch, "app", "init", "Demo", "--target-dir", str(app_dir), "--force")
    out = capsys.readouterr().out
    assert rc == 0
    backups = list(app_dir.glob("app.py.bak-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == USER_CODE
    assert backups[0].name in out
    assert SCAFFOLD_FILES <= {p.name for p in app_dir.iterdir()}

    # A fresh directory needs no flag.
    assert _run_cli(monkeypatch, "app", "init", "Demo", "--target-dir", str(tmp_path / "fresh")) == 0


def test_mcp_scaffold_tool_writes_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The MCP tool returns a manifest preview only: it cannot overwrite anything (and has no force)."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    (work / "app.py").write_text(USER_CODE, encoding="utf-8")
    tool = next(t for t in mcp_tools.get_mcp_tools() if t.name == "scaffold_enterprise_app")
    assert "force" not in tool.input_schema["properties"]
    assert "target_dir" not in tool.input_schema["properties"]
    result = mcp_tools.tool_scaffold_enterprise_app({"name": "Demo", "target_dir": str(work), "force": True})
    assert result["valid"] is True
    assert _snapshot(work) == {"app.py": USER_CODE.encode()}
    assert "no file or directory is written" in tool.description


def test_init_force_is_documented_where_init_is_shown() -> None:
    for name in ("README.md", "ENTERPRISE_ONBOARDING.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert re.search(r"smdc app init .*--force", text), name
        assert ".bak-" in text, name


# ── 2. Requested signing that fails is a packaging failure ────────────────────────────────


@pytest.fixture
def app_dir(tmp_path: Path) -> Path:
    return EnterpriseRegistry.create_project_scaffold(tmp_path / "pkg-app", _manifest("pkg-app"))


def _manager(tmp_path: Path) -> EnterpriseManager:
    return EnterpriseManager(registry=EnterpriseRegistry(registry_file=tmp_path / "registry.json"))


def test_signing_failure_fails_the_packaging_and_leaves_no_artifact(
    tmp_path: Path, app_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    out_dir = tmp_path / "out"
    out = out_dir / "pkg.smdc-app"
    caplog.set_level(logging.ERROR)
    with patch.object(PQCSigner, "sign", side_effect=RuntimeError("HSM unavailable")):
        ok, msg, meta = _manager(tmp_path).package_app(app_dir, output_path=out, sign_pqc=True)
    assert ok is False  # was True, with pqc_signature None
    assert "HSM unavailable" in msg and "signing failed" in msg.lower()
    assert meta == {}
    assert list(out_dir.iterdir()) == []  # no archive, no sidecar, no temp file
    assert "signing failed" in caplog.text


@pytest.mark.parametrize("empty", [b"", None])
def test_an_empty_signature_is_a_signing_failure(tmp_path: Path, app_dir: Path, empty: Any) -> None:
    out = tmp_path / "out" / "pkg.smdc-app"
    with patch.object(PQCSigner, "sign", return_value=empty):
        ok, _, _ = _manager(tmp_path).package_app(app_dir, output_path=out, sign_pqc=True)
    assert ok is False
    assert not out.exists() and not package_sidecar_path(out).exists()


def test_a_failed_signing_run_keeps_the_previous_good_package(tmp_path: Path, app_dir: Path) -> None:
    out = tmp_path / "out" / "pkg.smdc-app"
    manager = _manager(tmp_path)
    ok, _, meta = manager.package_app(app_dir, output_path=out, sign_pqc=True)
    assert ok and meta["pqc_signature"]
    before = (out.read_bytes(), package_sidecar_path(out).read_bytes())
    (app_dir / "app.py").write_text("print('v2')\n", encoding="utf-8")
    with patch.object(PQCSigner, "sign", side_effect=RuntimeError("boom")):
        ok, _, _ = manager.package_app(app_dir, output_path=out, sign_pqc=True)
    assert ok is False
    assert (out.read_bytes(), package_sidecar_path(out).read_bytes()) == before
    trusted = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87).public_key
    assert verify_package(out, trusted) == (True, "ok")
    assert sorted(p.name for p in out.parent.iterdir()) == ["pkg.smdc-app", "pkg.smdc-app.sig"]


def test_packaging_inside_the_app_directory_never_packs_its_own_temp_files(tmp_path: Path, app_dir: Path) -> None:
    import tarfile

    out = app_dir / "pkg.smdc-app"
    ok, _, _ = _manager(tmp_path).package_app(app_dir, output_path=out, sign_pqc=False)
    assert ok
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert sorted(names) == sorted(SCAFFOLD_FILES)
    assert [p.name for p in app_dir.iterdir() if ".tmp" in p.name] == []


def test_unsigned_package_is_not_accepted_by_verify_package(tmp_path: Path, app_dir: Path) -> None:
    out = tmp_path / "out" / "pkg.smdc-app"
    ok, _, meta = _manager(tmp_path).package_app(app_dir, output_path=out, sign_pqc=False)
    assert ok and meta["pqc_signature"] is None
    trusted = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87).public_key
    assert verify_package(out, trusted) == (False, "package is not signed")


def test_cli_package_exits_non_zero_when_signing_fails(
    tmp_path: Path, app_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out" / "pkg.smdc-app"
    with patch.object(PQCSigner, "sign", side_effect=RuntimeError("HSM unavailable")):
        rc = _run_cli(monkeypatch, "app", "package", str(app_dir), "--output", str(out))
    text = capsys.readouterr().out
    assert rc == 1  # was 0
    assert "Omitted (--no-pqc)" not in text and "ready for air-gapped deployment" not in text
    assert "HSM unavailable" in text
    assert not out.exists() and not package_sidecar_path(out).exists()


def test_cli_package_says_omitted_only_when_no_pqc_was_passed(
    tmp_path: Path, app_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out" / "pkg.smdc-app"
    assert _run_cli(monkeypatch, "app", "package", str(app_dir), "--output", str(out), "--no-pqc") == 0
    assert "Omitted (--no-pqc)" in capsys.readouterr().out

    assert _run_cli(monkeypatch, "app", "package", str(app_dir), "--output", str(out)) == 0
    text = capsys.readouterr().out
    assert "signature attached" in text and "Omitted" not in text

    # Defence in depth: success without a signature and without --no-pqc is never reported as an opt-out.
    unsigned = (True, "Application packaged", {"app_id": "x", "version": "1", "pqc_signature": None})
    with patch.object(EnterpriseManager, "package_app", return_value=unsigned):
        rc = cli.cmd_app_package(argparse.Namespace(path=str(app_dir), output=None, no_pqc=False))
    text = capsys.readouterr().out
    assert rc == 1 and "Omitted (--no-pqc)" not in text and "ready for air-gapped" not in text


def test_adopted_legacy_file_with_unrestrictable_mode_is_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    src, dst = tmp_path / "legacy_wallet.json", tmp_path / "wallet.json"
    src.write_text("{}", encoding="utf-8")
    caplog.set_level(logging.WARNING)
    with patch.object(node_state.os, "chmod", side_effect=PermissionError("not permitted")):
        node_state._move(str(src), str(dst))
    assert dst.exists() and not src.exists()
    assert "Could not restrict adopted file" in caplog.text  # was: silently ignored


# ── 3. A first upsert with an unknown outcome leaves the document tracked ─────────────────


def _resp(body: Any) -> MagicMock:
    resp = MagicMock()
    resp.status = 200
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.headers = {}
    resp.__enter__.return_value = resp
    return resp


class FakeQdrant:
    """In-memory Qdrant whose upserts can commit and then lose the response, or be refused."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}
        self.upserts = 0
        self.deletes = 0
        self.commit_then_timeout: set[int] = set()  # upsert numbers that are stored, then time out
        self.refuse: set[int] = set()  # upsert numbers that are not stored
        self.fail_delete = False

    def __call__(self, req: Any, timeout: float = 0) -> MagicMock:
        url, method = req.full_url, req.get_method()
        body = json.loads(req.data) if req.data else {}
        if "/points/delete" in url:
            self.deletes += 1
            if self.fail_delete:
                raise urllib.error.URLError("delete refused")
            path = body["filter"]["must"][0]["match"]["value"]
            keep = {i for cond in body["filter"].get("must_not", []) for i in cond.get("has_id", [])}
            self.points = {k: v for k, v in self.points.items() if v["payload"]["path"] != path or k in keep}
            return _resp({"result": {"status": "completed"}})
        if method == "PUT" and "/points" in url:
            self.upserts += 1
            if self.upserts in self.refuse:
                raise urllib.error.URLError("upsert refused")
            for point in body["points"]:
                self.points[point["id"]] = point
            if self.upserts in self.commit_then_timeout:
                raise TimeoutError("response timed out after the commit")
            return _resp({"result": {"status": "completed"}})
        return _resp({})

    def count(self, path: Path) -> int:
        return sum(1 for p in self.points.values() if p["payload"]["path"] == str(path))


@pytest.fixture
def qdrant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeQdrant]:
    fake = FakeQdrant()
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "_cleanup_pending", set())
    monkeypatch.setattr(knowledge_indexer, "_reconcile_pending", set())
    with (
        patch.object(knowledge_indexer, "get_embedding", return_value=[0.1] * knowledge_indexer.EMBEDDING_DIM),
        patch("urllib.request.urlopen", side_effect=fake),
    ):
        yield fake


def test_first_upsert_commit_then_timeout_is_cleaned_up_once_the_file_is_gone(
    tmp_path: Path, qdrant: FakeQdrant
) -> None:
    doc = tmp_path / "docs" / "secret.md"
    doc.write_text("confidential runbook", encoding="utf-8")
    indexed: set[tuple[str, float]] = set()
    qdrant.commit_then_timeout = {1}

    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert indexed == set()
    assert qdrant.count(doc) == 1  # committed although the request "failed"
    assert knowledge_indexer.pending_reconcile_paths() == frozenset({str(doc)})  # was: tracked nowhere

    doc.unlink()
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)  # was (0, 0) with the point left searchable
    assert qdrant.points == {}
    assert knowledge_indexer.pending_reconcile_paths() == frozenset()
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)


def test_first_upsert_cleanup_is_retried_until_qdrant_confirms_it(tmp_path: Path, qdrant: FakeQdrant) -> None:
    doc = tmp_path / "docs" / "secret.md"
    doc.write_text("confidential runbook", encoding="utf-8")
    indexed: set[tuple[str, float]] = set()
    qdrant.commit_then_timeout = {1}
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    doc.unlink()
    qdrant.fail_delete = True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert qdrant.count(doc) == 1
    assert knowledge_indexer.pending_reconcile_paths() == frozenset({str(doc)})
    qdrant.fail_delete = False
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert qdrant.points == {}


def test_first_upsert_failure_is_resolved_by_the_next_successful_index(tmp_path: Path, qdrant: FakeQdrant) -> None:
    doc = tmp_path / "docs" / "runbook.md"
    doc.write_text("runbook", encoding="utf-8")
    indexed: set[tuple[str, float]] = set()
    qdrant.commit_then_timeout = {1}
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert qdrant.count(doc) == 1
    assert knowledge_indexer.pending_reconcile_paths() == frozenset()
    assert {path for path, _ in indexed} == {str(doc)}


def test_refused_first_upsert_keeps_the_previous_version_and_tracks_the_document(
    tmp_path: Path, qdrant: FakeQdrant
) -> None:
    doc = tmp_path / "docs" / "runbook.md"
    doc.write_text(" ".join(f"w{i}" for i in range(2000)), encoding="utf-8")
    assert knowledge_indexer.index_file(str(doc)) is True
    stored, deletes = qdrant.count(doc), qdrant.deletes
    assert stored > 1
    qdrant.refuse = {qdrant.upserts + 1}
    doc.write_text("short", encoding="utf-8")
    assert knowledge_indexer.index_file(str(doc)) is False
    # Nothing deleted: the previous version stays searchable; the path is tracked all the same.
    assert qdrant.count(doc) == stored and qdrant.deletes == deletes
    assert knowledge_indexer.pending_reconcile_paths() == frozenset({str(doc)})


def test_knowledge_indexer_mirror_carries_the_fix() -> None:
    src = (ROOT / "src/sovereign_dc/agents/knowledge_indexer.py").read_bytes()
    assert (ROOT / "software/agents/knowledge_indexer.py").read_bytes() == src


# ── 4. Traefik basic-auth hash in .env ────────────────────────────────────────────────────


def test_env_example_auth_instructions_and_compose_gate() -> None:
    """The documented form (every ``$`` doubled, value unquoted) is the one the gate renders.

    ``docker compose config`` prints each literal ``$`` as ``$$``; the container label has
    single dollars. ``scripts/validate_compose.sh`` renders a known hash through the
    documented transformation and compares the un-escaped label with the original.
    """
    env = (ROOT / "software" / "env.example").read_text(encoding="utf-8")
    assert "sed -e 's/\\$/\\$\\$/g'" in env
    value = re.search(r"^TRAEFIK_DASHBOARD_AUTH=(.*)$", env, re.MULTILINE)
    assert value is not None
    placeholder = value.group(1)
    assert not placeholder.startswith(("'", '"'))  # unquoted: Compose collapses $$ to $
    assert re.fullmatch(r"[^:$]+:\$\$apr1\$\$[^$]+\$\$[^$]+", placeholder)
    assert "replace_this" in placeholder  # still caught by setup.sh's placeholder gate

    gate = (ROOT / "scripts" / "validate_compose.sh").read_text(encoding="utf-8")
    assert "AUTH_TEST_HASH='admin:$apr1$" in gate
    assert '.endswith(".basicauth.users")' in gate
    assert 'value.replace("$$", "$")' in gate

    compose = (ROOT / "software" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "basicauth.users=${TRAEFIK_DASHBOARD_AUTH:-" in compose


# ── 7. MCP spool_dtn_bundle: the schema accepts what the handler accepts ──────────────────

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def _schema_errors(schema: dict[str, Any], value: Any) -> list[str]:
    """Minimal JSON-Schema check: type, required, properties, enum, anyOf / oneOf."""
    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        types = _JSON_TYPES[expected]
        if not isinstance(value, types) or (isinstance(value, bool) and expected != "boolean"):
            return [f"expected {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{value!r} not in enum")
    if isinstance(value, dict):
        errors += [f"missing required '{key}'" for key in schema.get("required", []) if key not in value]
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += [f"{key}: {e}" for e in _schema_errors(sub, value[key])]
    for combinator in ("anyOf", "oneOf"):
        if combinator in schema:
            matches = sum(1 for sub in schema[combinator] if not _schema_errors(sub, value))
            if matches == 0 or (combinator == "oneOf" and matches != 1):
                errors.append(f"{combinator} not satisfied")
    return errors


def _dtn_tool() -> Any:
    return next(t for t in mcp_tools.get_mcp_tools() if t.name == "spool_dtn_bundle")


def test_validator_itself_detects_a_missing_required_field() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    assert _schema_errors(schema, {}) == ["missing required 'a'"]
    assert _schema_errors(schema, {"a": 1}) == ["a: expected string"]
    assert _schema_errors(schema, {"a": "x"}) == []


def test_binary_only_dtn_request_validates_against_the_advertised_schema_and_is_spooled() -> None:
    schema = _dtn_tool().input_schema
    request = {"destination": "dtn://ground.earth/t", "payload_b64": "AP8="}
    assert _schema_errors(schema, request) == []  # was: missing required 'payload'
    result = mcp_tools.tool_spool_dtn_bundle(request)
    assert result["success"] is True
    assert result["payload_bytes"] == 2
    import hashlib

    assert result["payload_sha256"] == hashlib.sha256(b"\x00\xff").hexdigest()

    text_request = {"destination": "dtn://ground.earth/t", "payload": "hello"}
    assert _schema_errors(schema, text_request) == []
    assert mcp_tools.tool_spool_dtn_bundle(text_request)["payload_bytes"] == 5


def test_dtn_request_without_any_payload_is_rejected_with_a_clear_error() -> None:
    tool = _dtn_tool()
    assert tool.input_schema["required"] == ["destination"]
    # The schema uses no top-level combinator (several MCP clients reject those), so the
    # descriptions state the rule and the handler enforces it.
    assert not {"anyOf", "oneOf", "allOf"} & set(tool.input_schema)
    for field in ("payload", "payload_b64"):
        assert (
            "One of 'payload' or 'payload_b64' must be given" in tool.input_schema["properties"][field]["description"]
        )
    result = mcp_tools.tool_spool_dtn_bundle({"destination": "dtn://ground.earth/t"})
    assert result["success"] is False and result["status"] == "REJECTED"  # was: spooled a default payload
    assert "'payload'" in result["error"] and "'payload_b64'" in result["error"]


# ── 8. Every `smdc ...` command named in the documents exists ─────────────────────────────

_DOCS_WITH_COMMANDS = ("ENTERPRISE_ONBOARDING.md", "README.md", "AGENTS.md")
_COMMAND_RE = re.compile(r"(?<![\w/.-])smdc((?: +[a-z][a-z0-9-]*){1,2})")


def _cli_tree() -> dict[str, set[str]]:
    """First-level commands of the real parser, each with its sub-commands (empty if none)."""
    captured: list[argparse.ArgumentParser] = []
    real_parse = argparse.ArgumentParser.parse_args

    def capture(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
        captured.append(self)
        raise SystemExit(0)

    with patch.object(argparse.ArgumentParser, "parse_args", capture), pytest.raises(SystemExit):
        cli.main()
    assert real_parse is not capture and captured
    tree: dict[str, set[str]] = {}
    for action in captured[0]._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                tree[name] = set()
                for sub_action in sub._actions:
                    if isinstance(sub_action, argparse._SubParsersAction):
                        tree[name] |= set(sub_action.choices)
    return tree


def test_every_smdc_command_in_the_documents_is_registered() -> None:
    tree = _cli_tree()
    assert {"init", "start", "stop", "package"} <= tree["app"]
    phantoms: list[str] = []
    checked = 0
    for name in _DOCS_WITH_COMMANDS:
        for number, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
            for match in _COMMAND_RE.finditer(line):
                words = match.group(1).split()
                checked += 1
                if words[0] not in tree:
                    phantoms.append(f"{name}:{number}: smdc {words[0]}")
                elif len(words) > 1 and tree[words[0]] and words[1] not in tree[words[0]]:
                    phantoms.append(f"{name}:{number}: smdc {' '.join(words)}")
    assert checked > 50
    assert phantoms == []  # was: ENTERPRISE_ONBOARDING.md named `smdc app pause`


def test_guide_names_the_real_entry_point_for_a_manual_pause() -> None:
    tree = _cli_tree()
    assert "pause" not in tree["app"] and "resume" not in tree["app"]
    guide = (ROOT / "ENTERPRISE_ONBOARDING.md").read_text(encoding="utf-8")
    line = next(ln for ln in guide.splitlines() if "manual pause stays in force" in ln)
    assert "manage_enterprise_app" in line and "pause_app()" in line
    assert "has no `app pause` command" in line
    manage = next(t for t in mcp_tools.get_mcp_tools() if t.name == "manage_enterprise_app")
    assert {"pause", "resume"} <= set(manage.input_schema["properties"]["action"]["enum"])


# ── 9. One pricing rule in code and documents ─────────────────────────────────────────────


def _pricing_line(name: str, marker: str) -> str:
    lines = [ln for ln in (ROOT / name).read_text(encoding="utf-8").splitlines() if marker in ln]
    assert len(lines) == 1, f"{name}: expected exactly one line containing {marker!r}"
    return lines[0]


@pytest.mark.parametrize(
    ("name", "marker"),
    [("README.md", "Solar-Aware Dynamic Price Oracle"), ("AGENTS.md", "Dynamic Solar-Aware Pricing Engine")],
)
def test_documented_pricing_rule_matches_the_code(name: str, marker: str) -> None:
    line = _pricing_line(name, marker)

    def number(pattern: str) -> float:
        found = re.search(pattern, line)
        assert found is not None, f"{name}: {pattern!r} not found in the pricing line"
        return float(found.group(1).replace(",", ""))

    assert number(r"(\d+)% discount") == (1 - market.SOLAR_SURPLUS_MULTIPLIER) * 100
    assert number(r"solar harvest is at least ([\d,]+) W") == market.SOLAR_SURPLUS_MIN_SOLAR_WATTS
    assert number(r"battery SoC at least (\d+)%") == market.SOLAR_SURPLUS_MIN_SOC_PERCENT
    assert number(r"nominal pricing down to (\d+)% SoC") == market.NOMINAL_MIN_SOC_PERCENT
    assert number(r"([\d.]+)x surge below 40% SoC") == market.MODERATE_SURGE_MULTIPLIER
    assert number(r"surge below (\d+)% SoC and") == market.NOMINAL_MIN_SOC_PERCENT
    assert number(r"([\d.]+)x surge below 25% SoC") == market.CRITICAL_SURGE_MULTIPLIER
    assert number(r"x surge below (\d+)% SoC\b(?!.* and a)") == market.MODERATE_SURGE_MIN_SOC_PERCENT


def test_pricing_thresholds_are_the_named_constants_and_inclusive() -> None:
    soc, watts = market.SOLAR_SURPLUS_MIN_SOC_PERCENT, market.SOLAR_SURPLUS_MIN_SOLAR_WATTS
    assert (soc, watts) == (75.0, 500.0)
    multiplier = ComputeMarket.get_dynamic_multiplier
    assert multiplier(soc, watts) == (0.5, "SOLAR_SURPLUS_DISCOUNT_50%")
    assert multiplier(soc, watts - 0.1)[0] == 1.0
    assert multiplier(soc - 0.1, watts)[0] == 1.0
    assert multiplier(market.NOMINAL_MIN_SOC_PERCENT, 0.0)[0] == market.NOMINAL_MULTIPLIER == 1.0
    assert multiplier(market.NOMINAL_MIN_SOC_PERCENT - 0.1, 5000.0)[0] == market.MODERATE_SURGE_MULTIPLIER == 1.5
    assert multiplier(market.MODERATE_SURGE_MIN_SOC_PERCENT, 0.0)[0] == 1.5
    assert multiplier(market.MODERATE_SURGE_MIN_SOC_PERCENT - 0.1, 0.0) == (3.0, "CRITICAL_BATTERY_SURGE_300%")
    with patch.object(market, "SOLAR_SURPLUS_MIN_SOLAR_WATTS", 1000.0):
        assert multiplier(90.0, 800.0)[0] == 1.0  # the function reads the constant


def test_browser_demo_quote_uses_the_code_thresholds_and_catalog_prices() -> None:
    demo = (ROOT / "docs" / "js" / "calculator.js").read_text(encoding="utf-8")
    assert f"at or above the {market.SOLAR_SURPLUS_MIN_SOLAR_WATTS:.0f} W surplus threshold" in demo
    assert f"at or above the {market.SOLAR_SURPLUS_MIN_SOC_PERCENT:.0f}% surplus threshold" in demo
    quote = ComputeMarket().calculate_quote(ServiceType.LLM_INFERENCE, 1.0, battery_soc=88.5, solar_power_w=2660.0)
    relay = ComputeMarket().calculate_quote(ServiceType.SPACE_DTN_RELAY, 1.0, battery_soc=88.5, solar_power_w=2660.0)
    assert f"LLM Inference:  {quote.final_unit_price:.4f} credits / 1k tokens" in demo  # was 0.0025
    assert f"Space DTN Relay:{relay.final_unit_price:.4f} credits / MB" in demo  # was 0.0050
    assert BASE_SERVICE_PRICING[ServiceType.LLM_INFERENCE][0] == 0.05


def test_package_signing_refuses_an_ephemeral_node_key(tmp_path, monkeypatch):
    """With no usable persistent node key, packaging fails instead of signing with a throwaway key."""
    from sovereign_dc.enterprise.manager import EnterpriseManager
    from sovereign_dc.enterprise.registry import EnterpriseRegistry
    from sovereign_dc.security import pqc

    app_dir = tmp_path / "app"
    EnterpriseRegistry.create_project_scaffold(app_dir, _manifest())
    blocked = tmp_path / "state-is-a-file"
    blocked.write_text("not a directory")
    monkeypatch.setenv("SMDC_STATE_DIR", str(blocked))
    monkeypatch.setattr(pqc, "_node_state_dir", lambda: str(blocked))
    pqc._CACHED_NODE_KEYPAIRS.clear()
    pqc._EPHEMERAL_NODE_KEYPAIRS.clear()
    try:
        ok, msg, _meta = EnterpriseManager().package_app(app_dir, output_path=tmp_path / "out.smdc-app", sign_pqc=True)
        assert ok is False
        assert "ephemeral" in msg or "persistent" in msg
        assert not (tmp_path / "out.smdc-app").exists()
        # The non-publishing path may still fall back, and a later strict call must not reuse that key.
        assert pqc.get_node_pqc_keypair(pqc.PQCAlgorithm.ML_DSA_87) is not None
        with pytest.raises(RuntimeError):
            pqc.get_node_pqc_keypair(pqc.PQCAlgorithm.ML_DSA_87, require_persistent=True)
    finally:
        pqc._CACHED_NODE_KEYPAIRS.clear()
        pqc._EPHEMERAL_NODE_KEYPAIRS.clear()


def test_force_backups_are_not_packaged():
    from sovereign_dc.enterprise.manager import EnterpriseManager

    assert EnterpriseManager._is_excluded_name("app.py.bak-20261115T080000Z")
    assert not EnterpriseManager._is_excluded_name("app.py")
