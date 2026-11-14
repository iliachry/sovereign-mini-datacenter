"""Regression tests for the follow-up fixes: state paths, indexer filtering, truthful status."""

from __future__ import annotations

import io
import json
import logging
import os
import stat
import subprocess
import sys
import urllib.error
from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sovereign_dc import cli
from sovereign_dc import config as smdc_config
from sovereign_dc.agents import knowledge_indexer, sentinel_copilot
from sovereign_dc.agents.bootstrap_provisioner import BootstrapProvisioner
from sovereign_dc.agents.technician_notifier import (
    FileNotifier,
    MessageSeverity,
    TechnicianMessage,
    TechnicianNotifierChain,
)
from sovereign_dc.enterprise import sdk as sdk_module
from sovereign_dc.enterprise.sdk import SMDCClient
from sovereign_dc.events import get_event_bus
from sovereign_dc.hal.power import PowerReading
from sovereign_dc.hal.thermal import ThermalReading
from sovereign_dc.mcp import resources as mcp_resources
from sovereign_dc.mcp import tools as mcp_tools
from sovereign_dc.mesh.chaos import MeshChaosSimulator
from sovereign_dc.mesh.consensus import AppendEntriesReply, NodeRole, RaftCluster, RaftNode, RequestVoteReply
from sovereign_dc.mesh.lora import meshtastic_gateway
from sovereign_dc.security.pqc import PQCSigner
from sovereign_dc.space.dtn.router import DTNRouter
from sovereign_dc.web import dashboard

ROOT = Path(__file__).resolve().parent.parent


