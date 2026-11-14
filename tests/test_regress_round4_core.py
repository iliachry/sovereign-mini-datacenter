"""Regression tests for the round-4 review findings in the indexer, the enterprise registry,
the mesh/bootstrap reachability path, the DTN submission contract and the Mailcow add-on.

No external network: Qdrant and peers are fakes, every file lives under ``tmp_path``.
"""

from __future__ import annotations

import inspect
import json
import multiprocessing
import re
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sovereign_dc import config as smdc_config
from sovereign_dc.agents import knowledge_indexer
from sovereign_dc.agents.bootstrap_provisioner import BootstrapProvisioner
from sovereign_dc.enterprise import registry as registry_module
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.sdk import SMDCClient
from sovereign_dc.mcp import resources as mcp_resources
from sovereign_dc.mcp import tools as mcp_tools
from sovereign_dc.mesh import mesh_sync
from sovereign_dc.web import dashboard

ROOT = Path(__file__).resolve().parents[1]


# ── 1. A document whose stale-chunk cleanup failed must stay tracked ──────────────────────


def _resp(body: Any) -> MagicMock:
    resp = MagicMock()
    resp.status = 200
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.headers = {}
    resp.__enter__.return_value = resp
    return resp


class FlakyQdrant:
    """In-memory Qdrant whose deletes can be made to fail."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}
        self.fail_delete = False

    def __call__(self, req: Any, timeout: float = 0) -> MagicMock:
        url, method = req.full_url, req.get_method()
        body = json.loads(req.data) if req.data else {}
        if "/points/delete" in url:
            if self.fail_delete:
                raise urllib.error.URLError("delete refused")
            path = body["filter"]["must"][0]["match"]["value"]
            keep = {i for cond in body["filter"].get("must_not", []) for i in cond.get("has_id", [])}
            self.points = {k: v for k, v in self.points.items() if v["payload"]["path"] != path or k in keep}
            return _resp({"result": {"status": "completed"}})
        if method == "PUT" and "/points" in url:
            for point in body["points"]:
                self.points[point["id"]] = point
            return _resp({"result": {"status": "completed"}})
        return _resp({})

    def count(self, path: Path) -> int:
        return sum(1 for p in self.points.values() if p["payload"]["path"] == str(path))


@pytest.fixture
def qdrant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FlakyQdrant]:
    fake = FlakyQdrant()
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


def test_new_document_with_failed_final_cleanup_is_removed_once_it_vanishes(
    tmp_path: Path, qdrant: FlakyQdrant
) -> None:
    doc = tmp_path / "docs" / "note.md"
    doc.write_text("one small document", encoding="utf-8")
    indexed: set[tuple[str, float]] = set()

    qdrant.fail_delete = True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert qdrant.count(doc) == 1  # the upsert was written
    assert indexed == set()  # not recorded as indexed: the scan must retry it
    assert knowledge_indexer.pending_reconcile_paths() == frozenset({str(doc)})  # was: tracked nowhere

    doc.unlink()
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)  # Qdrant still refuses: counted, retried
    assert qdrant.count(doc) == 1
    assert knowledge_indexer.pending_reconcile_paths() == frozenset({str(doc)})

    qdrant.fail_delete = False
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)  # was (0, 0) with the point left behind
    assert qdrant.points == {}
    assert knowledge_indexer.pending_reconcile_paths() == frozenset()
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)


def test_pending_reconciliation_is_resolved_by_the_next_successful_index(tmp_path: Path, qdrant: FlakyQdrant) -> None:
    doc = tmp_path / "docs" / "note.md"
    doc.write_text("one small document", encoding="utf-8")
    indexed: set[tuple[str, float]] = set()
    qdrant.fail_delete = True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    qdrant.fail_delete = False
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert qdrant.count(doc) == 1  # the complete version stayed searchable throughout
    assert knowledge_indexer.pending_reconcile_paths() == frozenset()
    assert {path for path, _ in indexed} == {str(doc)}


def test_pending_reconciliation_survives_an_unlistable_watch_dir(
    tmp_path: Path, qdrant: FlakyQdrant, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "docs" / "note.md"
    doc.write_text("one small document", encoding="utf-8")
    indexed: set[tuple[str, float]] = set()
    qdrant.fail_delete = True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    qdrant.fail_delete = False

    # An unmounted watch directory must not look like "the document was deleted".
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(tmp_path / "unmounted"))
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)
    assert qdrant.count(doc) == 1
    assert knowledge_indexer.pending_reconcile_paths() == frozenset({str(doc)})


def test_indexer_mirror_carries_the_reconcile_tracking() -> None:
    src = (ROOT / "src/sovereign_dc/agents/knowledge_indexer.py").read_bytes()
    assert (ROOT / "software/agents/knowledge_indexer.py").read_bytes() == src
    assert b"_reconcile_pending" in src


# ── 2. Overlapping registry writers must not erase each other's registrations ─────────────


def _manifest(app_id: str) -> Any:
    return EnterpriseRegistry.scaffold_manifest(name=f"App {app_id}", app_id=app_id)


def _app_ids(path: Path) -> list[str]:
    entries = json.loads(path.read_text(encoding="utf-8"))["apps"]
    return sorted(entry["app_id"] for entry in entries if "app_id" in entry)


def test_two_registry_instances_do_not_overwrite_each_other(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    first, second = EnterpriseRegistry(path), EnterpriseRegistry(path)
    assert first.register_app(_manifest("alpha")) == (True, [])
    assert second.register_app(_manifest("beta")) == (True, [])
    assert _app_ids(path) == ["alpha", "beta"]  # was: only beta
    # Each mutation reloads, so the second instance also sees the first one's app.
    assert second.get_app("alpha") is not None
    assert {m.app_id for m in EnterpriseRegistry(path).list_apps()} == {"alpha", "beta"}


def test_unregister_removes_only_its_own_app_from_the_current_file(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    first, second = EnterpriseRegistry(path), EnterpriseRegistry(path)
    first.register_app(_manifest("alpha"))
    second.register_app(_manifest("beta"))
    first.register_app(_manifest("gamma"))
    # "beta" was registered by the other instance after this one loaded the file.
    assert first.unregister_app("beta") is True
    assert _app_ids(path) == ["alpha", "gamma"]
    assert second.unregister_app("beta") is False  # already gone on disk
    assert _app_ids(path) == ["alpha", "gamma"]


def test_unpersisted_registrations_are_merged_into_the_current_file_on_save(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    first, second = EnterpriseRegistry(path), EnterpriseRegistry(path)
    first.register_app(_manifest("alpha"), persist=False)
    first.register_app(_manifest("beta"), persist=False)
    assert not path.exists()
    second.register_app(_manifest("gamma"))
    first.save_registry()
    assert _app_ids(path) == ["alpha", "beta", "gamma"]
    assert {m.app_id for m in first.list_apps()} == {"alpha", "beta", "gamma"}


def test_locked_save_keeps_unparsed_entries_and_backs_up_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    EnterpriseRegistry(path).register_app(_manifest("alpha"))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["apps"].append({"broken": True})
    path.write_text(json.dumps(data), encoding="utf-8")

    reg = EnterpriseRegistry(path)
    reg.register_app(_manifest("beta"))
    stored = json.loads(path.read_text(encoding="utf-8"))["apps"]
    assert {"broken": True} in stored  # written back verbatim, exactly once
    assert stored.count({"broken": True}) == 1
    assert sorted(e["app_id"] for e in stored if "app_id" in e) == ["alpha", "beta"]

    # The file turns to garbage between this instance's load and its next save.
    path.write_text("{not json", encoding="utf-8")
    assert reg.register_app(_manifest("gamma")) == (True, [])
    backups = list(tmp_path.glob("registry.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "{not json"
    assert _app_ids(path) == ["alpha", "beta", "gamma"]  # this instance's last good view + the change


def test_corrupt_registry_is_backed_up_once_however_often_it_is_reloaded(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("{not json", encoding="utf-8")
    reg = EnterpriseRegistry(path)  # backs the file up
    assert reg.unregister_app("absent") is False  # reloads the same broken file
    assert reg.register_app(_manifest("alpha")) == (True, [])  # reloads again, then replaces it
    assert len(list(tmp_path.glob("registry.json.corrupt-*"))) == 1
    assert _app_ids(path) == ["alpha"]


def test_registry_lock_fallback_without_fcntl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "fcntl", None)
    path = tmp_path / "registry.json"
    first, second = EnterpriseRegistry(path), EnterpriseRegistry(path)
    first.register_app(_manifest("alpha"))
    second.register_app(_manifest("beta"))
    assert _app_ids(path) == ["alpha", "beta"]
    assert not list(tmp_path.glob("*.excl"))  # the exclusive lock file is removed on release

    # A lock file left by a crashed writer is detected as stale and replaced.
    stale = tmp_path / "registry.json.lock.excl"
    stale.write_text("99999999", encoding="utf-8")
    monkeypatch.setattr(registry_module, "LOCK_STALE_SECONDS", 0.0)
    first.register_app(_manifest("gamma"))
    assert _app_ids(path) == ["alpha", "beta", "gamma"]
    assert not stale.exists()


def _register_in_child(path: str, app_id: str) -> None:
    reg = EnterpriseRegistry(Path(path))
    ok, errors = reg.register_app(EnterpriseRegistry.scaffold_manifest(name=f"App {app_id}", app_id=app_id))
    if not ok:
        raise SystemExit(f"registration failed: {errors}")


def test_eight_processes_registering_at_once_all_end_up_in_the_file(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    ctx = multiprocessing.get_context("spawn")
    workers = [ctx.Process(target=_register_in_child, args=(str(path), f"app-{n}")) for n in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)
    assert [worker.exitcode for worker in workers] == [0] * 8
    assert _app_ids(path) == [f"app-{n}" for n in range(8)]


# ── 7. Bootstrap must not report a mesh peer that never answered ──────────────────────────


def test_default_node_id_is_shared_by_config_mesh_and_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NODE_ID", raising=False)
    assert mesh_sync.DEFAULT_NODE_ID == smdc_config.DEFAULT_NODE_ID
    assert smdc_config.SovereignConfig().node_id == smdc_config.DEFAULT_NODE_ID
    assert BootstrapProvisioner(dry_run=True).node_id == smdc_config.DEFAULT_NODE_ID
    # The standalone copy reads NODE_ID with the same default.
    source = (ROOT / "software/mesh/mesh_sync.py").read_text(encoding="utf-8")
    assert 'os.getenv("NODE_ID", DEFAULT_NODE_ID)' in source


def test_check_peer_health_never_reports_reachable_without_a_probe() -> None:
    local = mesh_sync.MeshNode(mesh_sync.NODE_ID, "100.64.0.1", "dtn://local.space", "Core")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")) as urlopen:
        assert mesh_sync.check_peer_health(local) is False  # was True, without any request
        assert urlopen.call_count == 1
    assert list(inspect.signature(mesh_sync.check_peer_health).parameters) == ["peer"]


def test_remote_peers_needs_the_local_node_named_explicitly() -> None:
    peers = mesh_sync.default_peers()
    assert [p.node_id for p in mesh_sync.remote_peers(peers, "smdc-node-02")] == ["smdc-node-01", "smdc-node-03"]
    assert len(mesh_sync.remote_peers(peers, "not-in-the-topology")) == 3
    assert inspect.signature(mesh_sync.remote_peers).parameters["local_node_id"].default is inspect.Parameter.empty


@pytest.mark.parametrize("node_id", [None, "smdc-node-01", "smdc-node-02"])
def test_bootstrap_reports_no_wireguard_when_every_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, node_id: str | None
) -> None:
    monkeypatch.delenv("NODE_ID", raising=False)
    monkeypatch.delenv("CLUSTER_CONFIG_PATH", raising=False)
    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "spool.db"))
    monkeypatch.setattr(mesh_sync, "CLUSTER_CONFIG_PATH", str(tmp_path / "absent.yaml"))
    provisioner = BootstrapProvisioner(node_id=node_id, dry_run=True)
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        net = provisioner.phase_2_network()
    assert net["tier1_wireguard"] is False  # was True for the default node id
    assert net["active_peers"] == []
    expected = [p.node_id for p in mesh_sync.default_peers() if p.node_id != provisioner.node_id]
    assert net["unreachable_peers"] == expected
    assert "network: no mesh peer reachable over WireGuard" in provisioner.state.unverified


def test_bootstrap_counts_only_remote_peers_that_answered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "spool.db"))
    monkeypatch.setattr(mesh_sync, "CLUSTER_CONFIG_PATH", str(tmp_path / "absent.yaml"))
    monkeypatch.delenv("CLUSTER_CONFIG_PATH", raising=False)

    def urlopen(req: Any, timeout: float = 0) -> MagicMock:
        if "100.64.0.3" in req.full_url:
            return _resp({})
        raise urllib.error.URLError("no route")

    provisioner = BootstrapProvisioner(node_id="smdc-node-01", dry_run=True)
    with patch("urllib.request.urlopen", side_effect=urlopen):
        net = provisioner.phase_2_network()
    assert net["tier1_wireguard"] is True
    assert net["active_peers"] == ["smdc-node-03"]
    assert net["unreachable_peers"] == ["smdc-node-02"]


# ── 8. One DTN submission contract across HTTP, SDK and MCP ───────────────────────────────


@pytest.fixture
def dtn_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("SMDC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "spool.db"))
    monkeypatch.delenv("NODE_ID", raising=False)
    smdc_config.reset_config()
    yield smdc_config.get_config().node_id
    smdc_config.reset_config()


def test_dtn_defaults_are_identical_across_http_sdk_and_mcp(dtn_node: str) -> None:
    destination = "dtn://ground.earth/t"

    # HTTP: what the dashboard handler parses out of a request body without optional fields.
    http = dashboard.parse_dtn_submission({"destination": destination, "payload": "x"}, dtn_node)
    # SDK: the node is unreachable, so the in-process path stores the bundle.
    with patch.object(SMDCClient, "_http_post", return_value={}):
        sdk = SMDCClient().send_dtn_bundle(destination, "x")
    # MCP tool.
    mcp = mcp_tools.tool_spool_dtn_bundle({"destination": destination, "payload": "x"})

    assert sdk["status"] == "spooled" and mcp["success"] is True
    assert http.ttl_seconds == sdk["ttl_seconds"] == mcp["ttl_seconds"] == dashboard.DTN_DEFAULT_TTL_SECONDS
    assert dashboard.DTN_DEFAULT_TTL_SECONDS == 7 * 86400  # was 86,400 through MCP
    assert http.priority == sdk["priority"] == mcp["priority_level"] == dashboard.DTN_DEFAULT_PRIORITY
    assert inspect.signature(SMDCClient.send_dtn_bundle).parameters["ttl_sec"].default is None

    # Source-EID rule: dtn://<node-id>/<service> by default ...
    assert http.source == sdk["source"] == dashboard.node_eid(dtn_node, "api")
    assert mcp["source"] == dashboard.node_eid(dtn_node, "mcp")
    # ... a valid requested EID is used as given, an invalid one falls back to the node's own.
    for requested, expect_used in (("dtn://client.example/app", True), ("not an eid", False)):
        http = dashboard.parse_dtn_submission({"destination": destination, "source": requested}, dtn_node)
        with patch.object(SMDCClient, "_http_post", return_value={}):
            sdk = SMDCClient().send_dtn_bundle(destination, "x", source_eid=requested)
        mcp = mcp_tools.tool_spool_dtn_bundle({"destination": destination, "payload": "x", "source": requested})
        if expect_used:
            assert http.source == sdk["source"] == mcp["source"] == requested
        else:
            assert http.source == sdk["source"] == dashboard.node_eid(dtn_node, "api")
            assert mcp["source"] == dashboard.node_eid(dtn_node, "mcp")


def test_mcp_tool_description_states_the_shared_default_lifetime() -> None:
    tool = next(t for t in mcp_tools.get_mcp_tools() if t.name == "spool_dtn_bundle")
    description = tool.input_schema["properties"]["ttl_seconds"]["description"]
    assert str(dashboard.DTN_DEFAULT_TTL_SECONDS) in description and "7 days" in description
    assert str(dashboard.DTN_MAX_TTL_SECONDS // 86400) + " days" in description
    assert "86400," not in description and "default 86400" not in description
    agents_md = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert re.search(r"`ttl` \(seconds, default 7 days, capped at 30 days", agents_md)


def test_uav_status_resource_does_not_step_a_simulation_on_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from sovereign_dc.metaverse.engine import MetaverseOrchestrator

    orch = MetaverseOrchestrator()
    monkeypatch.setattr(dashboard, "_METAVERSE_ORCH", orch)

    fresh = json.loads(mcp_resources.read_metaverse_uav_status_resource())
    assert fresh["uri"] == "smdc://metaverse/uav/status"
    assert fresh["source"] == "fresh_simulation" and fresh["total_cycles_executed"] == 0
    assert fresh["uav_position"] == list(orch.uav_position)
    assert fresh["per_receiver_sinr"] is None and fresh["depin_sla"] is None
    assert orch.total_cycles_executed == 0 and orch.traces == []  # was: a fresh orchestrator stepped per read

    trace = orch.step(deterministic=True)
    for _ in range(2):  # reading twice changes nothing
        after = json.loads(mcp_resources.read_metaverse_uav_status_resource())
        assert after["source"] == "process_orchestrator" and after["total_cycles_executed"] == 1
        assert after["uav_position"] == list(trace.uav_position)
        assert after["per_receiver_sinr"] == trace.per_receiver_sinr
        assert after["depin_sla"]["block_hash"] == trace.sla_result.block_hash
    assert orch.total_cycles_executed == 1


# ── 5 / 6. Mailcow: generated configuration and a reachable, verifiable SMTP endpoint ─────

MAILCOW = ROOT / "software" / "mailcow"
MAILCOW_CHANGED_SETTINGS = {
    "HTTP_BIND": "127.0.0.1",
    "HTTP_PORT": "8025",
    "HTTPS_BIND": "127.0.0.1",
    "HTTPS_PORT": "8443",
    "HTTP_REDIRECT": "n",
    "SKIP_LETS_ENCRYPT": "y",
}


def test_mailcow_example_lists_only_the_settings_to_change() -> None:
    text = (MAILCOW / "mailcow.conf.example").read_text(encoding="utf-8")
    active = dict(line.split("=", 1) for line in text.splitlines() if re.match(r"^[A-Z0-9_]+=", line))
    assert active == MAILCOW_CHANGED_SETTINGS
    # It can no longer be mistaken for a full configuration with blank database passwords.
    for generated_only in ("DBPASS", "DBROOT", "MAILCOW_HOSTNAME=", "COMPOSE_PROJECT_NAME="):
        assert not re.search(rf"^{generated_only}", text, flags=re.MULTILINE)
    assert "generate_config.sh" in text and "must not be copied" in text


def test_mailcow_instructions_require_the_generator_and_never_copy_the_example() -> None:
    readme = (MAILCOW / "README.md").read_text(encoding="utf-8")
    setup = (ROOT / "software" / "setup.sh").read_text(encoding="utf-8")
    for text in (readme, setup):
        assert "./generate_config.sh" in text
        assert not re.search(r"cp\s+\S*mailcow\.conf\.example", text)  # was the documented shortcut
    # The README's sed command sets exactly the example's settings.
    sed_settings = dict(re.findall(r"-e 's/\^([A-Z_]+)=\.\*/\1=([^/]*)/'", readme))
    assert sed_settings == MAILCOW_CHANGED_SETTINGS
    for url in (
        "https://docs.mailcow.email/getstarted/install/",
        "https://docs.mailcow.email/post_installation/reverse-proxy/r_p/",
        "https://docs.mailcow.email/post_installation/reverse-proxy/r_p-traefik3/",
    ):
        assert url in readme


def test_mailcow_smtp_guidance_names_an_endpoint_that_exists() -> None:
    import yaml

    readme = (MAILCOW / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / "software" / "env.example").read_text(encoding="utf-8")
    override_text = (MAILCOW / "docker-compose.override.yml").read_text(encoding="utf-8")
    for text in (readme, env_example, override_text):
        assert "mailcow-postfix" not in text  # no such service or alias exists upstream
    assert re.search(r"^SMTP_HOST=mail\.yourdomain\.com\b", readme, flags=re.MULTILINE)
    assert re.search(r"^SMTP_PORT=587\b", readme, flags=re.MULTILINE)
    assert "#   SMTP_HOST=mail.yourdomain.com" in env_example
    assert "data/assets/ssl/cert.pem" in readme  # how Postfix gets a certificate that verifies

    # The override still attaches only the web UI to the core network, as the guide says.
    override = yaml.safe_load(override_text)
    assert set(override["services"]) == {"nginx-mailcow"}
    assert override["services"]["nginx-mailcow"]["networks"] == ["mailcow-network", "sovereign_net"]

    # Only services that really receive SMTP_* are named as using it.
    compose = (ROOT / "software" / "docker-compose.yml").read_text(encoding="utf-8")
    core = yaml.safe_load(compose)["services"]
    wired = {name for name, svc in core.items() if "${SMTP_HOST" in json.dumps(svc)}
    assert wired == {"gitlab", "vaultwarden"}
    assert "passes these variables to GitLab and Vaultwarden" in readme
