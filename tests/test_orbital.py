import time

from sovereign_dc.space.orbital.propagator import GroundStation, SatelliteOrbit
from sovereign_dc.space.orbital.tle_updater import get_active_satellites


def test_ground_station_initialization():
    gs = GroundStation("Sovereign-Athens", 37.9838, 23.7275, 110.0)
    assert gs.name == "Sovereign-Athens"
    assert gs.lat_deg == 37.9838
    assert gs.lon_deg == 23.7275
    assert gs.altitude_km == 0.11


def test_satellite_orbit_and_look_angles():
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sat = SatelliteOrbit("LEO-Test-1", 99901, inclination_deg=53.0, altitude_km=550.0, period_minutes=95.0)

    now = time.time()
    sat_lat, sat_lon, sat_alt = sat.get_position_at(now)
    az, el, rng = gs.calculate_look_angles(sat_lat, sat_lon, sat_alt)
    assert 0.0 <= az <= 360.0
    assert -90.0 <= el <= 90.0
    assert rng > 0.0


def test_satellite_pass_predictions():
    gs = GroundStation("Test-GS", 37.98, 23.72)
    sat = SatelliteOrbit("LEO-Test-1", 99901, inclination_deg=53.0, altitude_km=550.0, period_minutes=95.0)

    passes = gs.predict_passes(sat, duration_hours=12.0, min_elevation_deg=10.0)
    assert isinstance(passes, list)
    assert len(passes) > 0
    for p in passes:
        assert p["max_elevation"] >= 10.0
        assert p["duration_seconds"] > 0


def test_active_satellite_catalog():
    sats = get_active_satellites()
    assert len(sats) >= 4
    names = [s.name for s in sats]
    assert "Starlink-Relay-Alpha" in names
    assert "Swarm-LoRa-Relay-1" in names
