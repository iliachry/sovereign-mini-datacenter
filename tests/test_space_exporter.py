"""
Tests for the Space Communications Prometheus Exporter metric formatting.

Since the standalone space_exporter.py uses runtime imports relative to the
software/ directory, we test the underlying modules directly and validate
Prometheus text format output structure.
"""

import time
import pytest
from sovereign_dc.space.orbital.propagator import GroundStation, SatelliteOrbit
from sovereign_dc.space.orbital.tle_updater import get_active_satellites
from sovereign_dc.space.transceiver.simulated_link import SpaceChannelSimulator
from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority
from sovereign_dc.space.dtn.router import DTNRouter


# ---------------------------------------------------------------------------
# Prometheus metric line helpers
# ---------------------------------------------------------------------------

def _build_prometheus_output(
    ground_station: GroundStation,
    satellites: list,
    simulator: SpaceChannelSimulator,
    router: DTNRouter,
) -> str:
    """Replicates the exporter's metric-building logic for test isolation."""
    now = time.time()
    active_link = False
    best_sat_name = "none"
    max_elevation = -90.0
    active_azimuth = 0.0
    active_snr = 0.0
    active_doppler = 0.0
    active_range_km = 0.0

    for sat in satellites:
        metrics = simulator.get_active_link_metrics(sat)
        if metrics["elevation_deg"] > max_elevation:
            max_elevation = metrics["elevation_deg"]
            active_azimuth = metrics["azimuth_deg"]
            active_range_km = metrics["range_km"]
            active_snr = metrics["snr_db"]
            active_doppler = metrics["doppler_shift_hz"]
            best_sat_name = sat.name
            if metrics["is_in_contact"]:
                active_link = True

    passes = ground_station.predict_passes(satellites[0], duration_hours=6.0, min_elevation_deg=10.0)
    next_pass_seconds = 0
    if passes:
        next_pass_seconds = max(0, int(passes[0]["aos_time"] - now))

    spool_stats = router.get_queue_stats()
    queue_count = spool_stats["queued_bundle_count"]
    queue_bytes = spool_stats["total_spool_bytes"]
    link_val = 1 if active_link else 0

    lines = [
        "# HELP sovereign_space_link_active 1 if an orbital satellite link is actively in contact, 0 otherwise",
        "# TYPE sovereign_space_link_active gauge",
        f"sovereign_space_link_active {link_val}",
        "",
        "# HELP sovereign_space_elevation_degrees Current highest satellite elevation angle (degrees)",
        "# TYPE sovereign_space_elevation_degrees gauge",
        f"sovereign_space_elevation_degrees {max_elevation:.1f}",
        "",
        "# HELP sovereign_space_azimuth_degrees Current highest satellite azimuth angle (degrees)",
        "# TYPE sovereign_space_azimuth_degrees gauge",
        f"sovereign_space_azimuth_degrees {active_azimuth:.1f}",
        "",
        "# HELP sovereign_space_slant_range_km Distance to active space relay (km)",
        "# TYPE sovereign_space_slant_range_km gauge",
        f"sovereign_space_slant_range_km {active_range_km:.1f}",
        "",
        "# HELP sovereign_space_link_snr_db Signal to Noise Ratio for space RF downlink (dB)",
        "# TYPE sovereign_space_link_snr_db gauge",
        f"sovereign_space_link_snr_db {active_snr:.1f}",
        "",
        "# HELP sovereign_space_doppler_shift_hz Doppler frequency shift on space carrier (Hz)",
        "# TYPE sovereign_space_doppler_shift_hz gauge",
        f"sovereign_space_doppler_shift_hz {active_doppler:.1f}",
        "",
        "# HELP sovereign_space_next_pass_seconds Countdown in seconds until next satellite contact pass (AOS)",
        "# TYPE sovereign_space_next_pass_seconds gauge",
        f"sovereign_space_next_pass_seconds {next_pass_seconds}",
        "",
        "# HELP sovereign_space_bundle_spool_count Total DTN bundles currently queued in store-and-forward spool",
        "# TYPE sovereign_space_bundle_spool_count gauge",
        f"sovereign_space_bundle_spool_count {queue_count}",
        "",
        "# HELP sovereign_space_bundle_spool_bytes Total size in bytes of queued DTN bundles",
        "# TYPE sovereign_space_bundle_spool_bytes gauge",
        f"sovereign_space_bundle_spool_bytes {queue_bytes}",
        ""
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ground_station():
    return GroundStation("Sovereign-Athens", 37.9838, 23.7275)


@pytest.fixture
def satellites():
    return get_active_satellites()


@pytest.fixture
def simulator(ground_station):
    return SpaceChannelSimulator(ground_station)


@pytest.fixture
def router(tmp_path):
    db_file = str(tmp_path / "exporter_test_spool.db")
    return DTNRouter(db_path=db_file, local_node_eid="dtn://test-exporter")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

EXPECTED_METRIC_NAMES = [
    "sovereign_space_link_active",
    "sovereign_space_elevation_degrees",
    "sovereign_space_azimuth_degrees",
    "sovereign_space_slant_range_km",
    "sovereign_space_link_snr_db",
    "sovereign_space_doppler_shift_hz",
    "sovereign_space_next_pass_seconds",
    "sovereign_space_bundle_spool_count",
    "sovereign_space_bundle_spool_bytes",
]


def test_prometheus_output_contains_all_metrics(ground_station, satellites, simulator, router):
    """Output must contain all nine expected Prometheus metric names."""
    output = _build_prometheus_output(ground_station, satellites, simulator, router)
    for name in EXPECTED_METRIC_NAMES:
        assert name in output, f"Missing metric: {name}"


def test_prometheus_output_has_help_and_type(ground_station, satellites, simulator, router):
    """Every metric must be preceded by # HELP and # TYPE annotations."""
    output = _build_prometheus_output(ground_station, satellites, simulator, router)
    for name in EXPECTED_METRIC_NAMES:
        assert f"# HELP {name} " in output, f"Missing HELP for {name}"
        assert f"# TYPE {name} gauge" in output, f"Missing TYPE for {name}"


def test_prometheus_output_parseable_values(ground_station, satellites, simulator, router):
    """Each non-comment, non-empty line must have metric_name followed by a numeric value."""
    output = _build_prometheus_output(ground_station, satellites, simulator, router)
    for line in output.strip().split("\n"):
        if line.startswith("#") or line.strip() == "":
            continue
        parts = line.split()
        assert len(parts) == 2, f"Bad metric line: {line}"
        metric_name, value_str = parts
        # Value should be parseable as float
        float(value_str)  # Will raise ValueError if not numeric


def test_link_active_is_binary(ground_station, satellites, simulator, router):
    """sovereign_space_link_active must be exactly 0 or 1."""
    output = _build_prometheus_output(ground_station, satellites, simulator, router)
    for line in output.strip().split("\n"):
        if line.startswith("sovereign_space_link_active"):
            val = int(line.split()[1])
            assert val in (0, 1)


def test_spool_metrics_with_queued_bundles(ground_station, satellites, simulator, router):
    """After queuing bundles, spool count and bytes should be non-zero."""
    router.queue_bundle(
        Bundle("dtn://test/src", "dtn://test/dst", b"payload_alpha", priority=BundlePriority.NORMAL)
    )
    router.queue_bundle(
        Bundle("dtn://test/src", "dtn://test/dst", b"payload_beta_longer", priority=BundlePriority.CRITICAL)
    )

    output = _build_prometheus_output(ground_station, satellites, simulator, router)
    for line in output.strip().split("\n"):
        if line.startswith("sovereign_space_bundle_spool_count"):
            assert int(line.split()[1]) == 2
        if line.startswith("sovereign_space_bundle_spool_bytes"):
            assert int(line.split()[1]) > 0


def test_next_pass_non_negative(ground_station, satellites, simulator, router):
    """Next-pass countdown must be >= 0 (never negative)."""
    output = _build_prometheus_output(ground_station, satellites, simulator, router)
    for line in output.strip().split("\n"):
        if line.startswith("sovereign_space_next_pass_seconds"):
            assert int(line.split()[1]) >= 0


def test_space_exporter_module_metrics():
    import io
    from unittest.mock import patch, MagicMock
    from sovereign_dc.space import space_exporter
    
    metrics = space_exporter.get_space_telemetry_metrics()
    assert "sovereign_space_link_active" in metrics
    assert "sovereign_space_elevation_degrees" in metrics
    assert "sovereign_space_bundle_spool_count" in metrics

    # Test HTTP handler
    handler = space_exporter.SpaceMetricsHandler.__new__(space_exporter.SpaceMetricsHandler)
    handler.path = "/metrics"
    handler.wfile = io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.do_GET()
    handler.send_response.assert_called_with(200)

    # Test /health
    handler.path = "/health"
    handler.wfile = io.BytesIO()
    handler.do_GET()
    handler.send_response.assert_called_with(200)
    assert handler.wfile.getvalue() == b"OK\n"

    # Test 404
    handler.path = "/invalid"
    handler.wfile = io.BytesIO()
    handler.do_GET()
    handler.send_response.assert_called_with(404)

    # Test run
    mock_server = MagicMock()
    mock_server.serve_forever.side_effect = KeyboardInterrupt
    with patch("sovereign_dc.space.space_exporter.HTTPServer", return_value=mock_server):
        space_exporter.run()
        mock_server.server_close.assert_called_once()

