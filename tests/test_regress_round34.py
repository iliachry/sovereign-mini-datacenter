"""Regression tests for tri-council review round 34.

1. ``Ledger._check_chain()`` and ``smdc economy audit`` evaluate the stored head
   anchor MAC against the stored chain head even when row verification fails, so
   tampering an interior row reports ``Chain links: INVALID`` alongside
   ``Head anchor: VALID`` (or ``UNVERIFIABLE`` without an identity key).
2. ``software/telemetry/load_shedder.sh`` tracks the ``docker pause`` child worker in
   ``${STATE_FILE}.pending`` so a ``SIGKILL`` on the parent shell does not lose
   ownership while the pause completes asynchronously.
3. ``software/telemetry/load_shedder.sh`` merges newly observed shedding causes into
   existing pause/pending markers even when ``docker inspect`` returns ``unknown``.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import time
from argparse import Namespace
from pathlib import Path

import pytest

from sovereign_dc import cli
from sovereign_dc.economy.ledger import Ledger
from sovereign_dc.economy.node_state import NodeWallet


def test_economy_audit_evaluates_head_anchor_even_when_row_fails_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SMDC_STATE_DIR", str(tmp_path / "state"))
    db = str(tmp_path / "ledger.db")
    ledger = Ledger(db_path=db)
    wallet = NodeWallet.create(node_id="audit-head-anchor")
    ledger.mint(wallet.address, 2.0, memo="tx1")
    ledger.mint(wallet.address, 3.0, memo="tx2")

    conn = sqlite3.connect(db)
    conn.execute("UPDATE transactions SET memo = 'tampered' WHERE rowid = 1")
    conn.commit()
    conn.close()

    assert ledger.chain_anchor_status() == "valid"
    assert ledger.verify_chain() is False
    assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=False)) == 1
    out_valid_anchor = capsys.readouterr().out
    links_line = next(line for line in out_valid_anchor.splitlines() if "Chain links:" in line)
    anchor_line = next(line for line in out_valid_anchor.splitlines() if "Head anchor:" in line)
    assert "INVALID" in links_line
    assert "VALID" in anchor_line and "INVALID" not in anchor_line

    with monkeypatch.context() as mp:
        mp.setattr("sovereign_dc.economy.ledger._node_anchor_key", lambda: None)
        assert ledger.chain_anchor_status() == "unverifiable"
        assert cli.cmd_economy_verify(Namespace(db_path=db, anchor=False)) == 1
        out_unverifiable = capsys.readouterr().out
        links_line_u = next(line for line in out_unverifiable.splitlines() if "Chain links:" in line)
        anchor_line_u = next(line for line in out_unverifiable.splitlines() if "Head anchor:" in line)
        assert "INVALID" in links_line_u
        assert "UNVERIFIABLE" in anchor_line_u


def test_load_shedder_inflight_pause_outliving_parent_lock_preserves_recovery(tmp_path: Path) -> None:
    if shutil.which("bash") is None or shutil.which("awk") is None:
        pytest.skip("bash and awk are needed to run load_shedder.sh")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    metrics_file = tmp_path / "metrics.prom"
    container_state = tmp_path / "container.state"
    docker_log = tmp_path / "docker.log"
    state_file = tmp_path / "load_shedder.paused"
    pending_file = tmp_path / "load_shedder.paused.pending"
    pause_entered = tmp_path / "pause.entered"
    release_pause = tmp_path / "pause.release"
    worker_done = tmp_path / "pause.done"

    curl_bin = bin_dir / "curl"
    curl_bin.write_text(
        f'#!/bin/sh\ncat "{metrics_file}"\n',
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
        exit 1
        ;;
    pause)
        printf 'pause %s\\n' "$2" >>"{docker_log}"
        if [ -f "{pause_entered}.armed" ]; then
            : >"{pause_entered}"
            tries=0
            while [ ! -f "{release_pause}" ] && [ "$tries" -lt 200 ]; do
                sleep 0.05
                tries=$((tries + 1))
            done
        fi
        printf 'paused\\n' >"{container_state}"
        : >"{worker_done}"
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

    script_path = str(Path(__file__).resolve().parent.parent / "software" / "telemetry" / "load_shedder.sh")

    def make_env(timeout_s: str = "0.15") -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{bin_dir}:{env.get('PATH', '/usr/bin:/bin')}",
                "TELEMETRY_METRICS_URL": "http://fake.local/metrics",
                "LOAD_SHED_ACTION": "pause",
                "LOAD_SHED_CONTAINER": "sovereign_ollama",
                "LOAD_SHED_STATE_FILE": str(state_file),
                "LOAD_SHED_LOCK_TIMEOUT": timeout_s,
            }
        )
        return env

    def write_metrics(soc: int, coolant: float = 30.0) -> None:
        metrics_file.write_text(
            f"sovereign_telemetry_source_up 1\n"
            f"sovereign_battery_soc_percent {soc}\n"
            f"sovereign_temp_coolant_celsius {coolant}\n",
            encoding="utf-8",
        )

    container_state.write_text("running\n", encoding="utf-8")
    (pause_entered.parent / f"{pause_entered.name}.armed").write_text("1\n", encoding="utf-8")
    write_metrics(15)

    proc = subprocess.Popen(  # noqa: S603
        ["bash", script_path],
        env=make_env("0.15"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5.0
        while not pause_entered.exists():
            assert time.monotonic() < deadline, "docker pause never started"
            assert proc.poll() is None
            time.sleep(0.02)

        # SIGKILL parent load_shedder.sh while docker pause worker is still blocked in-flight.
        proc.kill()
        proc.wait(timeout=5)
        (pause_entered.parent / f"{pause_entered.name}.armed").unlink()

        # Next run at 70% SoC sees live worker in .pending and must NOT discard .pending.
        write_metrics(70)
        res_mid = subprocess.run(
            ["bash", script_path], env=make_env("0.15"), capture_output=True, text=True, check=False
        )  # noqa: S603, S607
        assert res_mid.returncode == 0, res_mid.stderr
        assert pending_file.exists()
        assert container_state.read_text(encoding="utf-8").strip() == "running"

        # Now let the orphaned docker pause child complete.
        release_pause.write_text("go\n", encoding="utf-8")
        deadline = time.monotonic() + 5.0
        while not worker_done.exists():
            assert time.monotonic() < deadline, "orphaned docker pause worker did not exit"
            time.sleep(0.02)
        time.sleep(0.05)
        assert container_state.read_text(encoding="utf-8").strip() == "paused"

        # Recovery run sees exited worker + paused container, promotes .pending -> .paused, then unpauses.
        res_rec = subprocess.run(
            ["bash", script_path], env=make_env("1.0"), capture_output=True, text=True, check=False
        )  # noqa: S603, S607
        assert res_rec.returncode == 0, res_rec.stderr
        assert "Recovery verified" in res_rec.stdout
        assert not pending_file.exists()
        assert not state_file.exists()
        assert container_state.read_text(encoding="utf-8").strip() == "running"
        assert docker_log.read_text(encoding="utf-8").splitlines() == [
            "pause sovereign_ollama",
            "unpause sovereign_ollama",
        ]
    finally:
        release_pause.write_text("go\n", encoding="utf-8")
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_load_shedder_records_new_shedding_cause_during_docker_outage(tmp_path: Path) -> None:
    if shutil.which("bash") is None or shutil.which("awk") is None:
        pytest.skip("bash and awk are needed to run load_shedder.sh")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    metrics_file = tmp_path / "metrics.prom"
    container_state = tmp_path / "container.state"
    outage_flag = tmp_path / "docker.outage"
    docker_log = tmp_path / "docker.log"
    state_file = tmp_path / "load_shedder.paused"

    curl_bin = bin_dir / "curl"
    curl_bin.write_text(f'#!/bin/sh\ncat "{metrics_file}"\n', encoding="utf-8")
    curl_bin.chmod(0o755)

    docker_bin = bin_dir / "docker"
    docker_bin.write_text(
        f"""#!/bin/sh
