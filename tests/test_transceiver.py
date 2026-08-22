"""
Tests for the SpaceChannelSimulator (transceiver) and RF link budget calculations.
"""

import pytest

from sovereign_dc.space.dtn.bundle import Bundle
from sovereign_dc.space.orbital.propagator import GroundStation, SatelliteOrbit
from sovereign_dc.space.transceiver.simulated_link import (
    BaseTransceiver,
    SpaceChannelSimulator,
)

# ---------------------------------------------------------------------------
# BaseTransceiver contract
# ---------------------------------------------------------------------------


def test_base_transceiver_raises_not_implemented():
    """BaseTransceiver is an abstract interface; every method must raise."""
    bt = BaseTransceiver()
    with pytest.raises(NotImplementedError):
        bt.connect()
    with pytest.raises(NotImplementedError):
        bt.transmit_bundle(None)
    with pytest.raises(NotImplementedError):
        bt.receive_bundle()
    with pytest.raises(NotImplementedError):
        bt.get_link_status()


# ---------------------------------------------------------------------------
# SpaceChannelSimulator – Free-Space Path Loss
# ---------------------------------------------------------------------------


def test_fspl_zero_distance():
    """FSPL at zero distance should return 0.0 (degenerate case guard)."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sim = SpaceChannelSimulator(gs)
    assert sim.calculate_free_space_path_loss(0.0) == 0.0
    assert sim.calculate_free_space_path_loss(-1.0) == 0.0


def test_fspl_known_value():
    """Validate FSPL formula against a hand-calculated reference:

    distance = 1000 km, freq = 2200 MHz
    FSPL = 20*log10(1000) + 20*log10(2200) + 32.44
         = 60 + 66.848 + 32.44 = 159.288 dB
    """
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sim = SpaceChannelSimulator(gs, carrier_freq_mhz=2200.0)
    fspl = sim.calculate_free_space_path_loss(1000.0)
    assert abs(fspl - 159.288) < 0.01


def test_fspl_increases_with_distance():
    """Path loss must increase monotonically with distance."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sim = SpaceChannelSimulator(gs)
    fspl_500 = sim.calculate_free_space_path_loss(500.0)
    fspl_1000 = sim.calculate_free_space_path_loss(1000.0)
    fspl_2000 = sim.calculate_free_space_path_loss(2000.0)
    assert fspl_500 < fspl_1000 < fspl_2000


# ---------------------------------------------------------------------------
# SpaceChannelSimulator – Active Link Metrics
# ---------------------------------------------------------------------------


def test_active_link_metrics_keys():
    """get_active_link_metrics must return all expected telemetry keys."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sat = SatelliteOrbit("LEO-Test", 99901, inclination_deg=53.0, altitude_km=550.0, period_minutes=95.0)
    sim = SpaceChannelSimulator(gs)

    metrics = sim.get_active_link_metrics(sat)
    expected_keys = {
        "satellite_name",
        "norad_id",
        "is_in_contact",
        "azimuth_deg",
        "elevation_deg",
        "range_km",
        "path_loss_db",
        "snr_db",
        "doppler_shift_hz",
        "link_margin_db",
    }
    assert expected_keys == set(metrics.keys())


def test_active_link_metrics_types():
    """All metric values must be numeric (int, float, or bool)."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sat = SatelliteOrbit("LEO-Test", 99901, inclination_deg=53.0, altitude_km=550.0, period_minutes=95.0)
    sim = SpaceChannelSimulator(gs)

    metrics = sim.get_active_link_metrics(sat)
    assert isinstance(metrics["satellite_name"], str)
    assert isinstance(metrics["norad_id"], int)
    assert isinstance(metrics["is_in_contact"], bool)
    for key in ("azimuth_deg", "elevation_deg", "range_km", "path_loss_db", "snr_db"):
        assert isinstance(metrics[key], (int, float)), f"{key} is not numeric"


def test_link_margin_non_negative():
    """Link margin must never go below zero."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sat = SatelliteOrbit("LEO-Test", 99901, inclination_deg=53.0, altitude_km=550.0, period_minutes=95.0)
    sim = SpaceChannelSimulator(gs)
    metrics = sim.get_active_link_metrics(sat)
    assert metrics["link_margin_db"] >= 0.0


def test_azimuth_within_range():
    """Azimuth must always be in [0, 360]."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sat = SatelliteOrbit("LEO-Test", 99901, inclination_deg=53.0, altitude_km=550.0, period_minutes=95.0)
    sim = SpaceChannelSimulator(gs)
    metrics = sim.get_active_link_metrics(sat)
    assert 0.0 <= metrics["azimuth_deg"] <= 360.0


# ---------------------------------------------------------------------------
# SpaceChannelSimulator – Bundle Transmission
# ---------------------------------------------------------------------------


def test_transmit_bundle_tracking():
    """Transmit should increment the total TX byte counter."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sim = SpaceChannelSimulator(gs)

    assert sim.total_tx_bytes == 0
    b = Bundle("dtn://node/tx", "dtn://ground/rx", b"TELEMETRY_FRAME_01")
    result = sim.transmit_bundle(b)
    assert result is True
    assert sim.total_tx_bytes > 0


def test_transmit_multiple_bundles_accumulates():
    """Byte counter should accumulate across multiple transmissions."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sim = SpaceChannelSimulator(gs)

    b1 = Bundle("dtn://n/a", "dtn://g/b", b"FRAME_A")
    b2 = Bundle("dtn://n/a", "dtn://g/b", b"FRAME_B_LONGER_PAYLOAD")
    sim.transmit_bundle(b1)
    after_first = sim.total_tx_bytes
    sim.transmit_bundle(b2)
    assert sim.total_tx_bytes > after_first


def test_receive_bundle_returns_none():
    """Simulated receiver always returns None (no uplink simulation yet)."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sim = SpaceChannelSimulator(gs)
    assert sim.receive_bundle() is None


# ---------------------------------------------------------------------------
# SpaceChannelSimulator – Link Status
# ---------------------------------------------------------------------------


def test_link_status_structure():
    """get_link_status must contain connected, total_tx_bytes, total_rx_bytes."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sim = SpaceChannelSimulator(gs)
    status = sim.get_link_status()
    assert status["connected"] is True
    assert status["total_tx_bytes"] == 0
    assert status["total_rx_bytes"] == 0


# ---------------------------------------------------------------------------
# SpaceChannelSimulator – Antenna Gain Configuration
# ---------------------------------------------------------------------------


def test_custom_antenna_gains():
    """Custom antenna gains should affect link budget calculations."""
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sim_low = SpaceChannelSimulator(gs, ground_antenna_gain_dbi=6.0, sat_antenna_gain_dbi=3.0)
    sim_high = SpaceChannelSimulator(gs, ground_antenna_gain_dbi=30.0, sat_antenna_gain_dbi=12.0)

    sat = SatelliteOrbit("LEO-Test", 99901, inclination_deg=53.0, altitude_km=550.0, period_minutes=95.0)
    metrics_low = sim_low.get_active_link_metrics(sat)
    metrics_high = sim_high.get_active_link_metrics(sat)

    # Higher gain antennas should yield better SNR when in contact
    if metrics_low["is_in_contact"] and metrics_high["is_in_contact"]:
        assert metrics_high["snr_db"] >= metrics_low["snr_db"]
