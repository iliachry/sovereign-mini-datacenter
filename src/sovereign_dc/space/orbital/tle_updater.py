"""
Sovereign Mini Datacenter - Satellite Constellation Ephemeris Catalog
"""

from .propagator import SatelliteOrbit

# Default orbital parameters for standard space relay constellations
DEFAULT_CONSTELLATION: list[SatelliteOrbit] = [
    SatelliteOrbit(
        name="Starlink-Relay-Alpha",
        norad_id=70001,
        inclination_deg=53.2,
        altitude_km=550.0,
        period_minutes=95.6,
        raan_deg=45.0,
    ),
    SatelliteOrbit(
        name="Iridium-NEXT-102",
        norad_id=41917,
        inclination_deg=86.4,
        altitude_km=780.0,
        period_minutes=100.4,
        raan_deg=120.0,
    ),
    SatelliteOrbit(
        name="Swarm-LoRa-Relay-1",
        norad_id=47432,
        inclination_deg=97.5,
        altitude_km=525.0,
        period_minutes=95.1,
        raan_deg=210.0,
    ),
    SatelliteOrbit(
        name="Sovereign-CubeSat-Relay-1",
        norad_id=99001,
        inclination_deg=51.6,
        altitude_km=420.0,
        period_minutes=92.9,
        raan_deg=300.0,
    ),
]


def get_active_satellites() -> list[SatelliteOrbit]:
    return DEFAULT_CONSTELLATION
