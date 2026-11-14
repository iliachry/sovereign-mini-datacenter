"""Regression tests for reproduced defects in the autonomous agents."""

from __future__ import annotations

import importlib.util
import json
import logging
import urllib.error
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sovereign_dc.agents import gitlab_reviewer, knowledge_indexer, sentinel_copilot
from sovereign_dc.agents.bootstrap_provisioner import BootstrapProvisioner
from sovereign_dc.agents.technician_notifier import (
    BaseNotifier,
    DTNNotifier,
    FileNotifier,
    LoRaNotifier,
    MessageSeverity,
    MQTTNotifier,
    TechnicianMessage,
    TechnicianNotifierChain,
)

ROOT = Path(__file__).resolve().parent.parent


def _resp(body: Any, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


class RecordingNotifier(BaseNotifier):
    def __init__(self) -> None:
        self.sent: list[TechnicianMessage] = []

    def send(self, message: TechnicianMessage) -> bool:
        self.sent.append(message)
        return True


# --- GitLab reviewer (finding 3) ------------------------------------------------------------


REVIEWER_ID = 99
MR_AUTHOR_ID = 7


@pytest.fixture(autouse=True)
def _reset_reviewer_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gitlab_reviewer, "_reviewer_user_id", None)


class FakeGitLab:
    """Routes urlopen() calls like a tiny GitLab API and records posted notes."""

    def __init__(
        self,
        mrs: list[dict[str, Any]],
        existing_notes: list[dict[str, Any]] | None = None,
        user_lookup_fails: bool = False,
    ) -> None:
        self.mrs = mrs
        self.notes = list(existing_notes or [])
        self.posted: list[str] = []
        self.listing_urls: list[str] = []
        self.user_lookup_fails = user_lookup_fails
        self.user_lookups = 0

    def __call__(self, req: Any, timeout: float = 0) -> MagicMock:
        url = req.full_url
        if req.get_method() == "POST":
            body = json.loads(req.data.decode("utf-8"))["body"]
            self.posted.append(body)
            self.notes.append({"body": body, "author": {"id": REVIEWER_ID}})
            return _resp({})
        if url.endswith("/user"):
            self.user_lookups += 1
            if self.user_lookup_fails:
                raise urllib.error.URLError("identity lookup failed")
            return _resp({"id": REVIEWER_ID, "username": "smdc-reviewer"})
        if url.endswith("/changes"):
            return _resp({"changes": [{"diff": "+print('hello')"}]})
        if "/notes" in url:
            return _resp(self.notes)
        self.listing_urls.append(url)
        return _resp(self.mrs)


def test_reviewer_posts_once_per_mr_revision() -> None:
    gitlab = FakeGitLab([{"project_id": 1, "iid": 10, "sha": "aaa111"}])
    reviewed: set[gitlab_reviewer.ReviewKey] = set()
    with (
        patch.object(gitlab_reviewer, "GITLAB_TOKEN", "fake-token"),
        patch.object(gitlab_reviewer, "query_ollama", return_value="Looks fine."),
        patch("urllib.request.urlopen", side_effect=gitlab),
    ):
        assert gitlab_reviewer.poll_once(reviewed) == 1
        assert gitlab_reviewer.poll_once(reviewed) == 0
        assert gitlab_reviewer.poll_once(reviewed) == 0
        assert len(gitlab.posted) == 1

        # A new commit on the MR is a new revision and gets exactly one more review
        gitlab.mrs[0]["sha"] = "bbb222"
        assert gitlab_reviewer.poll_once(reviewed) == 1
        assert gitlab_reviewer.poll_once(reviewed) == 0
    assert len(gitlab.posted) == 2
    assert all("scope=all" in u and "state=opened" in u for u in gitlab.listing_urls)


def test_reviewer_does_not_repost_after_restart() -> None:
    marker = gitlab_reviewer.REVIEW_MARKER_TEMPLATE.format(sha="aaa111")
    gitlab = FakeGitLab(
        [{"project_id": 1, "iid": 10, "sha": "aaa111"}],
        existing_notes=[{"body": f"old\n{marker}", "author": {"id": REVIEWER_ID}}],
    )
    with (
        patch.object(gitlab_reviewer, "GITLAB_TOKEN", "fake-token"),
        patch.object(gitlab_reviewer, "query_ollama", return_value="Looks fine.") as llm,
        patch("urllib.request.urlopen", side_effect=gitlab),
    ):
        assert gitlab_reviewer.poll_once(set()) == 0  # fresh process: empty in-memory set
    assert gitlab.posted == []
    llm.assert_not_called()


