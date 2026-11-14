"""Regression tests for the leftover fixes: import-time logging, temp files, config wiring, file modes."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from sovereign_dc import cli, shed_benchmark
from sovereign_dc import config as smdc_config
from sovereign_dc.agents import knowledge_indexer, sentinel_copilot
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.events import get_event_bus
from sovereign_dc.mesh import mesh_sync
from sovereign_dc.mesh.chaos import MeshChaosSimulator
from sovereign_dc.space.dtn.bundle import Bundle
from sovereign_dc.space.dtn.router import DTNRouter

ROOT = Path(__file__).resolve().parent.parent

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


def _mode(path: Path | str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# ── 1. Importing the package never configures logging ─────────────────────

_IMPORT_PROBE = """
import importlib, logging, pkgutil, sys
root = logging.getLogger()
before = (list(root.handlers), root.level)
import sovereign_dc
names = [m.name for m in pkgutil.walk_packages(sovereign_dc.__path__, "sovereign_dc.")]
for name in names:
    importlib.import_module(name)
after = (list(root.handlers), root.level)
assert len(names) > 40, names
assert before == after, f"root logger changed by an import: {before} -> {after}"
print(len(names))
"""


def test_importing_every_module_leaves_the_root_logger_untouched(tmp_path: Path) -> None:
    """Runs in a fresh interpreter: modules already imported by pytest would not re-execute."""
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "HOME": str(tmp_path / "home"),
        "SMDC_STATE_DIR": str(tmp_path / "state"),
        "DTN_DB_PATH": str(tmp_path / "state" / "dtn.db"),
    }
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE], capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) > 40
    # Importing must not create the node's state either.
    assert not (tmp_path / "home" / ".smdc").exists()


@pytest.mark.parametrize(
    "relative",
    [
        "src/sovereign_dc/mesh/mesh_sync.py",
        "src/sovereign_dc/agents/gitlab_reviewer.py",
        "src/sovereign_dc/agents/sentinel_copilot.py",
        "src/sovereign_dc/agents/knowledge_indexer.py",
        "src/sovereign_dc/mesh/lora/meshtastic_gateway.py",
    ],
)
def test_standalone_scripts_configure_logging_only_in_their_entry_point(relative: str) -> None:
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert source.count("logging.basicConfig(") == 1
    assert source.index("logging.basicConfig(") > source.index('if __name__ == "__main__":')
    assert "LOG_FORMAT" in source or "[LoRaGateway]" in source


def test_space_exporter_configures_logging_in_run_only() -> None:
    source = (ROOT / "src/sovereign_dc/space/space_exporter.py").read_text(encoding="utf-8")
    assert source.count("logging.basicConfig(") == 1
    assert source.index("logging.basicConfig(") > source.index("def run() -> None:")


def test_scaffolded_sample_app_configures_logging_only_when_run_as_a_script() -> None:
    manifest = EnterpriseRegistry.scaffold_manifest(name="Sample", app_id="sample-app")
    source = EnterpriseRegistry._render_sample_app(manifest)
    compile(source, "app.py", "exec")
    assert source.count("logging.basicConfig(") == 1
    assert source.index("logging.basicConfig(") > source.index('if __name__ == "__main__":')


def test_mesh_sync_logs_through_its_own_named_logger(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    mesh_sync.log_peer_reachable(mesh_sync.MeshNode("n2", "100.64.0.2", "dtn://n2", "Edge"))
    assert [r.name for r in caplog.records] == ["MeshSync"]


# ── 4. Cluster topology file ──────────────────────────────────────────────


def test_load_peers_honours_cluster_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "cluster.yaml"
    config.write_text(
        "nodes:\n"
        "  - {id: alpha, wireguard_ip: 10.0.0.1, dtn_eid: 'dtn://alpha', role: gateway}\n"
        "  - {id: beta, wireguard_ip: 10.0.0.2}\n"
        "  - {id: broken}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLUSTER_CONFIG_PATH", str(config))
    peers = mesh_sync.load_peers()
    assert [(p.node_id, p.wireguard_ip, p.dtn_eid, p.role) for p in peers] == [
        ("alpha", "10.0.0.1", "dtn://alpha", "gateway"),
        ("beta", "10.0.0.2", "dtn://beta.sovereign.space", "unknown"),
    ]


@pytest.mark.parametrize("content", [None, "nodes: []\n", "- just\n- a list\n", "nodes: [\n"])
def test_load_peers_falls_back_to_reference_topology_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, content: str | None
) -> None:
    config = tmp_path / "cluster.yaml"
    if content is not None:
        config.write_text(content, encoding="utf-8")
    monkeypatch.setenv("CLUSTER_CONFIG_PATH", str(config))
    caplog.set_level(logging.WARNING)
    peers = mesh_sync.load_peers()
    assert [p.node_id for p in peers] == ["smdc-node-01", "smdc-node-02", "smdc-node-03"]
    assert str(config) in caplog.text


def test_default_cluster_config_is_next_to_the_module_never_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLUSTER_CONFIG_PATH", raising=False)
    monkeypatch.setattr(mesh_sync, "CLUSTER_CONFIG_PATH", "")
    (tmp_path / "cluster_config.yaml").write_text("nodes: [{id: planted, wireguard_ip: 6.6.6.6}]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert mesh_sync.cluster_config_path() == str(Path(mesh_sync.__file__).resolve().parent / "cluster_config.yaml")
    assert "planted" not in [p.node_id for p in mesh_sync.load_peers()]


def test_single_cluster_config_copy_is_loadable_by_the_standalone_script(monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = ROOT / "software" / "mesh" / "cluster_config.yaml"
    assert shipped.is_file()
    assert not (ROOT / "src" / "sovereign_dc" / "mesh" / "cluster_config.yaml").exists()
    monkeypatch.setenv("CLUSTER_CONFIG_PATH", str(shipped))
    peers = mesh_sync.load_peers()
    assert [p.node_id for p in peers] == ["smdc-node-01", "smdc-node-02", "smdc-node-03"]
    assert peers[0].role == "primary_gateway"


# ── 5. Temporary files are removed ────────────────────────────────────────


@pytest.fixture
def scratch_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirects ``tempfile`` to an empty directory under tmp_path so leftovers are visible."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    return scratch


def test_chaos_dtn_scenario_leaves_no_spool_behind(scratch_tmp: Path) -> None:
    result = MeshChaosSimulator().simulate_terrestrial_sever_dtn_fallback()
    assert result.success is True
    assert result.details["spool_removed"] is True
    assert not os.path.exists(os.path.dirname(result.details["spool_db"]))
    assert list(scratch_tmp.iterdir()) == []


def test_chaos_dtn_scenario_cleans_up_when_spooling_fails(scratch_tmp: Path) -> None:
    with patch.object(DTNRouter, "queue_bundle", side_effect=RuntimeError("disk full")):
        with pytest.raises(RuntimeError, match="disk full"):
            MeshChaosSimulator().simulate_terrestrial_sever_dtn_fallback()
    assert list(scratch_tmp.iterdir()) == []


def test_benchmark_leaves_no_temp_directory_behind(scratch_tmp: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = Namespace(all=False, ai=False, dtn=True, system=False, shed=True, shed_iterations=3, export=None)
    assert cli.cmd_benchmark(args) == 0
    assert "bundles/sec" in capsys.readouterr().out
    assert list(scratch_tmp.iterdir()) == []


def test_benchmark_removes_its_spool_when_ingestion_fails(scratch_tmp: Path) -> None:
    args = Namespace(all=False, ai=False, dtn=True, system=False, export=None)
    with patch.object(DTNRouter, "queue_bundle", side_effect=RuntimeError("disk full")):
        with pytest.raises(RuntimeError, match="disk full"):
            cli.cmd_benchmark(args)
    assert list(scratch_tmp.iterdir()) == []


# ── 3. Load-shedding latency benchmark ────────────────────────────────────


def test_shed_benchmark_measures_the_real_path_and_restores_global_state(
    scratch_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", "/nonexistent/original-mode")
    bus = get_event_bus()
    manager_logger = logging.getLogger("smdc.enterprise.manager")
    level_before = manager_logger.level

    result = shed_benchmark.measure_shed_latency(iterations=5, apps=2)

    assert result["simulated"] is True and result["hardware_actuation_measured"] is False
    assert result["iterations"] == 5 and result["deferrable_apps"] == 2
    stages = result["stages"]
    assert set(stages) == {"sample_to_mode_decision", "sample_to_apps_paused", "sample_to_mode_published"}
    for stage in stages.values():
        assert 0.0 <= stage["min_ms"] <= stage["median_ms"] <= stage["p95_ms"] <= stage["max_ms"]
    # The stages are cumulative from the same start: decision, then pause, then the mode file.
    assert stages["sample_to_mode_decision"]["min_ms"] <= stages["sample_to_apps_paused"]["min_ms"]
    assert stages["sample_to_apps_paused"]["min_ms"] <= stages["sample_to_mode_published"]["min_ms"]
    assert stages["sample_to_mode_published"]["median_ms"] > 0.0

    assert os.environ["SOVEREIGN_MODE_FILE"] == "/nonexistent/original-mode"
    assert bus.subscriber_count() == 0
    assert manager_logger.level == level_before
    assert list(scratch_tmp.iterdir()) == []


def test_shed_benchmark_refuses_to_time_a_broken_shed_path(scratch_tmp: Path) -> None:
    with patch.object(sentinel_copilot, "evaluate_mode", return_value=("NORMAL", "wrong")):
        with pytest.raises(RuntimeError, match="expected ECO_PRESERVATION"):
            shed_benchmark.measure_shed_latency(iterations=1)
    with patch("sovereign_dc.enterprise.manager.EnterpriseManager._on_load_shedding_event"):
        with pytest.raises(RuntimeError, match="not every deferrable app was paused"):
            shed_benchmark.measure_shed_latency(iterations=1)
    assert get_event_bus().subscriber_count() == 0
    assert list(scratch_tmp.iterdir()) == []


@pytest.mark.parametrize(("iterations", "apps"), [(0, 1), (1, 0)])
def test_shed_benchmark_rejects_empty_runs(iterations: int, apps: int) -> None:
    with pytest.raises(ValueError):
        shed_benchmark.measure_shed_latency(iterations=iterations, apps=apps)


def test_summarize_ms_uses_median_and_nearest_rank_p95() -> None:
    summary = shed_benchmark.summarize_ms([i / 1000.0 for i in range(1, 101)])
    assert summary == {"median_ms": 50.5, "p95_ms": 95.0, "min_ms": 1.0, "max_ms": 100.0}
    assert shed_benchmark.summarize_ms([0.002])["p95_ms"] == 2.0


def test_cli_benchmark_shed_flag_exports_measurements(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    export = tmp_path / "bench.json"
    with patch("sys.argv", ["smdc", "benchmark", "--shed", "--shed-iterations", "4", "--export", str(export)]):
        cli.main()
    out = capsys.readouterr().out
    assert "[4/4]" in out and "[1/4]" not in out
    assert "no GPU/cgroup/relay actuation" in out
    benchmarks = json.loads(export.read_text(encoding="utf-8"))["benchmarks"]
    assert list(benchmarks) == ["load_shedding"]
    assert benchmarks["load_shedding"]["iterations"] == 4


def test_cli_benchmark_all_includes_shed_and_rejects_bad_iteration_count(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.cmd_benchmark(Namespace(all=True, ai=False, dtn=False, system=False, shed_iterations=0)) == 2
    assert "--shed-iterations must be at least 1" in capsys.readouterr().out
    assert (
        cli.cmd_benchmark(Namespace(all=True, ai=False, dtn=False, system=False, shed_iterations=2, export=None)) == 0
    )
    assert "[4/4]" in capsys.readouterr().out


# ── 6. Sentinel poll interval and docs watch dir are configurable ─────────


@pytest.mark.parametrize(("raw", "expected"), [("", 30.0), ("  ", 30.0), ("5", 5.0), ("0.5", 30.0), ("1", 1.0)])
def test_sentinel_poll_interval_parsing(raw: str, expected: float) -> None:
    assert sentinel_copilot.poll_interval_seconds(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "0", "-3", "nan", "inf"])
def test_sentinel_poll_interval_rejects_unusable_values(raw: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    assert sentinel_copilot.poll_interval_seconds(raw) == sentinel_copilot.DEFAULT_POLL_INTERVAL_SECONDS
    assert "SENTINEL_POLL_INTERVAL_SECONDS" in caplog.text


def test_sentinel_reads_poll_interval_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_POLL_INTERVAL_SECONDS", "7")
    assert sentinel_copilot.poll_interval_seconds() == 7.0
    monkeypatch.delenv("SENTINEL_POLL_INTERVAL_SECONDS")
    assert sentinel_copilot.poll_interval_seconds() == 30.0


def test_sentinel_loop_sleeps_for_the_configured_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sentinel_copilot, "POLL_INTERVAL_SECONDS", 7.0)
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        raise StopIteration

    with (
        patch.object(sentinel_copilot, "get_telemetry", return_value={}),
        patch.object(sentinel_copilot.time, "sleep", side_effect=fake_sleep),
        pytest.raises(StopIteration),
    ):
        sentinel_copilot.run_copilot()
    assert slept == [7.0]


def test_config_fields_use_the_env_names_the_agents_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SENTINEL_POLL_INTERVAL_SECONDS", "12")
    monkeypatch.setenv("DOCS_WATCH_DIR", str(tmp_path / "docs"))
    cfg = smdc_config.SovereignConfig.from_env()
    assert cfg.sentinel_poll_interval == 12.0
    assert cfg.docs_watch_dir == str(tmp_path / "docs")
    monkeypatch.delenv("SENTINEL_POLL_INTERVAL_SECONDS")
    assert smdc_config.SovereignConfig.from_env().sentinel_poll_interval == 30.0


def test_agents_overlay_passes_the_poll_interval_through() -> None:
    text = (ROOT / "software" / "agents" / "docker-compose.agents.yml").read_text(encoding="utf-8")
    assert "SENTINEL_POLL_INTERVAL_SECONDS=${SENTINEL_POLL_INTERVAL_SECONDS:-30}" in text


# ── 7. `smdc agent index` indexes exactly what the worker would ───────────


def _document_tree(root: Path) -> None:
    for relative in (
        "a.md",
        "table.csv",
        "settings.yaml",
        "sub/b.txt",
        ".hidden/secret.md",
        "appdata_x/internal.md",
        "alice/files/note.md",
        "alice/cache/tmp.md",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content", encoding="utf-8")
    (root / "big.md").write_text("x" * 200, encoding="utf-8")


def _cli_indexed(root: Path) -> tuple[int, list[str]]:
    seen: list[str] = []

    def record(path: str) -> bool:
        seen.append(path)
        return True

    with (
        patch.object(knowledge_indexer, "ensure_qdrant_collection", return_value=True),
        patch.object(knowledge_indexer, "process_file", side_effect=record),
    ):
        code = cli.cmd_agent_index(Namespace(directory=str(root)))
    return code, seen


def test_agent_index_uses_the_indexers_own_file_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "docs"
    _document_tree(root)
    monkeypatch.setattr(knowledge_indexer, "MAX_FILE_BYTES", 100)

    code, seen = _cli_indexed(root)
    worker_set = [p for p in knowledge_indexer.list_documents(str(root)) if not knowledge_indexer.is_oversized(p)]
    assert code == 0
    assert seen == worker_set
    relative = sorted(os.path.relpath(p, root) for p in seen)
    assert relative == ["a.md", "alice/files/note.md", "sub/b.txt", "table.csv"]
    assert "big.md" in capsys.readouterr().out  # reported as skipped, not silently dropped

    monkeypatch.setattr(knowledge_indexer, "INCLUDE_GLOBS", ("*/files/**",))
    _, seen = _cli_indexed(root)
    assert [os.path.relpath(p, root) for p in seen] == ["alice/files/note.md"]

    monkeypatch.setattr(knowledge_indexer, "INCLUDE_GLOBS", ())
    monkeypatch.setattr(knowledge_indexer, "EXCLUDE_DIRS", ("sub",))
    _, seen = _cli_indexed(root)
    assert ".hidden/secret.md" in [os.path.relpath(p, root) for p in seen]
    assert "sub/b.txt" not in [os.path.relpath(p, root) for p in seen]


def test_agent_index_defaults_to_docs_watch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "watched"
    root.mkdir()
    (root / "only.md").write_text("content", encoding="utf-8")
    monkeypatch.setenv("DOCS_WATCH_DIR", str(root))
    smdc_config.reset_config()
    seen: list[str] = []
    with (
        patch.object(knowledge_indexer, "ensure_qdrant_collection", return_value=True),
        patch.object(knowledge_indexer, "process_file", side_effect=lambda p: seen.append(p) or True),
        patch("sys.argv", ["smdc", "agent", "index"]),
    ):
        cli.main()
    assert seen == [str(root / "only.md")]


def test_agent_index_fails_when_a_directory_cannot_be_listed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "docs"
    root.mkdir()

    def broken_listing(watch_dir: str, walk_errors: list[OSError] | None = None) -> list[str]:
        assert walk_errors is not None
        walk_errors.append(PermissionError(13, "Permission denied", os.path.join(watch_dir, "locked")))
        return []

    with (
        patch.object(knowledge_indexer, "ensure_qdrant_collection", return_value=True),
        patch.object(knowledge_indexer, "list_documents", side_effect=broken_listing),
    ):
        assert cli.cmd_agent_index(Namespace(directory=str(root))) == 1
    assert "could not be listed" in capsys.readouterr().out


# ── 9. SQLite state files are private (0600) ──────────────────────────────


@posix_only
@pytest.mark.parametrize("kind", ["ledger", "router"])
def test_new_sqlite_state_files_and_their_side_files_are_created_0600(tmp_path: Path, kind: str) -> None:
    db = tmp_path / "db" / "state.db"
    db.parent.mkdir()
    previous = os.umask(0o022)
    try:
        if kind == "ledger":
            Ledger(db_path=str(db))
        else:
            DTNRouter(db_path=str(db)).queue_bundle(
                Bundle(source_eid="dtn://a", destination_eid="dtn://b", payload=b"x")
            )
        # A writer in WAL mode keeps -wal and -shm on disk while it is connected.
        conn = sqlite3.connect(db)
        try:
            conn.execute("CREATE TABLE probe (x INTEGER)")
            conn.commit()
            modes = {p.name: oct(_mode(p)) for p in db.parent.iterdir()}
        finally:
            conn.close()
    finally:
        os.umask(previous)
    assert {"state.db", "state.db-wal", "state.db-shm"} <= set(modes)
    assert set(modes.values()) == {"0o600"}, modes


@posix_only
@pytest.mark.parametrize("kind", ["ledger", "router"])
def test_existing_sqlite_files_are_tightened_on_open(tmp_path: Path, kind: str) -> None:
    db = tmp_path / "state.db"
    opener = (lambda: Ledger(db_path=str(db))) if kind == "ledger" else (lambda: DTNRouter(db_path=str(db)))
    opener()
    sidecar = tmp_path / "state.db-wal"
    sidecar.touch()
    os.chmod(db, 0o644)
    os.chmod(sidecar, 0o664)
    opener()
    assert _mode(db) == 0o600
    assert not sidecar.exists() or _mode(sidecar) == 0o600


@posix_only
def test_secure_db_file_never_follows_or_chmods_a_symlink(tmp_path: Path) -> None:
    from sovereign_dc.space.dtn import router as router_module

    target = tmp_path / "victim.txt"
    target.write_text("keep", encoding="utf-8")
    os.chmod(target, 0o644)
    link = tmp_path / "spool.db"
    link.symlink_to(target)
    router_module._secure_db_file(str(link))
    assert _mode(target) == 0o644
    assert target.read_text(encoding="utf-8") == "keep"


def test_in_memory_databases_create_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    Ledger().close()
    DTNRouter(db_path=":memory:").close()
    assert list(tmp_path.iterdir()) == [tmp_path / "smdc_home"]


# ── 2. The browser demo terminal claims nothing it did not check ──────────


def test_demo_terminal_prints_no_fabricated_audit_or_status_claims() -> None:
    terminal = (ROOT / "docs" / "js" / "calculator.js").read_text(encoding="utf-8")
    terminal = terminal[terminal.index("Simulated smdc Terminal") :]
    for claim in ("PASSED", "VERIFIED", "100%", "Compliance Score", "ACTIVE", "IN CONTACT", "TRANSMITTED"):
        assert claim not in terminal, claim
    assert "NO AUDIT WAS RUN" in terminal
    assert "smdc audit</span>, run on a Linux node" in terminal
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "simulated; not connected to a node" in page
    assert "LIVE</span>" not in page


# ── 10. Lint configuration carries no stale blanket ignores ───────────────


def test_ruff_config_has_no_blanket_ignore_for_tmp_paths_bind_all_or_md5() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ignore_block = pyproject[pyproject.index("ignore = [") : pyproject.index("[tool.ruff.lint.isort]")]
    ignored = [line.split('"')[1] for line in ignore_block.splitlines() if line.strip().startswith('"')]
    assert not {"S104", "S108", "S324"} & set(ignored)
    assert "dtn_spool" not in ignore_block
