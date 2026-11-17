"""Regression tests for tri-council review round 33.

1. ``software/telemetry/load_shedder.sh`` persists ``${STATE_FILE}.pending`` before
   calling ``docker pause`` and reconciles it under the run lock so a ``SIGKILL``
   between ``docker pause`` and marker promotion cannot orphan sentinel-owned recovery,
   while genuinely externally paused containers remain untouched.
2. ``smdc economy audit`` prints ``Chain links: VALID`` alongside
   ``Head anchor: INVALID`` (and exits 1) when only the head anchor MAC is invalid,
   and ``Chain links: INVALID`` when transaction rows fail verification.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from sovereign_dc import cli
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.node_state import NodeWallet

ROOT = Path(__file__).resolve().parent.parent


def test_load_shedder_recovers_interrupted_pause_ownership_without_claiming_external_pause(tmp_path: Path) -> None:
    if shutil.which("bash") is None or shutil.which("awk") is None:
        pytest.skip("bash and awk are needed to run load_shedder.sh")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    metrics_file = tmp_path / "metrics.prom"
    container_state = tmp_path / "container.state"
    docker_log = tmp_path / "docker.log"
    state_file = tmp_path / "load_shedder.paused"
    pending_file = tmp_path / "load_shedder.paused.pending"

    curl_bin = bin_dir / "curl"
    curl_bin.write_text(
        f"""#!/bin/sh
cat "{metrics_file}"
""",
        encoding="utf-8",
    )
    curl_bin.chmod(0o755)

    docker_bin = bin_dir / "docker"
    docker_bin.write_text(
        f"""#!/bin/sh
cmd="${{1:-}}"
case "$cmd" in
    inspect)
        if [ -f "{container_state}" ]; then
            cat "{container_state}"
            exit 0
        fi
        printf 'Error: No such container: %s\\n' "${{3:-}}" >&2
        exit 1
        ;;
    pause)
        printf 'pause %s\\n' "$2" >>"{docker_log}"
        printf 'paused\\n' >"{container_state}"
        if [ "${{FAKE_KILL_ON_PAUSE:-0}}" = "1" ]; then
            kill -9 "$PPID"
        fi
        exit 0
        ;;
    unpause)
        printf 'unpause %s\\n' "$2" >>"{docker_log}"
        printf 'running\\n' >"{container_state}"
        exit 0
        ;;