@pytest.mark.parametrize("llm_output", ["Local LLM review unavailable: timed out", "No review output.", "  "])
def test_reviewer_posts_nothing_when_llm_failed(llm_output: str) -> None:
    gitlab = FakeGitLab([{"project_id": 1, "iid": 10, "sha": "aaa111"}])
    reviewed: set[gitlab_reviewer.ReviewKey] = set()
    with (
        patch.object(gitlab_reviewer, "GITLAB_TOKEN", "fake-token"),
        patch.object(gitlab_reviewer, "query_ollama", return_value=llm_output),
        patch("urllib.request.urlopen", side_effect=gitlab),
    ):
        assert gitlab_reviewer.poll_once(reviewed) == 0
    assert gitlab.posted == []
    assert reviewed == set()  # not recorded -> retried on the next poll


def test_reviewer_neutralises_quick_actions() -> None:
    gitlab = FakeGitLab([{"project_id": 1, "iid": 10, "sha": "aaa111"}])
    hostile = "Nice change.\n/approve\n  /merge\n/assign @root\nSee src/app.py for details."
    with (
        patch.object(gitlab_reviewer, "GITLAB_TOKEN", "fake-token"),
        patch.object(gitlab_reviewer, "query_ollama", return_value=hostile),
        patch("urllib.request.urlopen", side_effect=gitlab),
    ):
        assert gitlab_reviewer.poll_once(set()) == 1
    body = gitlab.posted[0]
    assert not any(line.lstrip().startswith("/") for line in body.splitlines())
    assert "\\/approve" in body and "\\/merge" in body
    assert "See src/app.py for details." in body


def test_reviewer_ignores_marker_posted_by_another_account() -> None:
    """An MR author must not be able to suppress the review by typing the hidden marker."""
    marker = gitlab_reviewer.REVIEW_MARKER_TEMPLATE.format(sha="aaa111")
    forged = [{"body": f"lgtm\n{marker}", "author": {"id": MR_AUTHOR_ID}}, {"body": marker}]
    gitlab = FakeGitLab([{"project_id": 1, "iid": 10, "sha": "aaa111"}], existing_notes=forged)
    reviewed: set[gitlab_reviewer.ReviewKey] = set()
    with (
        patch.object(gitlab_reviewer, "GITLAB_TOKEN", "fake-token"),
        patch.object(gitlab_reviewer, "query_ollama", return_value="Looks fine."),
        patch("urllib.request.urlopen", side_effect=gitlab),
    ):
        assert gitlab_reviewer.poll_once(reviewed) == 1
        assert gitlab_reviewer.poll_once(reviewed) == 0
        # The reviewer's own note is still honoured after a restart, and the identity
        # was resolved exactly once.
        assert gitlab_reviewer.poll_once(set()) == 0
    assert len(gitlab.posted) == 1
    assert gitlab.user_lookups == 1


def test_reviewer_trusts_no_remote_marker_when_identity_unresolved() -> None:
    marker = gitlab_reviewer.REVIEW_MARKER_TEMPLATE.format(sha="aaa111")
    gitlab = FakeGitLab(
        [{"project_id": 1, "iid": 10, "sha": "aaa111"}],
        existing_notes=[{"body": marker, "author": {"id": REVIEWER_ID}}],
        user_lookup_fails=True,
    )
    reviewed: set[gitlab_reviewer.ReviewKey] = set()
    with (
        patch.object(gitlab_reviewer, "GITLAB_TOKEN", "fake-token"),
        patch.object(gitlab_reviewer, "query_ollama", return_value="Looks fine."),
        patch("urllib.request.urlopen", side_effect=gitlab),
    ):
        # Identity unknown: even a genuine-looking marker is not trusted...
        assert gitlab_reviewer.poll_once(reviewed) == 1
        # ...and the in-memory set alone prevents a duplicate.
        assert gitlab_reviewer.poll_once(reviewed) == 0
    assert len(gitlab.posted) == 1


# --- Sentinel copilot (findings 4, 14) --------------------------------------------------------


def _run_copilot(sequence: list[dict[str, float]], persisted_mode: str | None = None) -> list[str]:
    modes: list[str] = []
    feed = iter(sequence)

    def telemetry() -> dict[str, float]:
        try:
            return next(feed)
        except StopIteration:
            raise KeyboardInterrupt from None

    with (
        patch.object(sentinel_copilot, "get_telemetry", side_effect=telemetry),
        patch.object(sentinel_copilot, "set_mode", side_effect=modes.append),
        patch.object(sentinel_copilot, "read_persisted_mode", return_value=persisted_mode),
        patch.object(sentinel_copilot, "get_solar_forecast", return_value=800.0),
        patch("time.sleep", return_value=None),
        pytest.raises(KeyboardInterrupt),
    ):
        sentinel_copilot.run_copilot()
    return modes


