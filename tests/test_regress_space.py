"""Regression tests for reproduced defects in the space layer (DTN, orbital, transceiver, exporter)."""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from sovereign_dc.space.dtn import router as dtn_router
from sovereign_dc.space.dtn.bundle import Bundle
from sovereign_dc.space.dtn.router import DTNRouter
from sovereign_dc.space.orbital.propagator import (
    REFERENCE_EPOCH_TIMESTAMP,
    SPEED_OF_LIGHT_MS,
    GroundStation,
    SatelliteOrbit,
)
from sovereign_dc.space.space_exporter import seconds_until_next_pass
from sovereign_dc.space.transceiver.simulated_link import SpaceChannelSimulator

ROOT = Path(__file__).resolve().parent.parent
EPOCH = 1_800_000_000.0


def _leo(epoch: float | None = EPOCH) -> SatelliteOrbit:
    return SatelliteOrbit(
        "LEO-Test", 99901, inclination_deg=53.0, altitude_km=550.0, period_minutes=95.0, epoch_timestamp=epoch
    )


def _first_pass(gs: GroundStation, sat: SatelliteOrbit, start: float) -> dict:
    with patch("time.time", return_value=start):
        passes = gs.predict_passes(sat, duration_hours=24.0, min_elevation_deg=10.0, time_step_s=5.0)
    complete = [p for p in passes if not p["truncated"] and p["aos_time"] > start]
    assert complete, "expected at least one complete pass in 24 h"
    return max(complete, key=lambda p: p["max_elevation"])


# --- Bundle -------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [0, -1])
def test_create_fragments_rejects_non_positive_size(size: int) -> None:
    bundle = Bundle("dtn://a", "dtn://b", b"x" * 100)
    with pytest.raises(ValueError):
        bundle.create_fragments(size)


def test_header_checksum_detects_ttl_and_fragment_tampering() -> None:
    bundle = Bundle("dtn://a", "dtn://b", b"payload", lifetime_seconds=3600)
    for field, value in (("ttl", 999_999_999), ("off", 4), ("total_len", 1_000_000)):
        data = json.loads(bundle.serialize())
        data[field] = value
        with pytest.raises(ValueError, match="checksum"):
            Bundle.deserialize(json.dumps(data).encode())


def test_legacy_bundle_without_chk2_still_deserializes() -> None:
    bundle = Bundle("dtn://a", "dtn://b", b"payload")
    data = json.loads(bundle.serialize())
    assert "chk2" in data
    del data["chk2"]  # shape written by nodes that predate the header checksum
    restored = Bundle.deserialize(json.dumps(data).encode())
    assert restored.payload == b"payload"
    assert restored.checksum == bundle.checksum


# --- Orbit epoch / pass prediction ----------------------------------------------------------


def test_default_epoch_is_fixed_reference_not_process_start() -> None:
    sat_a = _leo(epoch=None)
    time.sleep(0.01)
    sat_b = _leo(epoch=None)
    assert sat_a.epoch_timestamp == sat_b.epoch_timestamp == REFERENCE_EPOCH_TIMESTAMP
    assert sat_a.get_position_at(EPOCH) == sat_b.get_position_at(EPOCH)


def test_pass_open_at_window_end_is_reported_truncated() -> None:
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sat = _leo()
    best = _first_pass(gs, sat, EPOCH)
    tca = best["tca_time"]

    # Start 10 minutes before TCA with a window that ends exactly at TCA: the pass is still open
    start = tca - 600.0
    with patch("time.time", return_value=start):
        passes = gs.predict_passes(sat, duration_hours=600.0 / 3600.0, min_elevation_deg=10.0, time_step_s=5.0)

    assert len(passes) == 1
    assert passes[0]["truncated"] is True
    assert passes[0]["los_time"] == pytest.approx(tca)
    assert passes[0]["duration_seconds"] > 0


# --- Doppler --------------------------------------------------------------------------------


def test_doppler_matches_true_range_rate() -> None:
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sat = _leo()
    sim = SpaceChannelSimulator(gs, carrier_freq_mhz=2200.0)
    best = _first_pass(gs, sat, EPOCH)

    def slant_range(t: float) -> float:
        return gs.calculate_look_angles(*sat.get_position_at(t))[2]

    # At TCA the range rate crosses zero: Doppler must be near 0, not tens of kHz
    tca = min(
        (best["tca_time"] + dt / 10.0 for dt in range(-100, 101)),
        key=slant_range,
    )
    with patch("time.time", return_value=tca):
        at_tca = sim.get_active_link_metrics(sat)
    assert at_tca["is_in_contact"] is True
    assert abs(at_tca["doppler_shift_hz"]) < 2_000.0

    # Mid-way between AOS and TCA the satellite approaches: positive shift equal to -f * v_r / c
    t_mid = (best["aos_time"] + best["tca_time"]) / 2.0
    with patch("time.time", return_value=t_mid):
        approaching = sim.get_active_link_metrics(sat)
    expected_rate_ms = (slant_range(t_mid + 0.5) - slant_range(t_mid - 0.5)) * 1000.0
    expected_hz = -2200.0e6 * expected_rate_ms / SPEED_OF_LIGHT_MS
    assert approaching["doppler_shift_hz"] > 0
    assert approaching["doppler_shift_hz"] == pytest.approx(expected_hz, abs=1.0)
    # physically bounded by the orbital speed (~7.6 km/s -> ~56 kHz at 2.2 GHz)
    assert abs(approaching["doppler_shift_hz"]) < 56_000.0