def _resp(body: Any, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


def _state_dir_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No explicit path overrides: every default must resolve inside this state directory."""
    state_dir = tmp_path / "state"
    for name in ("SOVEREIGN_MODE_FILE", "SOVEREIGN_LOG_DIR", "LOG_DIR", "DTN_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    smdc_config.reset_config()
    return state_dir


# --- A. State directory / no shared-tmp defaults ---------------------------------------------


def test_mcp_state_dir_is_the_config_state_dir() -> None:
    assert mcp_tools._get_mcp_state_dir() == smdc_config.get_state_dir()
    # The duplicate implementation is gone: there is one set of rules, in sovereign_dc.config.
    for name in ("_is_private_dir", "_FALLBACK_STATE_DIR", "tempfile"):
        assert not hasattr(mcp_tools, name), name


def test_bootstrap_spool_default_is_the_private_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = _state_dir_only(monkeypatch, tmp_path)
    provisioner = BootstrapProvisioner(
        node_id="n1", notifier_chain=TechnicianNotifierChain(notifiers=[], node_id="n1"), dry_run=True
    )
    counted: list[str] = []
    with (
        patch.object(provisioner, "_count_spool_bundles", side_effect=lambda path: counted.append(path) or 0),
        patch("sovereign_dc.mesh.mesh_sync.load_peers", return_value=[]),
    ):
        provisioner.phase_4_sync()
        net = provisioner.phase_2_network()
    assert counted == [str(state_dir / "dtn_spool.db")]
    assert net["tier4_space_dtn"] is True  # the state directory exists and is writable

    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "explicit" / "spool.db"))
    with patch.object(provisioner, "_count_spool_bundles", side_effect=lambda path: counted.append(path) or 0):
        provisioner.phase_4_sync()
    assert counted[-1] == str(tmp_path / "explicit" / "spool.db")


def test_lora_spool_default_is_never_shared_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = _state_dir_only(monkeypatch, tmp_path)
    assert meshtastic_gateway.resolve_dtn_spool_path() == str(state_dir / "dtn_spool.db")

    # Standalone (LoRa container: only this file is mounted, sovereign_dc is not importable).
    monkeypatch.setitem(sys.modules, "sovereign_dc.space.dtn.router", None)
    standalone = meshtastic_gateway.resolve_dtn_spool_path()
    assert standalone == str(state_dir / "dtn_spool.db")
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700

    # An unusable state directory falls back to a private mkdtemp directory, not /tmp/dtn_spool.db.
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("SMDC_STATE_DIR", str(blocker / "state"))
    monkeypatch.setattr(meshtastic_gateway, "_fallback_state_dir", None)
    monkeypatch.setattr(meshtastic_gateway.tempfile, "tempdir", str(tmp_path))
    fallback = Path(meshtastic_gateway.resolve_dtn_spool_path())
    assert fallback.name == "dtn_spool.db" and fallback.parent.parent == tmp_path
    assert stat.S_IMODE(fallback.parent.stat().st_mode) == 0o700
    assert meshtastic_gateway.resolve_dtn_spool_path() == str(fallback)  # stable within the process

    # The environment variable stays authoritative.
    monkeypatch.setenv("DTN_DB_PATH", "/data/dtn/spool.db")
    assert meshtastic_gateway.resolve_dtn_spool_path() == "/data/dtn/spool.db"


def test_alert_log_is_created_owner_only(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "technician_notifications.jsonl"
    message = TechnicianMessage(node_id="n1", event_type="T", severity=MessageSeverity.INFO, message="m")
    old_umask = os.umask(0o022)  # the usual default, which used to yield 0644
    try:
        assert FileNotifier(str(log_file)).send(message) is True
        assert FileNotifier(str(log_file)).send(message) is True
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    assert len(log_file.read_text(encoding="utf-8").splitlines()) == 2  # appended, not truncated


# --- B. Knowledge indexer ---------------------------------------------------------------------


class FakeQdrant:
    """In-memory stand-in for the Qdrant REST calls the indexer makes."""

    def __init__(self) -> None:
        self.points: dict[Any, dict[str, Any]] = {}
        self.upsert_sizes: list[int] = []

    def __call__(self, req: Any, timeout: float = 0) -> MagicMock:
        url, method = req.full_url, req.get_method()
        body = json.loads(req.data) if req.data else {}
        if "/points/delete" in url:
            keep = {i for cond in body["filter"].get("must_not", []) for i in cond.get("has_id", [])}
            path = body["filter"]["must"][0]["match"]["value"]
            self.points = {
                k: v for k, v in self.points.items() if not (v["payload"]["path"] == path and v["id"] not in keep)
            }
        elif "/points/scroll" in url:
            return _resp(
                {
                    "result": {
                        "points": [{"id": p["id"], "payload": p["payload"]} for p in self.points.values()],
                        "next_page_offset": None,
                    }
                }
            )
        elif method == "PUT" and "/points" in url:
            self.upsert_sizes.append(len(body["points"]))
            for point in body["points"]:
                self.points[point["id"]] = point
        return _resp({"result": {"status": "completed"}})

    def paths(self) -> set[str]:
        return {p["payload"]["path"] for p in self.points.values()}


@pytest.fixture
def qdrant() -> Any:
    fake = FakeQdrant()
    with (
        patch.object(knowledge_indexer, "get_embedding", return_value=[0.1] * knowledge_indexer.EMBEDDING_DIM),
        patch("urllib.request.urlopen", side_effect=fake),
    ):
        yield fake


def _nextcloud_tree(root: Path) -> dict[str, Path]:
    files = {
        "user": root / "alice" / "files" / "notes.md",
        "user_nested": root / "alice" / "files" / "projects" / "plan.txt",
        "trash": root / "alice" / "files_trashbin" / "files" / "deleted.md",
        "versions": root / "alice" / "files_versions" / "notes.md",
        "cache": root / "alice" / "cache" / "tmp.md",
        "uploads": root / "alice" / "uploads" / "partial.txt",
        "appdata": root / "appdata_oc1a2b3c" / "preview" / "meta.json",
        "hidden": root / ".ocdata_hidden" / "secret.md",
        "top": root / "nextcloud_readme.md",
    }
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content of {path.name}", encoding="utf-8")
    return files


def test_default_excludes_prune_nextcloud_internals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = _nextcloud_tree(tmp_path / "data")
    root = str(tmp_path / "data")
    assert knowledge_indexer.EXCLUDE_DIRS == knowledge_indexer._split_patterns(knowledge_indexer.DEFAULT_EXCLUDE_DIRS)
    assert knowledge_indexer.INCLUDE_GLOBS == ()  # default: everything under the watch directory

    listed = knowledge_indexer.list_documents(root)
    assert listed == sorted(str(files[k]) for k in ("user", "user_nested", "top"))

    # The deployment's glob keeps user files only.
    monkeypatch.setattr(knowledge_indexer, "INCLUDE_GLOBS", ("*/files/**",))
    assert knowledge_indexer.list_documents(root) == sorted(str(files[k]) for k in ("user", "user_nested"))

    # Excluded directories are pruned, not merely filtered: os.walk never lists inside them.
    visited: list[str] = []
    real_walk = os.walk

    def recording_walk(top: str, **kwargs: Any) -> Any:
        for entry in real_walk(top, **kwargs):
            visited.append(entry[0])
            yield entry

    with patch("os.walk", side_effect=recording_walk):
        knowledge_indexer.list_documents(root)
    assert not [d for d in visited if "files_trashbin" in d or "appdata_" in d or ".ocdata_hidden" in d]


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("*/files/**", "alice/files/a.md", True),
        ("*/files/**", "alice/files/deep/er/a.md", True),
        ("*/files/**", "alice/files_versions/a.md", False),
        ("*/files/**", "a/b/files/a.md", False),  # "*" stays within one path segment
        ("*/files/**", "files/a.md", False),
        ("**/*.md", "a/b/c.md", True),
        ("**/*.md", "c.md", True),
        ("docs/?.txt", "docs/a.txt", True),
        ("docs/?.txt", "docs/ab.txt", False),
        ("a.b/*", "aXb/c", False),  # regex metacharacters are literal
    ],
)
def test_include_glob_semantics(monkeypatch: pytest.MonkeyPatch, pattern: str, path: str, expected: bool) -> None:
    monkeypatch.setattr(knowledge_indexer, "INCLUDE_GLOBS", (pattern,))
    assert knowledge_indexer.is_included_path(path) is expected


def test_newly_excluded_documents_are_removed_from_the_index(
    tmp_path: Path, qdrant: FakeQdrant, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _nextcloud_tree(tmp_path / "data")
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(tmp_path / "data"))
    # An earlier release indexed the whole data directory.
    monkeypatch.setattr(knowledge_indexer, "EXCLUDE_DIRS", ())
    indexed: set[tuple[str, float]] = set()
    assert knowledge_indexer.scan_and_index(indexed) == (len(files), 0)
    assert qdrant.paths() == {str(p) for p in files.values()}

    # Restarted with the new defaults and the deployment glob: a fresh worker seeds from Qdrant.
    monkeypatch.setattr(
        knowledge_indexer, "EXCLUDE_DIRS", knowledge_indexer._split_patterns(knowledge_indexer.DEFAULT_EXCLUDE_DIRS)
    )
    monkeypatch.setattr(knowledge_indexer, "INCLUDE_GLOBS", ("*/files/**",))
    restarted: set[tuple[str, float]] = set()
    assert knowledge_indexer.seed_indexed_sources(restarted) is True
    succeeded, failed = knowledge_indexer.scan_and_index(restarted)
    assert failed == 0 and succeeded == len(files)  # 2 re-indexed + 7 removed
    assert qdrant.paths() == {str(files["user"]), str(files["user_nested"])}
    assert knowledge_indexer.scan_and_index(restarted) == (0, 0)


def test_compose_overlay_indexes_user_files_only() -> None:
    text = (ROOT / "software" / "agents" / "docker-compose.agents.yml").read_text(encoding="utf-8")
    assert "DOCS_INCLUDE_GLOB=${DOCS_INCLUDE_GLOB-*/files/**}" in text
    assert "DOCS_WATCH_DIR=${DOCS_WATCH_DIR:-/data/nextcloud/data}" in text


def test_worker_waits_for_a_missing_watch_dir_without_creating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    watch = tmp_path / "nextcloud" / "data"  # created by Nextcloud on its first start
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(tmp_path / "no_mode_file"))
    polls = {"n": 0}

    def sleep(_seconds: float) -> None:
        polls["n"] += 1
        if polls["n"] == 3:
            watch.mkdir(parents=True)
            (watch / "a.md").write_text("now there", encoding="utf-8")
        if polls["n"] == 4:
            raise KeyboardInterrupt

    with (
        patch.object(knowledge_indexer, "WATCH_DIR", str(watch)),
        patch.object(knowledge_indexer, "ensure_qdrant_collection", return_value=True),
        patch.object(knowledge_indexer, "seed_indexed_sources", return_value=True),
        patch.object(knowledge_indexer, "index_file", return_value=True) as index,
        patch("os.makedirs", side_effect=AssertionError("the watch directory must not be created")),
        patch("time.sleep", side_effect=sleep),
        pytest.raises(KeyboardInterrupt),
    ):
        knowledge_indexer.run_worker()

    assert caplog.text.count("does not exist yet") == 1  # logged once, not on every poll
    assert "is now available" in caplog.text
    index.assert_called_once_with(str(watch / "a.md"))


def test_upserts_are_batched(tmp_path: Path, qdrant: FakeQdrant, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = tmp_path / "big.md"
    doc.write_text(" ".join(f"w{i}" for i in range(450 * 6 + 10)), encoding="utf-8")  # 7 chunks
    monkeypatch.setattr(knowledge_indexer, "UPSERT_BATCH_SIZE", 3)
    assert knowledge_indexer.index_file(str(doc)) is True
    assert qdrant.upsert_sizes == [3, 3, 1]
    assert len(qdrant.points) == 7
    assert knowledge_indexer.UPSERT_BATCH_SIZE == 3  # default (64) restored by monkeypatch afterwards


def test_oversized_files_are_skipped_with_one_warning(
    tmp_path: Path, qdrant: FakeQdrant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    docs = tmp_path / "docs"
    docs.mkdir()
    small, big = docs / "small.md", docs / "big.md"
    small.write_text("ok", encoding="utf-8")
    big.write_text("ok too", encoding="utf-8")
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "MAX_FILE_BYTES", 64)
    monkeypatch.setattr(knowledge_indexer, "_oversize_warned", set())
    indexed: set[tuple[str, float]] = set()
    assert knowledge_indexer.scan_and_index(indexed) == (2, 0)

    big.write_text("x" * 65, encoding="utf-8")  # grew past the limit
    # Not a failure (a failure would make the worker re-verify the collection forever);
    # the stale version is withdrawn from the index.
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert qdrant.paths() == {str(small)}
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)
    assert knowledge_indexer.index_file(str(big)) is False  # also refused when called directly
    assert caplog.text.count("exceeds DOCS_MAX_FILE_BYTES") == 1

    big.write_text("small again", encoding="utf-8")
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert qdrant.paths() == {str(small), str(big)}


def test_default_limits() -> None:
    assert knowledge_indexer.UPSERT_BATCH_SIZE == 64
    assert knowledge_indexer.MAX_FILE_BYTES == 5 * 1024 * 1024


@pytest.mark.parametrize("module", ["knowledge_indexer", "sentinel_copilot"])
def test_importing_an_agent_does_not_configure_root_logging(module: str) -> None:
    code = (
        "import logging\n"
        f"import sovereign_dc.agents.{module} as agent\n"
        "agent.logger.warning('library use')\n"
        "root = logging.getLogger()\n"
        "assert root.handlers == [], root.handlers\n"
        "assert root.level == logging.WARNING, root.level\n"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", [knowledge_indexer, sentinel_copilot])
def test_agents_log_through_their_own_logger_and_configure_only_as_scripts(module: Any) -> None:
    import ast

    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    root_calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logging"
        and node.func.attr in {"debug", "info", "warning", "error", "critical", "exception", "basicConfig"}
    ]
    main_guard = next(node for node in tree.body if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test))
    # The only logging.* call is basicConfig inside the script entry point, with the old format.
    assert root_calls == [main_guard.body[0].lineno]
    assert ast.unparse(main_guard.body[0]) == "logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)"
    assert module.LOG_FORMAT.startswith("%(asctime)s [") and module.LOG_FORMAT.endswith("] %(message)s")


# --- D. Truthful status ------------------------------------------------------------------------


@pytest.mark.parametrize(("soc", "level"), [(75.0, "L0"), (45.0, "L1"), (25.0, "L2"), (15.0, "L3"), (5.0, "L4")])
def test_mcp_recommended_level_uses_the_configured_tiers(
    monkeypatch: pytest.MonkeyPatch, soc: float, level: str
) -> None:
    power = PowerReading(battery_soc=soc, solar_watts=100.0, is_simulated=True)
    thermal = ThermalReading(coolant_celsius=30.0, rack_inlet_celsius=24.0, rack_exhaust_celsius=33.0)
    monkeypatch.setattr(mcp_tools, "read_power", lambda **_k: power)
    monkeypatch.setattr(mcp_tools, "read_thermal", lambda **_k: thermal)
    status = mcp_tools.tool_get_system_status({})
    assert status["load_shedding"]["recommended_level"] == level
    assert level == f"L{smdc_config.get_config().get_shedding_level(soc)}"


def test_dashboard_pqc_status_is_derived_from_a_real_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard, "_PQC_SELF_TEST_PASSED", set())
    ok = dashboard.get_pqc_status()
    assert ok["status"] == "SELF_TEST_PASSED" and ok["self_test"] == "passed"
    assert ok["key_id"].startswith("pqc-ml-dsa-65-")
    assert dashboard.get_system_status_payload()["pqc"] == ok

    # Cached per key: the round-trip is not repeated on every status build.
    with patch.object(PQCSigner, "sign", side_effect=AssertionError("self-test must be cached")):
        assert dashboard.get_pqc_status()["status"] == "SELF_TEST_PASSED"

    # A key whose signatures do not verify is not reported as passing.
    monkeypatch.setattr(dashboard, "_PQC_SELF_TEST_PASSED", set())
    with patch.object(PQCSigner, "verify", return_value=False):
        failed = dashboard.get_pqc_status()
    assert failed["status"] == "KEY_LOADED" and failed["self_test"] == "failed"
    with patch.object(PQCSigner, "sign", side_effect=ValueError("bad key")):
        assert dashboard.get_pqc_status()["status"] == "KEY_LOADED"
    assert dashboard._PQC_SELF_TEST_PASSED == set()  # failures are never cached as passes

    with patch.object(dashboard, "get_node_pqc_keypair", side_effect=OSError("no key")):
        unavailable = dashboard.get_pqc_status()
    assert unavailable == {"algorithm": "ML-DSA-65", "key_id": None, "status": "UNAVAILABLE", "self_test": "not_run"}

    html = dashboard.DASHBOARD_HTML
    assert "OPERATIONAL" not in html
    assert "data.pqc.status === 'SELF_TEST_PASSED'" in html


def test_mcp_pqc_resource_reports_only_what_is_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_resources, "_PQC_SELF_TEST_PASSED", set())
    data = json.loads(mcp_resources.read_pqc_status_resource())
    assert data["status"] == "SELF_TEST_PASSED"
    assert data["self_tests"] == {
        "ML-DSA-65": "PASSED",
        "ML-DSA-87": "PASSED",
        "ML-KEM-768": "PASSED",
        "ML-KEM-1024": "PASSED",
    }
    text = json.dumps(data)
    assert "ACTIVE_ENFORCED" not in text
    assert data["compliance_standards"]["NIST SP 800-207 Zero Trust"] == "not assessed"
    assert all(value.startswith("not assessed") for value in data["compliance_standards"].values())
    assert data["enforcement"] == "not assessed"

    monkeypatch.setattr(mcp_resources, "_PQC_SELF_TEST_PASSED", set())
    with patch.object(PQCSigner, "verify", return_value=False):
        broken = json.loads(mcp_resources.read_pqc_status_resource())
    assert broken["status"] == "SELF_TEST_FAILED"
    assert broken["self_tests"]["ML-DSA-87"] == "FAILED" and broken["self_tests"]["ML-KEM-1024"] == "PASSED"


def test_dashboard_5g_rows_come_from_the_slicing_simulator() -> None:
    html = dashboard.DASHBOARD_HTML
    markup, script = html.split("<script>")[0], html.split("<script>")[1]
    for static_claim in ("0.80 ms (99.999%)", "127 Mbps (15 ms)", "12,000+ nodes/km²"):
        assert static_claim not in html, static_claim
    for element_id in ("slice-urllc", "slice-embb", "slice-mmtc"):
        assert f'id="{element_id}">--<' in markup  # neutral until the payload arrives
        assert f"'{element_id}'" in script
    assert markup.count("Slice (simulated)") == 3
    assert "data.metaverse.slices" in script

    slices = dashboard.get_system_status_payload()["metaverse"]["slices"]
    assert set(slices) == {"URLLC", "eMBB", "mMTC"}
    for name in slices:
        assert {"avg_latency_ms", "target_latency_ms", "allocated_bandwidth_mbps", "active_connections"} <= set(
            slices[name]
        )


def test_slices_endpoint_reports_the_orchestrators_traffic() -> None:
    orch = dashboard.get_metaverse_orchestrator()
    orch.slicing.transmit_xr_frame(frame_size_bytes=1_048_576)
    handler = dashboard.DashboardHandler.__new__(dashboard.DashboardHandler)
    handler.path = "/api/metaverse/slices"
    handler.headers = {}
    handler.client_address = ("127.0.0.1", 0)
    handler.wfile = io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.do_GET()
    served = json.loads(handler.wfile.getvalue())
    assert served == orch.slicing.get_summary()
    assert served["eMBB"]["total_packets"] == 1  # a fresh manager would always say 0


def test_agent_index_counts_real_outcomes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("a.md", "b.md", "c.txt"):
        (docs / name).write_text("text", encoding="utf-8")
    (docs / "ignored.bin").write_text("x", encoding="utf-8")
    args = Namespace(directory=str(docs))

    with (
        patch.object(knowledge_indexer, "ensure_qdrant_collection", return_value=True),
        patch.object(knowledge_indexer, "process_file", side_effect=[True, False, True]),
    ):
        assert cli.cmd_agent_index(args) == 1
    out = capsys.readouterr().out
    assert "Indexed 2 document(s); 1 failed" in out and "Successfully" not in out
    assert str(docs / "b.md") in out

    with (
        patch.object(knowledge_indexer, "ensure_qdrant_collection", return_value=True),
        patch.object(knowledge_indexer, "process_file", return_value=True),
        patch.object(knowledge_indexer, "MAX_FILE_BYTES", 4),
        patch.object(knowledge_indexer, "_oversize_warned", set()),
    ):
        (docs / "c.txt").write_text("larger than four bytes", encoding="utf-8")
        assert cli.cmd_agent_index(args) == 0
    out = capsys.readouterr().out
    assert "Successfully indexed 2 document(s)" in out and "1 skipped as too large" in out


def _http_error(code: int, body: Any) -> urllib.error.HTTPError:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    return urllib.error.HTTPError("http://node.test/x", code, "Refused", {}, io.BytesIO(raw))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("code", "body", "message"),
    [
        (
            400,
            {"success": False, "error": "'priority' must be 0 (bulk), 1 (normal), 2 (expedited) or 3 (critical)"},
            "priority",
        ),
        (401, {"success": False, "error": "Unauthorized"}, "Unauthorized"),
        (403, b"<html>forbidden</html>", "Refused"),
        (500, {"error": "boom"}, "boom"),
    ],
)
def test_sdk_does_not_turn_a_remote_rejection_into_a_local_write(
    monkeypatch: pytest.MonkeyPatch, code: int, body: Any, message: str
) -> None:
    def rejecting_urlopen(_req: Any, timeout: float = 0) -> Any:
        raise _http_error(code, body)

    monkeypatch.setattr(sdk_module, "urlopen", rejecting_urlopen)
    received: list[Any] = []
    get_event_bus().subscribe("enterprise.*", received.append)
    client = SMDCClient(base_url="http://node.test")

    res = client.send_dtn_bundle("dtn://ground.earth/data", b"payload")
    assert res["status"] == "rejected" and res["success"] is False
    assert res["http_status"] == code and message in res["error"]
    spool = DTNRouter(db_path=smdc_config.get_config().dtn_db_path)
    assert spool.get_queue_stats()["queued_bundle_count"] == 0  # nothing was spooled locally

    assert client.emit_telemetry("app-1", {"m": 1}) is False
    assert received == []  # and no local event was published


def test_sdk_still_falls_back_when_the_node_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(_req: Any, timeout: float = 0) -> Any:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(sdk_module, "urlopen", unreachable)
    received: list[Any] = []
    get_event_bus().subscribe("enterprise.*", received.append)
    client = SMDCClient(base_url="http://node.test")

    assert client.send_dtn_bundle("dtn://ground.earth/data", b"payload")["status"] == "spooled"
    assert client.emit_telemetry("app-1", {"m": 1}) is True
    assert len(received) == 1
    assert client._http_post("/api/x", {}) == {}


def test_economy_send_rejects_sub_micro_amounts_clearly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from sovereign_dc.economy.ledger import Ledger
    from sovereign_dc.economy.wallet import NodeWallet

    paths = dict(wallet_file=str(tmp_path / "w.json"), db_path=str(tmp_path / "l.db"))
    cli.cmd_economy_wallet(Namespace(create=True, pqc=False, mint=10.0, node_id="n1", **paths))
    sender = NodeWallet.load_from_file(paths["wallet_file"])
    before = Ledger(db_path=paths["db_path"]).get_balance(sender.address)
    recipient = NodeWallet.create(node_id="peer").address
    capsys.readouterr()

    for bad in (0.0000001, 1.2345678, float("nan"), float("inf")):
        assert cli.cmd_economy_send(Namespace(recipient=recipient, amount=bad, memo="", **paths)) == 1
        out = capsys.readouterr().out
        assert "Invalid amount" in out and "at most 6 decimal places" in out
        assert "Traceback" not in out
    assert Ledger(db_path=paths["db_path"]).get_balance(sender.address) == before

    assert cli.cmd_economy_send(Namespace(recipient=recipient, amount=0.000001, memo="", **paths)) == 0
    out = capsys.readouterr().out
    assert "Transfer of 0.000001 credits completed" in out  # not rounded to "0.00"
    assert cli._format_credits(25.0) == "25.00" and cli._format_credits(1.5) == "1.50"


def test_economy_send_cli_exit_status_for_bad_amount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = dict(wallet_file=str(tmp_path / "w.json"), db_path=str(tmp_path / "l.db"))
    cli.cmd_economy_wallet(Namespace(create=True, pqc=False, mint=10.0, node_id="n1", **paths))
    argv = ["smdc", "economy", "send", "sov_x", "0.0000001", "--wallet-file", paths["wallet_file"]]
    monkeypatch.setattr(sys, "argv", [*argv, "--db-path", paths["db_path"]])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1


def test_demoted_nodes_stop_naming_a_leader() -> None:
    cluster = RaftCluster(["a", "b", "c"])
    assert cluster.step_election("a") == "a"
    leader = cluster.nodes["a"]
    assert leader.status()["leader_id"] == "a"

    leader.handle_append_reply(
        "b", AppendEntriesReply(term=leader.current_term + 1, success=False, responder_id="b", match_index=0), 3
    )
    assert leader.role == NodeRole.FOLLOWER
    assert leader.status()["leader_id"] is None  # was: still "a"

    candidate = RaftNode("x", peers=["y", "z"])
    candidate.leader_id = "y"
    candidate.start_election()
    candidate.leader_id = "y"  # stale knowledge of an older term's leader
    assert candidate.handle_vote_reply(RequestVoteReply(term=99, vote_granted=False, voter_id="y"), 3) is False
    assert candidate.role == NodeRole.FOLLOWER and candidate.current_term == 99
    assert candidate.status()["leader_id"] is None


def test_packet_loss_scenario_stops_cleanly_when_its_leader_is_demoted() -> None:
    def higher_term_reply(self: RaftNode, args: Any) -> AppendEntriesReply:
        return AppendEntriesReply(term=args.term + 1, success=False, responder_id=self.node_id, match_index=0)

    with patch.object(RaftNode, "handle_append_entries", autospec=True, side_effect=higher_term_reply):
        result = MeshChaosSimulator(["a", "b", "c"]).simulate_packet_loss_replication(loss_rate=0.0, max_rounds=5)

    assert result.success is False and result.resilience_score == 0.0
    assert result.details["leader_demoted"] is True
    assert result.details["replication_rounds"] == 1 and result.details["successful_transmissions"] == 1
    assert result.details["committed_entries"] == 0

    healthy = MeshChaosSimulator(["a", "b", "c"]).simulate_packet_loss_replication(loss_rate=0.0)
    assert healthy.success is True and healthy.details["leader_demoted"] is False


# --- E. Site, docs and infrastructure text -----------------------------------------------------


def test_site_does_not_claim_sgp4_or_a_static_space_contact() -> None:
    for path in (ROOT / "docs").rglob("*"):
        if path.suffix in {".js", ".html"}:
            assert "SGP4" not in path.read_text(encoding="utf-8"), path
    index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert ">IN CONTACT<" not in index
    assert 'id="hud-space-link"' in index
    hud = (ROOT / "docs" / "js" / "hud.js").read_text(encoding="utf-8")
    assert "updateSpaceLinkPill(data.space)" in hud and "space.contact_state" in hud


def test_headscale_pushes_no_third_party_resolver() -> None:
    import yaml

    config = yaml.safe_load((ROOT / "software/vpn/config/headscale.yaml").read_text(encoding="utf-8"))
    assert config["dns"]["nameservers"]["global"] == []
    assert config["dns"]["magic_dns"] is True
    assert config["derp"]["urls"] == []


def test_mailcow_override_network_name_is_configurable() -> None:
    import yaml

    text = (ROOT / "software/mailcow/docker-compose.override.yml").read_text(encoding="utf-8")
    override = yaml.safe_load(text)
    variable = "${SOVEREIGN_NET_NAME:-software_sovereign_net}"
    assert override["networks"]["sovereign_net"]["name"] == variable
    assert f"traefik.docker.network={variable}" in override["services"]["nginx-mailcow"]["labels"]
    assert text.count("software_sovereign_net") == 2  # only as the default of the variable
    lora = (ROOT / "software/mesh/lora/docker-compose.lora.yml").read_text(encoding="utf-8")
    assert variable in lora  # same variable as the LoRa stack


def test_image_has_no_ignored_syntax_directive_and_documents_the_token() -> None:
    dockerfile = (ROOT / "software/Dockerfile.smdc").read_text(encoding="utf-8")
    assert "\n# syntax=" not in dockerfile and not dockerfile.startswith("# syntax=")
    for buildkit_only in ("--mount=", "<<EOF", "--link", "--chmod="):
        assert buildkit_only not in dockerfile
    cmd_at = dockerfile.index('CMD ["dashboard"')
    assert "SMDC_DASHBOARD_TOKEN" in dockerfile[cmd_at - 700 : cmd_at]


@pytest.mark.parametrize("script", ["dev.ps1", "quality_gate.ps1"])
def test_windows_runners_delegate_the_bash_only_gates(script: str) -> None:
    text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert '. "$PSScriptRoot\\bash_gates.ps1"' in text
    assert '-Script "check_mirrors.sh"' in text and '-Script "validate_compose.sh"' in text
    helper = (ROOT / "scripts" / "bash_gates.ps1").read_text(encoding="utf-8")
    assert "SKIPPED" in helper and "not reimplemented in PowerShell" in helper
    for gate in ("check_mirrors.sh", "validate_compose.sh"):
        assert (ROOT / "scripts" / gate).is_file()


# --- conftest isolation -------------------------------------------------------------------------


def test_status_cache_is_populated_by_this_test() -> None:
    dashboard.get_cached_system_status_payload()
    assert dashboard._STATUS_CACHE is not None


def test_status_cache_never_leaks_into_the_next_test() -> None:
    assert dashboard._STATUS_CACHE is None
