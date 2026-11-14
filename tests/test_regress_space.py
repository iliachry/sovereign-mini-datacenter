"""Regression tests for reproduced defects in the space layer (DTN, orbital, transceiver, exporter)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from sovereign_dc.space.dtn.bundle import Bundle
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
    for dockerfile in (ROOT / "src/sovereign_dc/space/Dockerfile", ROOT / "software/space/Dockerfile"):
        text = dockerfile.read_text(encoding="utf-8")
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