# --- Exporter -------------------------------------------------------------------------------


def test_next_pass_distinguishes_in_contact_from_no_pass() -> None:
    now = 1000.0
    assert seconds_until_next_pass([], now) == -1
    assert seconds_until_next_pass([{"aos_time": now - 30.0}], now) == 0
    assert seconds_until_next_pass([{"aos_time": now + 90.0}], now) == 90


def test_exporter_help_documents_no_pass_sentinel() -> None:
    from sovereign_dc.space import space_exporter

    text = space_exporter.get_space_telemetry_metrics()
    help_line = next(ln for ln in text.splitlines() if ln.startswith("# HELP sovereign_space_next_pass_seconds"))
    assert "-1" in help_line


# --- Packaging ------------------------------------------------------------------------------


def test_space_dockerfile_runs_package_module() -> None:
    # The only Dockerfile: the stale duplicate under src/sovereign_dc/space was removed.
    assert not (ROOT / "src/sovereign_dc/space/Dockerfile").exists()
    text = (ROOT / "software/space/Dockerfile").read_text(encoding="utf-8")
    assert "/app/sovereign_dc/space" in text
    assert "sovereign_dc.space.space_exporter" in text
    assert "/app/software/space" not in text


@pytest.mark.parametrize(
    "relative",
    [
        "space/__init__.py",
        "space/space_exporter.py",
        "space/dtn/bundle.py",
        "space/dtn/router.py",
        "space/orbital/propagator.py",
        "space/orbital/tle_updater.py",
        "space/transceiver/simulated_link.py",
        "agents/gitlab_reviewer.py",
        "agents/knowledge_indexer.py",
        "agents/sentinel_copilot.py",
    ],
)
def test_software_mirror_is_byte_identical(relative: str) -> None:
    assert (ROOT / "src/sovereign_dc" / relative).read_bytes() == (ROOT / "software" / relative).read_bytes()


# --- DTN spool location: private state directory, not shared /tmp ---------------------------


@pytest.fixture
def spool_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """No DTN_DB_PATH; returns (state directory, stand-in for the shared /tmp of older releases)."""
    state_dir = tmp_path / "state"
    legacy_dir = tmp_path / "legacy_tmp"
    legacy_dir.mkdir()
    legacy_dir.chmod(0o1777)
    monkeypatch.delenv("DTN_DB_PATH", raising=False)
    monkeypatch.setenv("SMDC_STATE_DIR", str(state_dir))
    monkeypatch.setattr(dtn_router, "LEGACY_TMP_DIR", str(legacy_dir))
    return state_dir, legacy_dir


def test_router_default_spool_is_in_private_state_dir(spool_env: tuple[Path, Path]) -> None:
    state_dir, _ = spool_env
    router = DTNRouter()
    assert router.db_path == str(state_dir / "dtn_spool.db")
    assert router.queue_bundle(Bundle(source_eid="dtn://a", destination_eid="dtn://b", payload=b"x")) is True
    assert (state_dir / "dtn_spool.db").is_file()
    assert (state_dir.stat().st_mode & 0o777) == 0o700
    # A second router (e.g. after a restart) finds the same persistent spool.
    assert DTNRouter().get_queue_stats()["queued_bundle_count"] == 1


def test_router_honours_env_and_explicit_path(
    spool_env: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir, _ = spool_env
    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "volume" / "bundles.db"))
    assert DTNRouter().db_path == str(tmp_path / "volume" / "bundles.db")  # container deployment
    assert DTNRouter(db_path=str(tmp_path / "explicit.db")).db_path == str(tmp_path / "explicit.db")
    assert DTNRouter(db_path=":memory:").db_path == ":memory:"
    assert not state_dir.exists()  # an explicit location never touches the state directory


def test_router_keeps_using_a_trusted_legacy_spool(spool_env: tuple[Path, Path]) -> None:
    state_dir, legacy_dir = spool_env
    legacy = legacy_dir / "dtn_spool.db"
    old = DTNRouter(db_path=str(legacy))  # what the previous release created in /tmp
    old.queue_bundle(Bundle(source_eid="dtn://a", destination_eid="dtn://b", payload=b"queued before upgrade"))
    legacy.chmod(0o644)

    upgraded = DTNRouter()
    assert upgraded.db_path == str(legacy)
    assert upgraded.get_queue_stats()["queued_bundle_count"] == 1  # nothing orphaned
    assert not (state_dir / "dtn_spool.db").exists()


