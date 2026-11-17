"""Regression tests for tri-council review round 26.

1. ``StateChannel._promise_payload()`` signs an unambiguous, versioned canonical JSON array
   (``smdc-promise-v2``) and ``verify_promise()`` accepts legacy colon-delimited v1
   signatures only when fields match a strict non-overlapping grammar, preventing
   cross-field colon-splicing forgeries.
2. ``software/telemetry/load_shedder.sh`` serializes overlapping ``LOAD_SHED_ACTION=pause``
   runs with an atomic PID symlink lock across telemetry fetch, marker decisions and
   Docker actions, while reaping dead-PID locks and timing out cleanly on live locks.
3. MCP ``set_load_shedding`` result and schema descriptions state accurately that only
   apps paused solely by tier shedding are resumed and ``min_battery_soc`` floor pauses
   remain held when no ``battery_soc`` reading is published.
4. ``README.md``, ``AGENTS.md``, and ``COMMERCIALIZATION.md`` reflect the measured 92.2%
   coverage, and ``COMMERCIALIZATION.md`` qualifies LiFePO4 thermal stability accurately.
"""

from __future__ import annotations

import http.server
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from sovereign_dc.economy.ledger import StateChannel, StateChannelPromise
from sovereign_dc.economy.wallet import NodeWallet
from sovereign_dc.enterprise.manager import AppStatus
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, EventType, get_event_bus
from sovereign_dc.mcp import tools as mcp_tools

ROOT = Path(__file__).resolve().parent.parent


# --- Astra 26 #1: StateChannel unambiguous promise payload & strict legacy v1 fallback ---------


def test_state_channel_rejects_colon_spliced_cross_channel_amount_forgery() -> None:
    payer = NodeWallet.create(node_id="payer-r26")
    payee = NodeWallet.create(node_id="payee-r26")

    # V2 signing handles arbitrary channel IDs including colons without ambiguity.
    colon_sender = StateChannel("target:1:9.000000", payer.address, payee.address, 10.0)
    colon_receiver = StateChannel("target:1:9.000000", payer.address, payee.address, 10.0)
    promise = colon_sender.stream_micropayment(1.0, payer)
    assert colon_receiver.verify_promise(promise, payer.keypair.public_key_hex) is True

    # Reusing that v2 signature on another channel/amount/spliced timestamp must fail.
    victim = StateChannel("target", payer.address, payee.address, 10.0)
    forged_v2 = StateChannelPromise("target", 1, 9.0, f"1:1.000000:{promise.timestamp}", promise.signature)
    assert victim.verify_promise(forged_v2, payer.keypair.public_key_hex) is False
    assert victim.close() == (0.0, 10.0)

    # Even against a legacy v1 signature produced from raw colon-joined text,
    # ambiguous channel_ids with colons or spliced non-ISO timestamps are rejected.
    iso_ts = "2026-01-01T00:00:00+00:00"
    ambiguous_v1_sig = payer.sign_payload(f"target:1:9.000000:1:1.000000:{iso_ts}".encode())
    victim_v1 = StateChannel("target", payer.address, payee.address, 10.0)
    forged_v1 = StateChannelPromise("target", 1, 9.0, f"1:1.000000:{iso_ts}", ambiguous_v1_sig)
    assert victim_v1.verify_promise(forged_v1, payer.keypair.public_key_hex) is False
    assert victim_v1.close() == (0.0, 10.0)

    colon_v1_receiver = StateChannel("target:1:9.000000", payer.address, payee.address, 10.0)
    assert (
        colon_v1_receiver.verify_promise(
            StateChannelPromise("target:1:9.000000", 1, 1.0, iso_ts, ambiguous_v1_sig),
            payer.keypair.public_key_hex,
        )
        is False
    )

    # Clean legacy v1 signatures (colon-free channel_id + ISO-8601 timestamp) still verify.
    valid_v1_sig = payer.sign_payload(f"ch-legacy-1:1:1.500000:{iso_ts}".encode())
    legacy_receiver = StateChannel("ch-legacy-1", payer.address, payee.address, 10.0)
    assert (
        legacy_receiver.verify_promise(
            StateChannelPromise("ch-legacy-1", 1, 1.5, iso_ts, valid_v1_sig),
            payer.keypair.public_key_hex,
        )
        is True
    )
    assert legacy_receiver.close() == (1.5, 8.5)


def test_state_channel_input_validation_guards() -> None:
    payer = NodeWallet.create(node_id="payer-guards")
    payee = NodeWallet.create(node_id="payee-guards")
    iso_ts = "2026-01-01T00:00:00+00:00"

    with pytest.raises(ValueError, match="channel_id"):
        StateChannel("", payer.address, payee.address, 1.0)

    assert StateChannel._legacy_v1_promise_payload("ch-1", 0, 100, iso_ts) is None

    receiver = StateChannel("ch-1", payer.address, payee.address, 5.0)
    pub = payer.keypair.public_key_hex
    assert receiver.verify_promise(StateChannelPromise("ch-1", True, 1.0, iso_ts, "00"), pub) is False  # type: ignore[arg-type]
    assert receiver.verify_promise(StateChannelPromise("ch-1", 1, 10.0, iso_ts, "00"), pub) is False
    assert receiver.verify_promise(StateChannelPromise("ch-1", 1, 1.0, "", "00"), pub) is False
    assert receiver.verify_promise(StateChannelPromise("ch-1", 1, 1.0, "\ud800", "00"), pub) is False