esac
exit 1
""",
        encoding="utf-8",
    )
    docker_bin.chmod(0o755)

    script_path = str(ROOT / "software" / "telemetry" / "load_shedder.sh")

    def run_once(soc: int, coolant: float = 30.0, *, kill_on_pause: bool = False) -> subprocess.CompletedProcess[str]:
        metrics_file.write_text(
            f"sovereign_telemetry_source_up 1\n"
            f"sovereign_battery_soc_percent {soc}\n"
            f"sovereign_temp_coolant_celsius {coolant}\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{bin_dir}:{env.get('PATH', '/usr/bin:/bin')}",
                "TELEMETRY_METRICS_URL": "http://fake.local/metrics",
                "LOAD_SHED_ACTION": "pause",
                "LOAD_SHED_CONTAINER": "sovereign_ollama",
                "LOAD_SHED_STATE_FILE": str(state_file),
                "LOAD_SHED_LOCK_TIMEOUT": "2",
                "FAKE_KILL_ON_PAUSE": "1" if kill_on_pause else "0",
            }
        )
        return subprocess.run(["bash", script_path], env=env, capture_output=True, text=True, check=False)  # noqa: S603, S607

    # Case A: Astra 33 scenario — killed right after docker pause, then retried at 15% then 70%.
    container_state.write_text("running\n", encoding="utf-8")
    res_kill = run_once(15, kill_on_pause=True)
    assert res_kill.returncode in (-9, 137)
    assert not state_file.exists()
    assert pending_file.exists()
    assert container_state.read_text(encoding="utf-8").strip() == "paused"

    res_low = run_once(15)
    assert res_low.returncode == 0, res_low.stderr
    assert "still paused by this sentinel" in res_low.stdout
    assert state_file.exists()
    assert not pending_file.exists()
    assert container_state.read_text(encoding="utf-8").strip() == "paused"

    res_rec = run_once(70)
    assert res_rec.returncode == 0, res_rec.stderr
    assert "Recovery verified" in res_rec.stdout
    assert not state_file.exists()
    assert not pending_file.exists()
    assert container_state.read_text(encoding="utf-8").strip() == "running"
    assert docker_log.read_text(encoding="utf-8").splitlines() == [
        "pause sovereign_ollama",
        "unpause sovereign_ollama",
    ]

    # Case B: Killed on pause at 15%, then immediately recovers at 70% in one pass.
    docker_log.write_text("", encoding="utf-8")
    res_kill_b = run_once(15, kill_on_pause=True)
    assert res_kill_b.returncode in (-9, 137)
    assert pending_file.exists()
    assert container_state.read_text(encoding="utf-8").strip() == "paused"

    res_rec_b = run_once(70)
    assert res_rec_b.returncode == 0, res_rec_b.stderr
    assert "Recovery verified" in res_rec_b.stdout
    assert not state_file.exists()
    assert not pending_file.exists()
    assert container_state.read_text(encoding="utf-8").strip() == "running"

    # Case C: Stale .pending when container remained running is cleared; external pause stays untouched.
    docker_log.write_text("", encoding="utf-8")
    pending_file.write_text("container=sovereign_ollama\ncause=soc\n", encoding="utf-8")
    res_stale = run_once(70)
    assert res_stale.returncode == 0, res_stale.stderr
    assert not pending_file.exists()
    assert not state_file.exists()
    assert container_state.read_text(encoding="utf-8").strip() == "running"
    assert docker_log.read_text(encoding="utf-8") == ""

    container_state.write_text("paused\n", encoding="utf-8")
    res_ext_low = run_once(15)
    assert res_ext_low.returncode == 0, res_ext_low.stderr
    assert "already paused, but not by this sentinel" in res_ext_low.stdout
    assert not state_file.exists()
    res_ext_high = run_once(70)
    assert res_ext_high.returncode == 0, res_ext_high.stderr
    assert container_state.read_text(encoding="utf-8").strip() == "paused"
    assert docker_log.read_text(encoding="utf-8") == ""


def test_economy_audit_reports_valid_chain_links_when_only_head_anchor_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SMDC_STATE_DIR", str(tmp_path / "state"))
    db = str(tmp_path / "ledger.db")
    ledger = Ledger(db_path=db)
    wallet = NodeWallet.create(node_id="audit-anchor-node")
    ledger.mint(wallet.address, 2.0, memo="mint")

    conn = sqlite3.connect(db)
    mac = conn.execute("SELECT mac FROM chain_anchor WHERE id = 1").fetchone()[0]
    bad_mac = ("0" if mac[0] != "0" else "1") + mac[1:]
    conn.execute("UPDATE chain_anchor SET mac = ? WHERE id = 1", (bad_mac,))
    conn.commit()
    conn.close()

    assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=False)) == 1
    out = capsys.readouterr().out
    links_line = next(line for line in out.splitlines() if "Chain links:" in line)
    anchor_line = next(line for line in out.splitlines() if "Head anchor:" in line)
    assert "VALID" in links_line and "INVALID" not in links_line
    assert "INVALID" in anchor_line

    conn = sqlite3.connect(db)
    conn.execute("UPDATE transactions SET memo = 'tampered'")
    conn.commit()
    conn.close()

    assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=False)) == 1
    out_bad_links = capsys.readouterr().out
    links_line_bad = next(line for line in out_bad_links.splitlines() if "Chain links:" in line)
    assert "INVALID" in links_line_bad