@pytest.mark.parametrize("flaw", ["world_writable", "group_writable", "symlink", "foreign_owner", "foreign_wal"])
def test_router_never_trusts_a_planted_tmp_spool(
    spool_env: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flaw: str
) -> None:
    state_dir, legacy_dir = spool_env
    legacy = legacy_dir / "dtn_spool.db"
    planted = tmp_path / "attacker.db"
    sqlite3.connect(planted).close()
    planted.write_bytes(b"")
    if flaw == "symlink":
        legacy.symlink_to(planted)
    else:
        legacy.write_bytes(b"")
        legacy.chmod({"world_writable": 0o666, "group_writable": 0o660}.get(flaw, 0o600))
    if flaw == "foreign_wal":
        (legacy_dir / "dtn_spool.db-wal").write_bytes(b"")
        (legacy_dir / "dtn_spool.db-wal").chmod(0o666)
    if flaw == "foreign_owner":
        monkeypatch.setattr(dtn_router.os, "getuid", lambda: 4_000_000)

    router = DTNRouter()
    assert router.db_path == str(state_dir / "dtn_spool.db")
    router.queue_bundle(Bundle(source_eid="dtn://a", destination_eid="dtn://b", payload=b"x"))
    assert legacy.is_symlink() or legacy.stat().st_size == 0  # the planted file was not written
    assert planted.stat().st_size == 0


def test_space_exporter_opens_no_spool_at_import(tmp_path: Path) -> None:
    """Importing the exporter must not create or open a database (it used to open /tmp/dtn_spool.db)."""
    state_dir = tmp_path / "state"
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "SMDC_STATE_DIR": str(state_dir),
        "PYTHONPATH": str(ROOT / "src"),
    }
    env.pop("DTN_DB_PATH", None)
    code = (
        "import sqlite3, sys\n"
        "opened = []\n"
        "real = sqlite3.connect\n"
        "sqlite3.connect = lambda *a, **k: (opened.append(a[0]), real(*a, **k))[1]\n"
        "import sovereign_dc.space.space_exporter as e\n"
        "assert e.router is None and opened == [], opened\n"
        "import sovereign_dc.space.dtn.router as r\n"
        f"r.LEGACY_TMP_DIR = {str(tmp_path / 'legacy_tmp')!r}\n"
        "assert e.get_router() is e.get_router() is e.router\n"
        "print(e.router.db_path)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(state_dir / "dtn_spool.db")


def test_space_exporter_uses_env_spool_and_injected_router(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sovereign_dc.space import space_exporter

    monkeypatch.setattr(space_exporter, "router", None)
    monkeypatch.setenv("DTN_DB_PATH", str(tmp_path / "var-spool-dtn" / "bundles.db"))
    assert space_exporter.get_router().db_path == str(tmp_path / "var-spool-dtn" / "bundles.db")

    injected = DTNRouter(db_path=":memory:")
    injected.queue_bundle(Bundle(source_eid="dtn://a", destination_eid="dtn://b", payload=b"abc"))
    monkeypatch.setattr(space_exporter, "router", injected)
    assert "sovereign_space_bundle_spool_count 1" in space_exporter.get_space_telemetry_metrics()


def test_space_container_sources_only_need_stdlib_and_their_own_package() -> None:
    """software/space is copied alone into the image as the package sovereign_dc.space."""
    for path in sorted((ROOT / "software" / "space").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                assert top in sys.stdlib_module_names or name.startswith("sovereign_dc.space"), (path.name, name)


def test_router_module_is_importable_without_the_rest_of_the_package(tmp_path: Path) -> None:
    """Loads router.py the way the container does: sovereign_dc contains nothing but space/."""
    pkg = tmp_path / "app" / "sovereign_dc"
    (pkg / "space" / "dtn").mkdir(parents=True)
    for rel in ("__init__.py", "dtn/__init__.py", "dtn/bundle.py", "dtn/router.py"):
        (pkg / "space" / rel).write_bytes((ROOT / "software" / "space" / rel).read_bytes())
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(tmp_path / "app"), "HOME": str(tmp_path / "home")}
    env["DTN_DB_PATH"] = str(tmp_path / "var" / "spool" / "dtn" / "bundles.db")
    code = (
        "from sovereign_dc.space.dtn.router import DTNRouter\n"
        "import sovereign_dc, os\n"
        "assert not hasattr(sovereign_dc, 'config')\n"
        "r = DTNRouter(); r.get_queue_stats(); print(r.db_path)\n"
        "assert not os.path.exists(os.path.expanduser('~/.smdc'))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(tmp_path / 'app')!r})\n" + code],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == env["DTN_DB_PATH"]
