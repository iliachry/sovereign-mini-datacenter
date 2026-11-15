"""Regression tests for tri-council round 3: findings 3, 6, 7, 9, 10, 11 and 12.

Dashboard requests go through the real handler on a loopback server; GitLab, Qdrant and
Ollama are recording fakes. All state lives under ``tmp_path`` (see ``conftest.py``).
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import threading
import urllib.error
import urllib.parse
from argparse import Namespace
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sovereign_dc import cli
from sovereign_dc.agents import gitlab_reviewer, knowledge_indexer, sentinel_copilot
from sovereign_dc.enterprise.sdk import SMDCClient
from sovereign_dc.web import dashboard
from sovereign_dc.web.dashboard import DashboardHandler

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "tok-r3"  # noqa: S105 - test value


# ── 3. DNS rebinding: the Host header decides whether a loopback request is local ─────────


def _serve(host: str = "127.0.0.1", bind_name: str | None = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, 0), DashboardHandler)
    server.daemon_threads = True
    if bind_name is not None:
        server.smdc_bind_host = bind_name  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def node() -> Iterator[int]:
    server = _serve()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def wildcard_node() -> Iterator[int]:
    server = _serve("0.0.0.0")  # noqa: S104 - reached over loopback only
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _request(
    port: int,
    method: str,
    path: str,
    host: str | None,
    token: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, bytes]:
    """One request over loopback with an explicit ``Host`` header (None = send none at all)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            conn.putheader("Host", host)
        if token is not None:
            conn.putheader("Authorization", f"Bearer {token}")
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        if method == "POST":
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(raw)))
        conn.endheaders(raw if method == "POST" else None)
        response = conn.getresponse()
        streaming = response.getheader("Content-Type", "").startswith("text/event-stream")
        # An SSE stream never ends: its status is all these tests need.
        return response.status, b"" if streaming else response.read()
    finally:
        conn.close()


REBIND_HOST = "rebind.attacker.example:8080"