# --- Astra 26 #2: Overlapping load_shedder.sh serialization ------------------------------------


def _setup_shedder_env(tmp_path: Path, docker_script: str) -> tuple[Path, Path, http.server.HTTPServer, dict[str, str]]:
    fake = tmp_path / "fake"
    bin_dir = tmp_path / "bin"
    fake.mkdir()
    bin_dir.mkdir()
    (fake / "metrics").write_text(
        "sovereign_telemetry_source_up 1\nsovereign_battery_soc_percent 70.0\nsovereign_temp_coolant_celsius 30.0\n",
        encoding="utf-8",
    )
    (fake / "status").write_text("paused\n", encoding="utf-8")
    (fake / "docker.log").write_text("", encoding="utf-8")

    curl_bin = bin_dir / "curl"
    curl_bin.write_text('#!/bin/sh\ncat "$FAKE/metrics"\n', encoding="utf-8")
    os.chmod(curl_bin, 0o700)

    docker_bin = bin_dir / "docker"
    docker_bin.write_text(docker_script, encoding="utf-8")
    os.chmod(docker_bin, 0o700)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = (fake / "metrics").read_bytes()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state_file = fake / "load_shedder.paused"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE": str(fake),
        "TMPDIR": str(tmp_path),
        "HOME": str(fake),
        "LOAD_SHED_ACTION": "pause",
        "LOAD_SHED_STATE_FILE": str(state_file),
        "TELEMETRY_METRICS_URL": f"http://127.0.0.1:{server.server_port}/metrics",
    }
    return fake, state_file, server, env