cmd="${{1:-}}"
case "$cmd" in
    inspect)
        if [ -f "{outage_flag}" ]; then
            printf 'Cannot connect to the Docker daemon\\n' >&2
            exit 1
        fi
        if [ -f "{container_state}" ]; then
            cat "{container_state}"
            exit 0
        fi
        exit 1
        ;;
    pause)
        printf 'pause %s\\n' "$2" >>"{docker_log}"
        printf 'paused\\n' >"{container_state}"
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

    script_path = str(Path(__file__).resolve().parent.parent / "software" / "telemetry" / "load_shedder.sh")

    def run_once(soc: int, coolant: float | None) -> subprocess.CompletedProcess[str]:
        body = f"sovereign_telemetry_source_up 1\nsovereign_battery_soc_percent {soc}\n"
        if coolant is not None:
            body += f"sovereign_temp_coolant_celsius {coolant}\n"
        metrics_file.write_text(body, encoding="utf-8")
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{bin_dir}:{env.get('PATH', '/usr/bin:/bin')}",
                "TELEMETRY_METRICS_URL": "http://fake.local/metrics",
                "LOAD_SHED_ACTION": "pause",
                "LOAD_SHED_CONTAINER": "sovereign_ollama",
                "LOAD_SHED_STATE_FILE": str(state_file),
                "LOAD_SHED_LOCK_TIMEOUT": "2",
            }
        )
        return subprocess.run(["bash", script_path], env=env, capture_output=True, text=True, check=False)  # noqa: S603, S607

    container_state.write_text("running\n", encoding="utf-8")
    assert run_once(15, 30.0).returncode == 0
    assert state_file.read_text(encoding="utf-8").splitlines() == ["container=sovereign_ollama", "cause=soc"]

    # Docker inspect outage while thermal limit exceeds 60°C -> cause=thermal must still be added.
    outage_flag.write_text("1\n", encoding="utf-8")
    res_outage = run_once(70, 65.0)
    assert res_outage.returncode == 0, res_outage.stderr
    assert state_file.read_text(encoding="utf-8").splitlines() == [
        "container=sovereign_ollama",
        "cause=soc",
        "cause=thermal",
    ]

    # Docker returns while coolant sensor is missing: SoC recovered, thermal unverified -> must hold!
    outage_flag.unlink()
    res_hold = run_once(70, None)
    assert res_hold.returncode == 0, res_hold.stderr
    assert "Holding the pause" in res_hold.stdout
    assert container_state.read_text(encoding="utf-8").strip() == "paused"
    assert docker_log.read_text(encoding="utf-8").splitlines() == ["pause sovereign_ollama"]

    # Coolant cools <= 55°C -> now resumes cleanly.
    res_resume = run_once(70, 45.0)
    assert res_resume.returncode == 0, res_resume.stderr
    assert "Recovery verified" in res_resume.stdout
    assert not state_file.exists()
    assert container_state.read_text(encoding="utf-8").strip() == "running"
    assert docker_log.read_text(encoding="utf-8").splitlines() == [
        "pause sovereign_ollama",
        "unpause sovereign_ollama",
    ]