def test_rebinding_host_gets_no_token_exempt_read(node: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", TOKEN)
    status, raw = _request(node, "GET", "/api/status", REBIND_HOST)
    assert status == 401  # was 200: loopback peer + attacker's name in Host
    assert b"battery_soc" not in raw
    assert _request(node, "GET", "/api/metaverse/status", REBIND_HOST)[0] == 401
    assert _request(node, "GET", "/api/telemetry/stream", REBIND_HOST)[0] == 401


def test_rebinding_host_is_refused_without_a_token_too(node: int) -> None:
    status, raw = _request(node, "GET", "/api/status", REBIND_HOST)
    assert status == 421
    assert "SMDC_DASHBOARD_ALLOWED_HOSTS" in json.loads(raw)["error"]
    assert b"battery_soc" not in raw
    # The tokenless local control path is closed to it as well.
    status, _ = _request(node, "POST", "/api/control/rack-door", REBIND_HOST, body={"open": True})
    assert status == 403
    assert dashboard._HARDWARE_STATE["door_open"] is False


@pytest.mark.parametrize("host", ["localhost:8080", "127.0.0.1:8080", "127.8.9.1", "[::1]:8080", "LOCALHOST.", None])
def test_loopback_host_names_are_still_local(node: int, monkeypatch: pytest.MonkeyPatch, host: str | None) -> None:
    monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", TOKEN)
    assert _request(node, "GET", "/api/status", host)[0] == 200
    monkeypatch.delenv("SMDC_DASHBOARD_TOKEN")
    assert _request(node, "POST", "/api/control/rack-door", host, body={"open": True})[0] == 200


def test_token_keeps_working_with_any_host_name(node: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", TOKEN)
    for host in ("smdc-node-7.example.org", "dash.example.org:8443", REBIND_HOST):
        status, raw = _request(node, "GET", "/api/status", host, token=TOKEN)
        assert status == 200 and "power" in json.loads(raw)
        assert _request(node, "GET", "/api/status", host, token="wrong")[0] == 401  # noqa: S106
    assert _request(node, "GET", f"/api/telemetry/stream?token={TOKEN}", "smdc-node-7.example.org")[0] == 200
    status, _ = _request(node, "POST", "/api/control/rack-door", "smdc-node-7.example.org", TOKEN, {"open": True})
    assert status == 200


def test_health_and_page_stay_reachable_under_any_host(node: int, monkeypatch: pytest.MonkeyPatch) -> None:
    for token in (None, TOKEN):
        if token:
            monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", token)
        for path in ("/health", "/healthz", "/"):
            assert _request(node, "GET", path, REBIND_HOST)[0] == 200


def test_allowed_hosts_variable_makes_a_name_local(node: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMDC_DASHBOARD_ALLOWED_HOSTS", "smdc.lan, Node1.Example.org:8080")
    assert _request(node, "GET", "/api/status", "smdc.lan:8080")[0] == 200
    assert _request(node, "POST", "/api/control/rack-door", "node1.example.org", body={"open": True})[0] == 200
    monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", TOKEN)
    assert _request(node, "GET", "/api/status", "SMDC.LAN")[0] == 200  # local: no token needed
    assert _request(node, "GET", "/api/status", "other.lan")[0] == 401
    assert _request(node, "GET", "/api/status", "smdc.lan.attacker.example")[0] == 401


@pytest.mark.parametrize("host", ["bad host", "a/b", "user@127.0.0.1", "127.0.0.1:notaport", "[::1"])
def test_malformed_host_is_never_local(node: int, monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", TOKEN)
    assert _request(node, "GET", "/api/status", host)[0] == 401
    monkeypatch.delenv("SMDC_DASHBOARD_TOKEN")
    assert _request(node, "GET", "/api/status", host)[0] == 421


def test_wildcard_bind_accepts_ip_literals_but_not_unknown_names(
    wildcard_node: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tokenless deployment reached by address: reads stay open (as documented), names do not.
    assert _request(wildcard_node, "GET", "/api/status", "192.168.1.50:8080")[0] == 200
    assert _request(wildcard_node, "GET", "/api/status", "[fd00::50]:8080")[0] == 200
    assert _request(wildcard_node, "GET", "/api/status", REBIND_HOST)[0] == 421
    # An IP literal is trusted, but it is not "local": no tokenless control, no token-exempt read.
    status, _ = _request(wildcard_node, "POST", "/api/control/rack-door", "192.168.1.50:8080", body={"open": True})
    assert status == 403
    monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", TOKEN)
    assert _request(wildcard_node, "GET", "/api/status", "192.168.1.50:8080")[0] == 401
    assert _request(wildcard_node, "GET", "/api/status", "192.168.1.50:8080", token=TOKEN)[0] == 200


def test_specific_bind_accepts_only_its_own_address_as_host() -> None:
    # Bound to one address (recorded by run_dashboard_server as smdc_bind_host).
    server = _serve(bind_name="192.168.1.50")
    try:
        port = server.server_address[1]
        assert _request(port, "GET", "/api/status", "192.168.1.50:8080")[0] == 200
        assert _request(port, "GET", "/api/status", "192.168.1.51:8080")[0] == 421
        assert _request(port, "GET", "/api/status", REBIND_HOST)[0] == 421
    finally:
        server.shutdown()
        server.server_close()
    # The default loopback bind: any non-loopback Host is untrusted, IP literal or not.
    server = _serve()
    try:
        assert _request(server.server_address[1], "GET", "/api/status", "192.168.1.50")[0] == 421
    finally:
        server.shutdown()
        server.server_close()


def test_run_dashboard_server_records_the_configured_bind_host(monkeypatch: pytest.MonkeyPatch) -> None:
    server = MagicMock()
    server.serve_forever.side_effect = KeyboardInterrupt
    with patch("sovereign_dc.web.dashboard.HTTPServer", return_value=server):
        dashboard.run_dashboard_server(port=8899, host="node1.example.org")
    assert server.smdc_bind_host == "node1.example.org"


def test_dashboard_variable_is_documented() -> None:
    for name in ("README.md", "AGENTS.md"):
        assert "SMDC_DASHBOARD_ALLOWED_HOSTS" in (ROOT / name).read_text(encoding="utf-8")


# ── 6. A failed later upsert batch must not leave untracked points behind ─────────────────


def _resp(body: Any, headers: dict[str, str] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status = 200
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.headers = headers or {}
    resp.__enter__.return_value = resp
    return resp


class FlakyQdrant:
    """In-memory Qdrant whose upserts and deletes can be made to fail."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}
        self.upserts = 0
        self.fail_upsert_number: int | None = None
        self.fail_delete = False
        self.deletes = 0

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
            if self.upserts == self.fail_upsert_number:
                raise urllib.error.URLError("upsert refused")
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
    monkeypatch.setattr(knowledge_indexer, "UPSERT_BATCH_SIZE", 2)
    monkeypatch.setattr(knowledge_indexer, "_cleanup_pending", set())
    monkeypatch.setattr(knowledge_indexer, "_reconcile_pending", set())
    with (
        patch.object(knowledge_indexer, "get_embedding", return_value=[0.1] * knowledge_indexer.EMBEDDING_DIM),
        patch("urllib.request.urlopen", side_effect=fake),
    ):
        yield fake


def _five_chunk_doc(tmp_path: Path) -> Path:
    doc = tmp_path / "docs" / "runbook.md"
    doc.write_text(" ".join(f"w{i}" for i in range(2000)), encoding="utf-8")  # 5 chunks = 3 batches of 2
    return doc


def test_partial_upsert_is_rolled_back_so_a_deleted_file_leaves_nothing(tmp_path: Path, qdrant: FlakyQdrant) -> None:
    doc = _five_chunk_doc(tmp_path)
    indexed: set[tuple[str, float]] = set()
    qdrant.fail_upsert_number = 2
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert indexed == set()
    assert qdrant.count(doc) == 0  # was: the first batch stayed, tracked by nothing
    assert knowledge_indexer.pending_cleanup_paths() == frozenset()

    doc.unlink()
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)
    assert qdrant.points == {}


def test_failed_rollback_is_retried_every_scan_even_after_the_file_is_gone(
    tmp_path: Path, qdrant: FlakyQdrant, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    doc = _five_chunk_doc(tmp_path)
    indexed: set[tuple[str, float]] = set()
    qdrant.fail_upsert_number = 2
    qdrant.fail_delete = True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    assert qdrant.count(doc) == 2  # partial document, could not be removed
    assert knowledge_indexer.pending_cleanup_paths() == frozenset({str(doc)})
    assert "NOT removed yet" in caplog.text

    doc.unlink()  # deleted before the next scan: nothing tracks it except the cleanup set
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)  # still failing: counted, retried
    assert qdrant.count(doc) == 2
    assert caplog.text.count("will retry every scan") == 1  # one message per failure streak

    qdrant.fail_delete = False
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)
    assert qdrant.points == {}
    assert knowledge_indexer.pending_cleanup_paths() == frozenset()


def test_pending_cleanup_is_resolved_by_a_successful_reindex(tmp_path: Path, qdrant: FlakyQdrant) -> None:
    doc = _five_chunk_doc(tmp_path)
    indexed: set[tuple[str, float]] = set()
    qdrant.fail_upsert_number = 2
    qdrant.fail_delete = True
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
    qdrant.fail_delete = False
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert qdrant.count(doc) == 5
    assert knowledge_indexer.pending_cleanup_paths() == frozenset()
    assert {path for path, _ in indexed} == {str(doc)}


def test_first_batch_failure_deletes_nothing_of_the_previous_version(tmp_path: Path, qdrant: FlakyQdrant) -> None:
    doc = _five_chunk_doc(tmp_path)
    assert knowledge_indexer.index_file(str(doc)) is True
    deletes = qdrant.deletes
    qdrant.fail_upsert_number = qdrant.upserts + 1
    doc.write_text("short", encoding="utf-8")
    assert knowledge_indexer.index_file(str(doc)) is False
    assert qdrant.count(doc) == 5 and qdrant.deletes == deletes  # nothing written, nothing rolled back


def test_agent_mirrors_carry_the_fixes() -> None:
    for name in ("knowledge_indexer.py", "sentinel_copilot.py", "gitlab_reviewer.py"):
        packaged = (ROOT / "src" / "sovereign_dc" / "agents" / name).read_bytes()
        assert (ROOT / "software" / "agents" / name).read_bytes() == packaged


# ── 7. A failed mode-file write is retried until it succeeds ──────────────────────────────

LOW = {"sovereign_battery_soc_percent": 12.0, "sovereign_solar_pv_power_watts": 0.0}


def _run_copilot(sequence: list[dict[str, float]], failing_replaces: int) -> list[int]:
    """Runs the sentinel over ``sequence`` with the first ``failing_replaces`` renames failing."""
    feed = iter(sequence)
    attempts: list[int] = []
    real_replace = sentinel_copilot.os.replace

    def telemetry() -> dict[str, float]:
        try:
            return next(feed)
        except StopIteration:
            raise KeyboardInterrupt from None

    def replace(src: str, dst: str) -> None:
        attempts.append(1)
        if len(attempts) <= failing_replaces:
            raise OSError(28, "No space left on device")
        real_replace(src, dst)

    with (
        patch.object(sentinel_copilot, "get_telemetry", side_effect=telemetry),
        patch.object(sentinel_copilot, "get_solar_forecast", return_value=800.0),
        patch.object(sentinel_copilot.os, "replace", side_effect=replace),
        patch("time.sleep", return_value=None),
        pytest.raises(KeyboardInterrupt),
    ):
        sentinel_copilot.run_copilot()
    return attempts


def test_sentinel_retries_the_mode_file_until_the_write_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    mode_file = tmp_path / "shared" / "sovereign_mode"
    mode_file.parent.mkdir()
    mode_file.write_text("NORMAL", encoding="utf-8")
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_file))

    attempts = _run_copilot([LOW, LOW, LOW, LOW], failing_replaces=2)
    assert len(attempts) == 3  # was 1: the failed write was never retried
    assert mode_file.read_text(encoding="utf-8") == "ECO_PRESERVATION"
    assert knowledge_indexer.get_current_mode() == "ECO_PRESERVATION"
    assert caplog.text.count("Failed to write mode state") == 1  # once per failure streak
    assert caplog.text.count("earlier write failures recovered") == 1


def test_sentinel_retries_the_write_while_telemetry_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mode_file = tmp_path / "sovereign_mode"
    mode_file.write_text("NORMAL", encoding="utf-8")
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_file))
    attempts = _run_copilot([LOW, {}, {}], failing_replaces=1)
    assert len(attempts) == 2
    assert mode_file.read_text(encoding="utf-8") == "ECO_PRESERVATION"


def test_sentinel_does_not_rewrite_the_file_once_it_is_in_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(tmp_path / "sovereign_mode"))
    assert len(_run_copilot([LOW, LOW, LOW, {}, LOW], failing_replaces=0)) == 1


def test_set_mode_reports_whether_the_file_was_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(tmp_path / "sovereign_mode"))
    assert sentinel_copilot.set_mode("ECO_PRESERVATION") is True
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(blocker / "sovereign_mode"))  # parent is a file
    assert sentinel_copilot.set_mode("NORMAL") is False
    assert sentinel_copilot.write_mode_file("NORMAL", log_failure=False) is False


# ── 9. SDK dynamic pricing prices the node's real power state ─────────────────────────────


def _client() -> SMDCClient:
    return SMDCClient(base_url="http://127.0.0.1:9")  # nothing listens: never the REST path


def test_default_pricing_reads_the_nodes_telemetry() -> None:
    client = _client()
    reading = {"battery_soc": 10.0, "solar_watts": 0.0, "source": "hal", "is_simulated": False}
    with patch.object(client, "get_telemetry", return_value=reading) as telemetry:
        quote = client.get_dynamic_pricing()
    telemetry.assert_called_once()
    assert quote["energy_multiplier"] == 3.0  # was 0.5: an invented 85 % / 850 W
    assert quote["status"] == "quoted" and quote["pricing_available"] is True
    assert quote["inputs"] == {
        "battery_soc": 10.0,
        "solar_watts": 0.0,
        "battery_soc_source": "telemetry:hal",
        "solar_watts_source": "telemetry:hal",
        "is_simulated": False,
    }


def test_pricing_without_telemetry_invents_no_multiplier() -> None:
    client = _client()
    unavailable = {"battery_soc": 0.0, "solar_watts": 0.0, "power_state": "UNKNOWN", "source": "unavailable"}
    with patch.object(client, "get_telemetry", return_value=unavailable):
        quote = client.get_dynamic_pricing()
    assert quote["status"] == "unavailable" and quote["pricing_available"] is False
    assert "energy_multiplier" not in quote and "final_unit_price" not in quote
    assert quote["inputs"]["battery_soc"] is None
    assert quote["inputs"]["battery_soc_source"] == "unavailable"


def test_pricing_arguments_override_telemetry_and_are_labelled() -> None:
    client = _client()
    with patch.object(client, "get_telemetry") as telemetry:
        quote = client.get_dynamic_pricing(soc=85.0, solar_w=850.0)
    telemetry.assert_not_called()
    assert quote["energy_multiplier"] == 0.5
    assert quote["inputs"]["battery_soc_source"] == "caller" and quote["inputs"]["solar_watts_source"] == "caller"

    # Only the SoC given, solar unreadable: priced as unknown, which never earns the discount.
    api_reading = {"battery_soc": 55.0, "solar_watts": None, "source": "api", "power": {"is_simulated": True}}
    with patch.object(client, "get_telemetry", return_value=api_reading):
        quote = client.get_dynamic_pricing(soc=90.0)
    assert quote["energy_multiplier"] == 1.0
    assert quote["inputs"] == {
        "battery_soc": 90.0,
        "solar_watts": None,
        "battery_soc_source": "caller",
        "solar_watts_source": "unavailable",
        "is_simulated": True,
    }


def test_pricing_error_is_reported_with_its_inputs() -> None:
    quote = _client().get_dynamic_pricing(soc=float("nan"), solar_w=100.0)
    assert quote["status"] == "error" and quote["pricing_available"] is False
    assert "energy_multiplier" not in quote and "base_compute_unit" not in quote


# ── 10. GitLab pagination ─────────────────────────────────────────────────────────────────

REVIEWER_ID = 99


class PagedGitLab:
    """A GitLab API fake that paginates lists (``page`` / ``per_page`` / ``X-Next-Page``)."""

    def __init__(self, mrs: list[dict[str, Any]], notes: list[dict[str, Any]] | None = None) -> None:
        self.mrs = mrs
        self.notes = list(notes or [])
        self.posted: list[str] = []
        self.pages_requested: list[tuple[str, int]] = []

    def _page(self, kind: str, items: list[Any], url: str) -> MagicMock:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        page = int(query.get("page", ["1"])[0])
        per_page = int(query.get("per_page", ["20"])[0])
        self.pages_requested.append((kind, page))
        start = (page - 1) * per_page
        more = start + per_page < len(items)
        return _resp(items[start : start + per_page], {"X-Next-Page": str(page + 1) if more else ""})

    def __call__(self, req: Any, timeout: float = 0) -> MagicMock:
        url = req.full_url
        if req.get_method() == "POST":
            self.posted.append(url)
            return _resp({})
        path = urllib.parse.urlsplit(url).path
        if path.endswith("/user"):
            return _resp({"id": REVIEWER_ID})
        if path.endswith("/changes"):
            return _resp({"changes": [{"diff": "+print('hello')"}]})
        if path.endswith("/notes"):
            return _page_notes(self, url)
        return self._page("mrs", self.mrs, url)


def _page_notes(fake: PagedGitLab, url: str) -> MagicMock:
    return fake._page("notes", fake.notes, url)


@pytest.fixture(autouse=True)
def _reset_reviewer_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gitlab_reviewer, "_reviewer_user_id", None)


def test_reviewer_follows_merge_request_pagination() -> None:
    mrs = [{"project_id": 1, "iid": i, "sha": f"sha{i}"} for i in range(1, 102)]
    gitlab = PagedGitLab(mrs)
    reviewed: set[gitlab_reviewer.ReviewKey] = set()
    with (
        patch.object(gitlab_reviewer, "GITLAB_TOKEN", "fake-token"),
        patch.object(gitlab_reviewer, "query_ollama", return_value="Looks fine."),
        patch("urllib.request.urlopen", side_effect=gitlab),
    ):
        assert gitlab_reviewer.poll_once(reviewed) == 101  # was 100: the oldest MR was never fetched
        assert gitlab_reviewer.poll_once(reviewed) == 0
    assert (1, 101, "sha101") in reviewed
    assert ("mrs", 2) in gitlab.pages_requested


def test_review_marker_beyond_the_first_notes_page_is_found() -> None:
    marker = gitlab_reviewer.REVIEW_MARKER_TEMPLATE.format(sha="aaa111")
    notes = [{"body": f"comment {i}", "author": {"id": 7}} for i in range(150)]
    notes.append({"body": f"old review\n{marker}", "author": {"id": REVIEWER_ID}})  # note 151, on page 2
    gitlab = PagedGitLab([{"project_id": 1, "iid": 10, "sha": "aaa111"}], notes)
    with (
        patch.object(gitlab_reviewer, "GITLAB_TOKEN", "fake-token"),
        patch.object(gitlab_reviewer, "query_ollama", return_value="Looks fine.") as llm,
        patch("urllib.request.urlopen", side_effect=gitlab),
    ):
        assert gitlab_reviewer.poll_once(set()) == 0  # was 1: a duplicate review
    llm.assert_not_called()
    assert gitlab.posted == []
    assert ("notes", 2) in gitlab.pages_requested


def test_pagination_is_capped(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(gitlab_reviewer, "PAGE_SIZE", 2)
    gitlab = PagedGitLab([{"project_id": 1, "iid": i, "sha": "s"} for i in range(1, 12)])
    with (
        patch.object(gitlab_reviewer, "GITLAB_TOKEN", "fake-token"),
        patch("urllib.request.urlopen", side_effect=gitlab),
    ):
        items = gitlab_reviewer._gitlab_get_all(f"{gitlab_reviewer.GITLAB_URL}/merge_requests?state=opened", 3)
    assert [mr["iid"] for mr in items] == [1, 2, 3, 4, 5, 6]
    assert "more than 3 pages" in caplog.text
    # A next-page header that does not advance cannot loop forever.
    assert gitlab_reviewer._next_page({"X-Next-Page": "2"}, 2) is None
    assert gitlab_reviewer._next_page({"X-Next-Page": ""}, 1) is None
    assert gitlab_reviewer._next_page({"X-Next-Page": "3"}, 2) == 3
    assert gitlab_reviewer._with_query("http://g/api/v4/x?per_page=5&sort=desc", per_page=100, page=2) == (
        "http://g/api/v4/x?sort=desc&per_page=100&page=2"
    )


# ── 11. backup_enabled is a declaration of intent, and the tools say so ───────────────────

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _cli_output(capsys: pytest.CaptureFixture[str]) -> str:
    return " ".join(_ANSI.sub("", capsys.readouterr().out).split())


def test_app_validate_and_register_print_the_backup_notice(capsys: pytest.CaptureFixture[str]) -> None:
    apps = ROOT / "examples" / "enterprise_apps"
    assert cli.cmd_app_validate(Namespace(path=str(apps / "iot-edge-gateway"))) == 0
    out = _cli_output(capsys)
    assert "is NOT backed up automatically" in out
    assert "software/backup/volumes.conf" in out and "not off-site" in out

    assert cli.cmd_app_register(Namespace(path=str(apps / "iot-edge-gateway"))) == 0
    assert "is NOT backed up automatically" in _cli_output(capsys)

    # backup_enabled: false declares nothing, so there is nothing to warn about.
    assert cli.cmd_app_validate(Namespace(path=str(apps / "edge-vision-ai"))) == 0
    assert "backed up" not in _cli_output(capsys)


def test_onboarding_guide_does_not_promise_automatic_offsite_backups() -> None:
    guide = (ROOT / "ENTERPRISE_ONBOARDING.md").read_text(encoding="utf-8")
    assert "Included in Restic encrypted off-site snapshots" not in guide
    assert "does not back anything up" in guide and "software/backup/volumes.conf" in guide
    # The operator instruction uses the real format of the backup set.
    header = (ROOT / "software" / "backup" / "volumes.conf").read_text(encoding="utf-8")
    assert "key|name|class|paths|reason" in header and "key|name|class|paths|reason" in guide


# ── 12. Documents describe the DTN path as it is: digests, no signatures ──────────────────


def test_dtn_bundle_path_does_not_sign_and_no_document_says_it_does() -> None:
    dtn_sources = "".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "src" / "sovereign_dc" / "space").rglob("*.py")
    )
    assert "sign_bundle" not in dtn_sources and "verify_bundle" not in dtn_sources and "pqc" not in dtn_sources.lower()

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "BPSec Signed Bundles" not in readme
    assert "optional PQC signatures" not in agents
    assert "nothing in the DTN bundle/router path calls them" in agents
    for doc in (readme, agents, *(p.read_text(encoding="utf-8") for p in (ROOT / "docs" / "js").glob("*.js"))):
        assert not re.search(r"signed (DTN )?bundles? (are|is) (created|sent|queued)", doc, re.IGNORECASE)

    from sovereign_dc.mcp.prompts import get_mcp_prompts

    descriptions = " ".join(p.description for p in get_mcp_prompts())
    assert "cryptographically signed" not in descriptions