def test_overlapping_load_shedder_runs_serialize_and_preserve_new_thermal_pause(tmp_path: Path) -> None:
    if shutil.which("bash") is None or shutil.which("awk") is None:
        pytest.skip("bash and awk are needed to run load_shedder.sh")

    docker_script = """#!/bin/sh
case "$1" in
  inspect)
    if [ -e "$FAKE/block_on_inspect" ]; then
      rm -f "$FAKE/block_on_inspect"
      touch "$FAKE/in_inspect"
      i=0
      while [ ! -e "$FAKE/release_inspect" ] && [ "$i" -lt 200 ]; do
        sleep 0.02
        i=$((i + 1))
      done
    fi
    cat "$FAKE/status" ;;
  pause) echo "pause $2" >>"$FAKE/docker.log"; echo paused >"$FAKE/status" ;;
  unpause) echo "unpause $2" >>"$FAKE/docker.log"; echo running >"$FAKE/status" ;;
esac
"""
    fake, state_file, server, env = _setup_shedder_env(tmp_path, docker_script)
    script_path = str(ROOT / "software" / "telemetry" / "load_shedder.sh")
    proc_a: subprocess.Popen[str] | None = None
    proc_b: subprocess.Popen[str] | None = None
    try:
        state_file.write_text("container=sovereign_ollama\ncause=soc\n", encoding="utf-8")
        (fake / "block_on_inspect").touch()

        proc_a = subprocess.Popen(
            ["bash", script_path],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 10.0
        while not (fake / "in_inspect").exists():
            if time.monotonic() > deadline:
                raise AssertionError("Run A did not enter docker inspect before deadline")
            time.sleep(0.01)

        # While Run A holds the lock inside docker inspect on 70% / 30 C, overheating begins.
        (fake / "metrics").write_text(
            "sovereign_telemetry_source_up 1\nsovereign_battery_soc_percent 70.0\nsovereign_temp_coolant_celsius 65.0\n",
            encoding="utf-8",
        )
        proc_b = subprocess.Popen(
            ["bash", script_path],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.15)
        (fake / "release_inspect").touch()

        out_a, err_a = proc_a.communicate(timeout=15)
        out_b, err_b = proc_b.communicate(timeout=15)
        assert proc_a.returncode == 0, err_a or out_a
        assert proc_b.returncode == 0, err_b or out_b
    finally:
        (fake / "release_inspect").touch()
        for proc in (proc_a, proc_b):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        server.shutdown()
        server.server_close()

    # Run B must wait until Run A releases the lock, then re-pause for coolant 65 C and record cause=thermal.
    assert (fake / "status").read_text(encoding="utf-8").strip() == "paused"
    assert state_file.exists()
    marker_lines = state_file.read_text(encoding="utf-8").splitlines()
    assert marker_lines == ["container=sovereign_ollama", "cause=thermal"]
    assert (fake / "docker.log").read_text(encoding="utf-8").splitlines() == [
        "unpause sovereign_ollama",
        "pause sovereign_ollama",
    ]
    assert not os.path.lexists(f"{state_file}.lock")


def test_load_shedder_reaps_dead_pid_lock_and_times_out_when_live_pid_holds_lock(tmp_path: Path) -> None:
    if shutil.which("bash") is None or shutil.which("awk") is None:
        pytest.skip("bash and awk are needed to run load_shedder.sh")

    docker_script = """#!/bin/sh
case "$1" in
  inspect) cat "$FAKE/status" ;;
  pause) echo "pause $2" >>"$FAKE/docker.log"; echo paused >"$FAKE/status" ;;
  unpause) echo "unpause $2" >>"$FAKE/docker.log"; echo running >"$FAKE/status" ;;
esac
"""
    fake, state_file, server, env = _setup_shedder_env(tmp_path, docker_script)
    script_path = str(ROOT / "software" / "telemetry" / "load_shedder.sh")
    lock_path = Path(f"{state_file}.lock")
    try:
        # Stale lock left behind by a dead PID is reaped automatically.
        state_file.write_text("container=sovereign_ollama\ncause=soc\n", encoding="utf-8")
        os.symlink("99999999", lock_path)
        proc_dead = subprocess.run(
            ["bash", script_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert proc_dead.returncode == 0, proc_dead.stderr
        assert not state_file.exists()
        assert not os.path.lexists(lock_path)
        assert (fake / "status").read_text(encoding="utf-8").strip() == "running"

        # Live holder lock causes clean timeout without breaking the holder's lock or marker.
        state_file.write_text("container=sovereign_ollama\ncause=soc\n", encoding="utf-8")
        (fake / "status").write_text("paused\n", encoding="utf-8")
        os.symlink(str(os.getpid()), lock_path)
        proc_live = subprocess.run(
            ["bash", script_path],
            env={**env, "LOAD_SHED_LOCK_TIMEOUT": "0.1"},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert proc_live.returncode == 0, proc_live.stderr
        assert "Timed out after 0.1s waiting for active load_shedder.sh lock" in proc_live.stdout
        assert os.path.islink(lock_path)
        assert os.readlink(lock_path) == str(os.getpid())
        assert state_file.exists()
    finally:
        if os.path.lexists(lock_path):
            lock_path.unlink()
        server.shutdown()
        server.server_close()


# --- Fable 26 #2: MCP set_load_shedding truthful description with min_battery_soc holds ----------


def test_mcp_set_load_shedding_description_reflects_min_battery_soc_hold() -> None:
    manager = mcp_tools.get_enterprise_manager()
    manifest = EnterpriseRegistry.scaffold_manifest(name="SoC Guarded", app_id="app-soc-hold")
    manifest.power.tier = PowerPriority.L1_STANDARD
    manifest.power.min_battery_soc = 40.0
    manager.registry.register_app(manifest)
    assert manager.start_app("app-soc-hold")[0]

    # Publish SoC below min_battery_soc=40 so the app pauses for soc_floor at L0.
    get_event_bus().publish(
        Event(event_type=EventType.LOAD_SHEDDING_CHANGED, source="test", payload={"level": 0, "battery_soc": 30.0})
    )
    state = manager.get_runtime_state("app-soc-hold")
    assert state is not None and state.status == AppStatus.PAUSED

    # Calling set_load_shedding(L0) and (L1) carries no battery_soc, so app stays paused;
    # both the tool result description and the tool schema description disclose this behavior.
    res_l0 = mcp_tools.tool_set_load_shedding({"level": "L0"})
    res_l1 = mcp_tools.tool_set_load_shedding({"level": "L1"})
    state_after = manager.get_runtime_state("app-soc-hold")
    assert state_after is not None and state_after.status == AppStatus.PAUSED

    for res in (res_l0, res_l1):
        desc = res["description"]
        assert "apps paused solely by tier shedding are resumed" in desc.lower()
        assert "no battery_soc reading" in desc
        assert "min_battery_soc" in desc

    tool = next(t for t in mcp_tools.get_mcp_tools() if t.name == "set_load_shedding")
    assert "apps paused solely by tier shedding" in tool.description
    assert "min_battery_soc" in tool.description
    assert "no battery_soc reading" in tool.description

    # Only an event carrying battery_soc above floor + hysteresis clears the hold.
    get_event_bus().publish(
        Event(event_type=EventType.LOAD_SHEDDING_CHANGED, source="test", payload={"level": 0, "battery_soc": 60.0})
    )
    resumed = manager.get_runtime_state("app-soc-hold")
    assert resumed is not None and resumed.status == AppStatus.RUNNING


# --- Fable 26 #1 & Astra 26 #3: Coverage docs & qualified LiFePO4 claim ------------------------


def test_round26_documented_coverage_and_lfp_wording() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    comm = (ROOT / "COMMERCIALIZATION.md").read_text(encoding="utf-8")

    assert "92.2%25%20Cov" in readme
    assert "92.2% coverage" in readme
    assert "92.2% coverage" in agents
    assert "92.2% Coverage" in comm

    lower_comm = comm.lower()
    assert "zero thermal runaway" not in lower_comm
    assert "lifepo4" in lower_comm
    assert "thermal-runaway onset temperature" in lower_comm