def test_sentinel_holds_load_shedding_when_telemetry_fails(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    modes = _run_copilot(
        [
            {"sovereign_battery_soc_percent": 12.0, "sovereign_solar_pv_power_watts": 0.0},
            {},  # exporter unreachable
            {"sovereign_solar_pv_power_watts": 1500.0},  # SoC series missing
            {"sovereign_battery_soc_percent": float("nan"), "sovereign_solar_pv_power_watts": 1500.0},
        ]
    )
    # Entered ECO once and never left it on missing data (old code: defaulted to 85 % / 1000 W -> NORMAL)
    assert modes == ["ECO_PRESERVATION"]
    assert "holding mode ECO_PRESERVATION" in caplog.text


def _run_copilot_with_mode_file(sequence: list[dict[str, float]]) -> None:
    """Like _run_copilot() but with the real mode-file read and write."""
    feed = iter(sequence)

    def telemetry() -> dict[str, float]:
        try:
            return next(feed)
        except StopIteration:
            raise KeyboardInterrupt from None

    with (
        patch.object(sentinel_copilot, "get_telemetry", side_effect=telemetry),
        patch.object(sentinel_copilot, "get_solar_forecast", return_value=800.0),
        patch("time.sleep", return_value=None),
        pytest.raises(KeyboardInterrupt),
    ):
        sentinel_copilot.run_copilot()


def test_sentinel_restart_clears_stale_eco_mode_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restart after an ECO period: the file must not keep saying ECO_PRESERVATION forever."""
    mode_file = tmp_path / "shared" / "sovereign_mode"
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_file))
    mode_file.parent.mkdir()
    mode_file.write_text("ECO_PRESERVATION\n", encoding="utf-8")
    assert sentinel_copilot.read_persisted_mode() == "ECO_PRESERVATION"

    healthy = {"sovereign_battery_soc_percent": 60.0, "sovereign_solar_pv_power_watts": 500.0}
    _run_copilot_with_mode_file([healthy])

    assert mode_file.read_text(encoding="utf-8") == "NORMAL"
    assert knowledge_indexer.get_current_mode() == "NORMAL"


def test_sentinel_resumes_persisted_mode_while_telemetry_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    mode_file = tmp_path / "sovereign_mode"
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_file))
    mode_file.write_text("ECO_PRESERVATION", encoding="utf-8")

    _run_copilot_with_mode_file([{}])  # exporter unreachable right after the restart

    assert "holding mode ECO_PRESERVATION" in caplog.text
    assert mode_file.read_text(encoding="utf-8") == "ECO_PRESERVATION"


def test_sentinel_writes_mode_on_first_evaluation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    healthy = {"sovereign_battery_soc_percent": 60.0, "sovereign_solar_pv_power_watts": 500.0}
    # Written once on the first evaluation even though NORMAL equals the initial mode,
    # then not again while the mode is unchanged.
    assert _run_copilot([healthy, healthy, healthy]) == ["NORMAL"]
    assert _run_copilot([healthy, healthy], persisted_mode="ECO_PRESERVATION") == ["NORMAL"]

    # A file holding anything but a known mode is ignored and then overwritten.
    mode_file = tmp_path / "sovereign_mode"
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_file))
    assert sentinel_copilot.read_persisted_mode() is None  # absent
    mode_file.write_text("ECO_PRESERV", encoding="utf-8")  # truncated / garbage
    assert sentinel_copilot.read_persisted_mode() is None
    _run_copilot_with_mode_file([healthy])
    assert mode_file.read_text(encoding="utf-8") == "NORMAL"


def test_sentinel_unknown_solar_never_unlocks_surplus() -> None:
    assert sentinel_copilot.evaluate_mode({"sovereign_battery_soc_percent": 90.0}, 800.0) is not None
    mode, _ = sentinel_copilot.evaluate_mode({"sovereign_battery_soc_percent": 90.0}, 800.0)  # type: ignore[misc]
    assert mode == "NORMAL"
    assert sentinel_copilot.evaluate_mode({}, 800.0) is None


def test_mode_file_path_is_configurable_and_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mode_file = tmp_path / "shared" / "sovereign_mode"
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_file))

    assert knowledge_indexer.get_current_mode() == "NORMAL"
    sentinel_copilot.set_mode("ECO_PRESERVATION")
    assert mode_file.read_text(encoding="utf-8") == "ECO_PRESERVATION"
    assert knowledge_indexer.get_current_mode() == "ECO_PRESERVATION"


def test_solar_forecast_honours_utc_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    midnight_utc = 86400.0 * 20_000  # 00:00 UTC; +4 h look-ahead = 04:00 -> dark at offset 0
    with patch("time.time", return_value=midnight_utc):
        monkeypatch.setattr(sentinel_copilot, "SOLAR_UTC_OFFSET_HOURS", 0.0)
        assert sentinel_copilot.get_solar_forecast() == 0.0
        monkeypatch.setattr(sentinel_copilot, "SOLAR_UTC_OFFSET_HOURS", 9.0)  # 13:00 local solar time
        assert sentinel_copilot.get_solar_forecast() > 1000.0


# --- Knowledge indexer (finding 5) -------------------------------------------------------------


def test_index_file_returns_false_on_failure(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("hello sovereign world", encoding="utf-8")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        assert knowledge_indexer.index_file(str(doc)) is False

    with (
        patch.object(knowledge_indexer, "get_embedding", return_value=[0.1] * knowledge_indexer.EMBEDDING_DIM),
        patch("urllib.request.urlopen", return_value=_resp({})),
    ):
        assert knowledge_indexer.index_file(str(doc)) is True


def test_failed_file_is_retried_and_collection_re_ensured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "manual.txt").write_text("assembly instructions", encoding="utf-8")
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(tmp_path / "no_mode_file"))
    index_results = [False, True]
    index_calls: list[str] = []

    def fake_index(path: str) -> bool:
        index_calls.append(path)
        return index_results[len(index_calls) - 1]

    with (
        patch.object(knowledge_indexer, "WATCH_DIR", str(tmp_path)),
        patch.object(knowledge_indexer, "ensure_qdrant_collection", return_value=True) as ensure,
        patch.object(knowledge_indexer, "seed_indexed_sources", return_value=True),
        patch.object(knowledge_indexer, "index_file", side_effect=fake_index),
        patch("time.sleep", side_effect=[None, None, KeyboardInterrupt]),
        pytest.raises(KeyboardInterrupt),
    ):
        knowledge_indexer.run_worker()

    # 1st scan fails -> retried on the 2nd scan -> recorded, so not indexed a 3rd time
    assert len(index_calls) == 2
    # startup + once more after the failure
    assert ensure.call_count == 2


def test_embedding_dimension_is_never_mixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mode_file = tmp_path / "sovereign_mode"
    mode_file.write_text("ECO_PRESERVATION", encoding="utf-8")
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_file))

    # ECO mode must not switch to the 384-dim model against the 768-dim collection
    assert knowledge_indexer.get_current_model() == knowledge_indexer.DEFAULT_EMBEDDING_MODEL

    with patch("urllib.request.urlopen", return_value=_resp({"embedding": [0.1] * 384})):
        with pytest.raises(ValueError, match="384"):
            knowledge_indexer.get_embedding("chunk")
    # A response without an embedding is an error, not a zero vector
    with patch("urllib.request.urlopen", return_value=_resp({})):
        with pytest.raises(ValueError):
            knowledge_indexer.get_embedding("chunk")


def test_indexing_is_deferred_in_eco_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("text", encoding="utf-8")
    mode_file = tmp_path / "sovereign_mode"
    mode_file.write_text("ECO_PRESERVATION", encoding="utf-8")
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_file))

    with (
        patch.object(knowledge_indexer, "WATCH_DIR", str(docs)),
        patch.object(knowledge_indexer, "ensure_qdrant_collection", return_value=True),
        patch.object(knowledge_indexer, "index_file") as index,
        patch("time.sleep", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        knowledge_indexer.run_worker()
    index.assert_not_called()


# --- Technician notifier (finding 9) -----------------------------------------------------------


def _msg() -> TechnicianMessage:
    return TechnicianMessage(node_id="n1", event_type="TEST", severity=MessageSeverity.INFO, message="m")


def test_unimplemented_transports_report_failure(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    assert MQTTNotifier().send(_msg()) is False
    assert LoRaNotifier().send(_msg()) is False
    assert caplog.text.count("not implemented") == 2

    chain = TechnicianNotifierChain(notifiers=[MQTTNotifier(), LoRaNotifier()], node_id="n1")
    assert chain.notify("TEST", MessageSeverity.INFO, "m") == {"MQTTNotifier": False, "LoRaNotifier": False}


def test_file_notifier_accepts_bare_filename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert FileNotifier("notes.jsonl").send(_msg()) is True
    assert (tmp_path / "notes.jsonl").read_text(encoding="utf-8").count("\n") == 1


# --- Bootstrap provisioner (finding 10) --------------------------------------------------------


def test_bootstrap_dry_run_has_no_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = tmp_path / "notifications.jsonl"
    spool = tmp_path / "spool" / "dtn.db"
    spool.parent.mkdir()
    monkeypatch.setenv("DTN_DB_PATH", str(spool))
    chain = TechnicianNotifierChain(
        notifiers=[FileNotifier(str(log_file)), DTNNotifier(db_path=str(spool))], node_id="dry"
    )
    events: list[Any] = []
    from sovereign_dc.events import get_event_bus

    get_event_bus().subscribe("*", events.append)

    provisioner = BootstrapProvisioner(node_id="dry", notifier_chain=chain, dry_run=True)
    with patch("urllib.request.urlopen", side_effect=Exception("offline")):
        state = provisioner.run_all_phases()

    assert state.is_complete is True
    assert state.dry_run is True
    assert not log_file.exists()
    assert not spool.exists()  # no DTN bundles queued, spool not even created
    assert events == []


def test_bootstrap_reports_unknowns_instead_of_inventing_values() -> None:
    recorder = RecordingNotifier()
    provisioner = BootstrapProvisioner(
        node_id="honest", notifier_chain=TechnicianNotifierChain(notifiers=[recorder], node_id="honest")
    )
    with (
        patch("urllib.request.urlopen", side_effect=Exception("offline")),
        patch("shutil.which", return_value=None),
        patch("sovereign_dc.mesh.mesh_sync.check_peer_health", return_value=False),
    ):
        state = provisioner.run_all_phases()

    hw = state.hardware_info
    assert hw["gpu_detected"] is False and hw["gpu_count"] == 0
    assert not any("DGX" in str(g.get("name")) for g in hw["gpus"])
    assert hw["power"]["telemetry_available"] is False
    assert "battery_soc" not in hw["power"] and "solar_w" not in hw["power"]
    assert state.network_info["tier1_wireguard"] is False
    assert state.network_info["tier3_lora"] is False
    assert state.services_info["services_started"] == []
    assert state.services_info["docker_available"] is False
    assert state.sync_info["crdt_sync"] == "NOT_IMPLEMENTED"
    assert state.sync_info["backup_verified"] is None

    summary = state.phase_results["ready"]
    assert summary["status"] == "NODE_ONLINE_DEGRADED"
    assert state.is_nominal is False
    assert any("power" in item for item in summary["unverified"])
    event_types = [m.event_type for m in recorder.sent]
    assert "NODE_ONLINE_READY" not in event_types
    assert "NODE_ONLINE_DEGRADED" in event_types
    assert "HARDWARE_OK" not in event_types and "SYNC_COMPLETE" not in event_types


def test_bootstrap_ready_only_when_everything_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "dtn.db"))
    recorder = RecordingNotifier()
    provisioner = BootstrapProvisioner(
        node_id="ok", notifier_chain=TechnicianNotifierChain(notifiers=[recorder], node_id="ok")
    )

    def urlopen(req: Any, timeout: float = 0) -> MagicMock:
        if req.full_url.endswith("/metrics"):
            return _resp(
                b"sovereign_telemetry_simulated 1\nsovereign_battery_soc_percent 91.0\n"
                b"sovereign_solar_pv_power_watts 800.0\n"
            )
        return _resp({})

    def run(cmd: list[str], **_: Any) -> MagicMock:
        out = "NVIDIA Test GPU, 16384\n" if cmd[0] == "nvidia-smi" else "sovereign_ollama\nsovereign_qdrant\n"
        return MagicMock(returncode=0, stdout=out)

    with (
        patch("urllib.request.urlopen", side_effect=urlopen),
        patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"),
        patch("subprocess.run", side_effect=run),
        patch("sovereign_dc.mesh.mesh_sync.check_peer_health", return_value=True),
    ):
        state = provisioner.run_all_phases()

    summary = state.phase_results["ready"]
    assert summary["unverified"] == []
    assert summary["status"] == "NODE_ONLINE_READY"
    assert state.is_nominal is True
    assert state.hardware_info["power"]["battery_soc"] == 91.0
    assert state.hardware_info["power"]["simulated"] is True  # simulated source is flagged
    assert state.services_info["services_started"] == ["sovereign_ollama", "sovereign_qdrant"]
    assert summary["not_implemented"] == ["crdt_sync", "backup_verification"]


def test_watchdog_notifies_only_on_state_change() -> None:
    recorder = RecordingNotifier()
    provisioner = BootstrapProvisioner(
        node_id="wd", notifier_chain=TechnicianNotifierChain(notifiers=[recorder], node_id="wd")
    )
    reachable = {"up": False}
    with (
        patch("urllib.request.urlopen", side_effect=Exception("offline")),
        patch("shutil.which", return_value=None),
        patch("sovereign_dc.mesh.mesh_sync.check_peer_health", side_effect=lambda peer: reachable["up"]),
    ):
        for _ in range(5):  # five identical watchdog polls
            provisioner.phase_1_discovery()
            provisioner.phase_2_network()
        assert [m.event_type for m in recorder.sent] == ["HARDWARE_UNVERIFIED", "NETWORK_DEGRADED"]

        reachable["up"] = True  # state change -> exactly one new notification
        provisioner.phase_1_discovery()
        provisioner.phase_2_network()
        provisioner.phase_2_network()
    assert [m.event_type for m in recorder.sent] == ["HARDWARE_UNVERIFIED", "NETWORK_DEGRADED", "NETWORK_UP"]


# --- software/telemetry/power_exporter.py ---------------------------------------------------------


def _load_power_exporter(monkeypatch: pytest.MonkeyPatch, simulate: str, offset: str = "0") -> ModuleType:
    monkeypatch.setenv("SIMULATE_POWER_DATA", simulate)
    monkeypatch.setenv("SOLAR_UTC_OFFSET_HOURS", offset)
    path = ROOT / "software" / "telemetry" / "power_exporter.py"
    spec = importlib.util.spec_from_file_location(f"power_exporter_{simulate}_{offset}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _series(text: str) -> dict[str, float]:
    return {ln.split()[0]: float(ln.split()[1]) for ln in text.splitlines() if ln and not ln.startswith("#")}


def test_power_exporter_non_simulated_mode_emits_no_fabricated_gauges(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    exporter = _load_power_exporter(monkeypatch, "false")
    series = _series(exporter.get_telemetry_metrics())
    assert series == {"sovereign_telemetry_simulated": 0.0, "sovereign_telemetry_source_up": 0.0}
    exporter.get_telemetry_metrics()
    assert caplog.text.count("ESP32_BRIDGE_URL is not set") == 1  # logged once


def test_power_exporter_simulated_mode_is_labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = _load_power_exporter(monkeypatch, "true")
    series = _series(exporter.get_telemetry_metrics())
    assert series["sovereign_telemetry_simulated"] == 1.0
    assert series["sovereign_telemetry_source_up"] == 1.0
    assert "sovereign_battery_soc_percent" in series


def test_power_exporter_solar_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    midnight_utc = 86400.0 * 20_000
    dark = _load_power_exporter(monkeypatch, "true", "0")
    sunny = _load_power_exporter(monkeypatch, "true", "12")
    with patch("time.time", return_value=midnight_utc):
        assert _series(dark.get_telemetry_metrics())["sovereign_solar_pv_power_watts"] == 0.0
        assert _series(sunny.get_telemetry_metrics())["sovereign_solar_pv_power_watts"] > 500.0


def test_software_python_tree_compiles() -> None:
    for path in (ROOT / "software").rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


# --- Knowledge indexer: per-source reconciliation and collision-resistant ids -------------------


class FakeQdrant:
    """In-memory stand-in for the Qdrant REST calls the indexer makes (upsert, delete, scroll)."""

    def __init__(self) -> None:
        self.points: dict[Any, dict[str, Any]] = {}
        self.fail_delete = False
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _matches(point: dict[str, Any], point_filter: dict[str, Any]) -> bool:
        for cond in point_filter.get("must", []):
            if point["payload"].get(cond["key"]) != cond["match"]["value"]:
                return False
        return not any(point["id"] in cond.get("has_id", []) for cond in point_filter.get("must_not", []))

    def __call__(self, req: Any, timeout: float = 0) -> MagicMock:
        url, method = req.full_url, req.get_method()
        body = json.loads(req.data) if req.data else {}
        self.calls.append((method, url.split("/collections/", 1)[-1]))
        if "/points/delete" in url:
            if self.fail_delete:
                raise urllib.error.URLError("delete refused")
            assert "wait=true" in url
            self.points = {k: v for k, v in self.points.items() if not self._matches(v, body["filter"])}
            return _resp({"result": {"status": "completed"}})
        if "/points/scroll" in url:
            assert body["with_vector"] is False
            start = int(body.get("offset") or 0)
            page = list(self.points.values())[start : start + body["limit"]]
            nxt = start + body["limit"] if start + body["limit"] < len(self.points) else None
            return _resp(
                {
                    "result": {
                        "points": [{"id": p["id"], "payload": p["payload"]} for p in page],
                        "next_page_offset": nxt,
                    }
                }
            )
        if method == "PUT" and "/points" in url:
            for point in body["points"]:
                self.points[point["id"]] = point
            return _resp({"result": {"status": "completed"}})
        return _resp({})

    def texts(self, path: Path | str) -> list[str]:
        own = [p["payload"] for p in self.points.values() if p["payload"]["path"] == str(path)]
        return [p["text"] for p in sorted(own, key=lambda p: p["chunk_index"])]


@pytest.fixture
def qdrant() -> Any:
    fake = FakeQdrant()
    with (
        patch.object(knowledge_indexer, "get_embedding", return_value=[0.1] * knowledge_indexer.EMBEDDING_DIM),
        patch("urllib.request.urlopen", side_effect=fake),
    ):
        yield fake


def _words(n: int, tag: str = "w") -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


def test_reindexing_a_shortened_document_removes_stale_chunks(tmp_path: Path, qdrant: FakeQdrant) -> None:
    doc = tmp_path / "runbook.md"
    other = tmp_path / "other.md"
    other.write_text("unrelated document", encoding="utf-8")
    doc.write_text(_words(1200, "old"), encoding="utf-8")
    assert knowledge_indexer.index_file(str(other)) is True
    assert knowledge_indexer.index_file(str(doc)) is True
    assert len(qdrant.texts(doc)) == 3

    doc.write_text("corrected short procedure", encoding="utf-8")
    assert knowledge_indexer.index_file(str(doc)) is True
    assert qdrant.texts(doc) == ["corrected short procedure"]  # was: 3 points, two of them obsolete
    assert qdrant.texts(other) == ["unrelated document"]

    doc.write_text("", encoding="utf-8")
    assert knowledge_indexer.index_file(str(doc)) is True
    assert qdrant.texts(doc) == []  # shrink-to-zero withdraws the document
    assert qdrant.texts(other) == ["unrelated document"]


def test_point_ids_are_collision_resistant_uuids(tmp_path: Path, qdrant: FakeQdrant) -> None:
    import uuid

    ids = {knowledge_indexer.chunk_point_id(f"/data/documents/doc-{d}.md", c) for d in range(2000) for c in range(5)}
    assert len(ids) == 10_000
    for point_id in list(ids)[:50]:
        assert str(uuid.UUID(point_id)) == point_id  # a UUID string, which Qdrant accepts as an id
    # Path and chunk index are separated unambiguously ("a-1" + 0 vs "a" + "1-0" collided before).
    assert knowledge_indexer.chunk_point_id("a-1", 0) != knowledge_indexer.chunk_point_id("a", 10)
    assert knowledge_indexer.chunk_point_id("a", 1) == knowledge_indexer.chunk_point_id("a", 1)

    doc = tmp_path / "doc.md"
    doc.write_text("hello", encoding="utf-8")
    assert knowledge_indexer.index_file(str(doc)) is True
    (point,) = qdrant.points.values()
    assert point["id"] == knowledge_indexer.chunk_point_id(str(doc), 0)
    assert point["payload"]["path"] == str(doc) and point["payload"]["source"] == "doc.md"


def test_reindexing_replaces_points_stored_under_legacy_integer_ids(tmp_path: Path, qdrant: FakeQdrant) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("current text", encoding="utf-8")
    for idx in range(2):  # as written by the release that used 32-bit md5 ids
        qdrant.points[1000 + idx] = {"id": 1000 + idx, "payload": {"path": str(doc), "chunk_index": idx, "text": "old"}}
    qdrant.points[7] = {"id": 7, "payload": {"path": str(tmp_path / "kept.md"), "chunk_index": 0, "text": "kept"}}

    assert knowledge_indexer.index_file(str(doc)) is True
    assert qdrant.texts(doc) == ["current text"]
    assert set(qdrant.points) == {7, knowledge_indexer.chunk_point_id(str(doc), 0)}


def test_failed_upsert_or_delete_keeps_old_version_and_reports_failure(tmp_path: Path, qdrant: FakeQdrant) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(_words(600), encoding="utf-8")
    assert knowledge_indexer.index_file(str(doc)) is True
    before = dict(qdrant.points)

    doc.write_text("short", encoding="utf-8")
    with patch.object(knowledge_indexer, "get_embedding", side_effect=ValueError("ollama down")):
        assert knowledge_indexer.index_file(str(doc)) is False
    assert qdrant.points == before  # nothing deleted when the new version could not be stored

    qdrant.fail_delete = True
    assert knowledge_indexer.index_file(str(doc)) is False  # stale chunk still there: must be retried
    qdrant.fail_delete = False
    assert knowledge_indexer.index_file(str(doc)) is True
    assert qdrant.texts(doc) == ["short"]


def test_scan_removes_points_of_deleted_documents(
    tmp_path: Path, qdrant: FakeQdrant, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    (docs / "sub").mkdir(parents=True)
    keep, gone = docs / "keep.md", docs / "sub" / "gone.txt"
    keep.write_text("keep me", encoding="utf-8")
    gone.write_text("withdrawn instructions", encoding="utf-8")
    outside = tmp_path / "elsewhere.md"
    qdrant.points["x"] = {"id": "x", "payload": {"path": str(outside), "chunk_index": 0, "text": "foreign"}}
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))

    indexed: set[tuple[str, float]] = set()
    assert knowledge_indexer.scan_and_index(indexed) == (2, 0)
    assert qdrant.texts(gone) == ["withdrawn instructions"]

    gone.unlink()
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert qdrant.texts(gone) == []
    assert qdrant.texts(keep) == ["keep me"]
    assert {p for p, _ in indexed} == {str(keep)}
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)  # idempotent
    assert qdrant.texts(outside) == ["foreign"]  # points outside the watch directory are not ours to delete

    # A failed removal is reported and retried.
    keep.unlink()
    qdrant.fail_delete = True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert qdrant.texts(keep) == ["keep me"]
    qdrant.fail_delete = False
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert qdrant.texts(keep) == [] and indexed == set()


def test_documents_deleted_while_the_worker_was_down_are_removed_after_restart(
    tmp_path: Path, qdrant: FakeQdrant, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    alive, deleted = docs / "alive.md", docs / "deleted.md"
    alive.write_text("still here", encoding="utf-8")
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "SCROLL_PAGE_SIZE", 2)  # force pagination
    for idx in range(3):  # left behind by a previous run, under legacy ids
        qdrant.points[idx] = {"id": idx, "payload": {"path": str(deleted), "chunk_index": idx, "text": "stale"}}
    qdrant.points[10] = {"id": 10, "payload": {"path": str(alive), "chunk_index": 0, "text": "old version"}}

    indexed: set[tuple[str, float]] = set()  # a freshly started worker knows nothing
    assert knowledge_indexer.seed_indexed_sources(indexed) is True
    assert {p for p, _ in indexed} == {str(alive), str(deleted)}
    assert knowledge_indexer.scan_and_index(indexed) == (2, 0)
    assert qdrant.texts(deleted) == []
    assert qdrant.texts(alive) == ["still here"]
    assert set(qdrant.points) == {knowledge_indexer.chunk_point_id(str(alive), 0)}


def test_missing_or_unreadable_watch_dir_never_purges_the_index(
    tmp_path: Path, qdrant: FakeQdrant, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "a.md"
    doc.write_text("content", encoding="utf-8")
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    indexed: set[tuple[str, float]] = set()
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)

    # The volume is not mounted: the directory is gone, which is not "all documents were deleted".
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(tmp_path / "not-mounted" / "docs"))
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)
    assert qdrant.texts(doc) == ["content"]

    # A listing error anywhere under the watch directory also suspends removal for that scan.
    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))

    def broken_walk(top: str, onerror: Any = None) -> Any:
        onerror(PermissionError(13, "denied", top))
        return iter(())

    with patch("os.walk", side_effect=broken_walk):
        assert knowledge_indexer.scan_and_index(indexed) == (0, 0)
    assert qdrant.texts(doc) == ["content"] and len(indexed) == 1


def test_seeding_failure_is_reported_so_the_worker_retries(tmp_path: Path) -> None:
    indexed: set[tuple[str, float]] = set()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        assert knowledge_indexer.seed_indexed_sources(indexed) is False
    assert indexed == set()


# --- Private state directory instead of shared /tmp (mode file, alert log, DTN spool) -----------


def _no_path_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    state_dir = tmp_path / "state"
    for name in ("SOVEREIGN_MODE_FILE", "SOVEREIGN_LOG_DIR", "LOG_DIR", "DTN_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    return state_dir


@pytest.mark.parametrize("module", [sentinel_copilot, knowledge_indexer])
def test_mode_file_defaults_to_private_state_dir(
    module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = _no_path_overrides(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "MODE_FILE", "")
    assert module.mode_file_path() == str(state_dir / "sovereign_mode")
    assert (state_dir.stat().st_mode & 0o777) == 0o700

    # The environment variable (set by the container deployment) stays authoritative.
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", "/run/sovereign/mode")
    assert module.mode_file_path() == "/run/sovereign/mode"


def test_sentinel_and_indexer_share_the_default_mode_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = _no_path_overrides(monkeypatch, tmp_path)
    monkeypatch.setattr(sentinel_copilot, "MODE_FILE", "")
    monkeypatch.setattr(knowledge_indexer, "MODE_FILE", "")
    assert knowledge_indexer.get_current_mode() == "NORMAL"
    sentinel_copilot.set_mode("ECO_PRESERVATION")
    assert (state_dir / "sovereign_mode").read_text(encoding="utf-8") == "ECO_PRESERVATION"
    assert sentinel_copilot.read_persisted_mode() == "ECO_PRESERVATION"
    assert knowledge_indexer.get_current_mode() == "ECO_PRESERVATION"


def test_technician_notifier_defaults_live_in_private_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = _no_path_overrides(monkeypatch, tmp_path)
    file_notifier = FileNotifier()
    dtn_notifier = DTNNotifier()
    assert file_notifier.log_path == str(state_dir / "sovereign_logs" / "technician_notifications.jsonl")
    assert dtn_notifier.db_path == str(state_dir / "dtn_spool.db")

    assert file_notifier.send(_msg()) is True
    assert dtn_notifier.send(_msg()) is True
    assert (state_dir / "dtn_spool.db").is_file()
    assert (state_dir.stat().st_mode & 0o777) == 0o700
    assert ((state_dir / "sovereign_logs").stat().st_mode & 0o777) == 0o700

    # Explicit configuration still wins.
    monkeypatch.setenv("SOVEREIGN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "spool.db"))
    assert FileNotifier().log_path == str(tmp_path / "logs" / "technician_notifications.jsonl")
    assert DTNNotifier().db_path == str(tmp_path / "spool.db")
    assert DTNNotifier(db_path=str(tmp_path / "explicit.db")).db_path == str(tmp_path / "explicit.db")


@pytest.mark.parametrize("name", ["knowledge_indexer.py", "sentinel_copilot.py", "gitlab_reviewer.py"])
def test_container_agent_scripts_import_only_the_standard_library(name: str) -> None:
    """software/agents/*.py run from a bare python image with only that directory mounted."""
    import ast
    import sys

    tree = ast.parse((ROOT / "software" / "agents" / name).read_text(encoding="utf-8"))
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    modules = {a.name.split(".")[0] for n in top_level if isinstance(n, ast.Import) for a in n.names}
    modules |= {(n.module or "").split(".")[0] for n in top_level if isinstance(n, ast.ImportFrom)}
    assert modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names)
