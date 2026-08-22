"""
Sovereign Mini Datacenter - Orbital Mechanics & Satellite Pass Predictor
Calculates topocentric Azimuth/Elevation, Range, Doppler shift, and contact passes.
Standard library implementation (no external C-extensions required).
"""

import math
import time
from typing import Dict, List, Any, Tuple, Optional

# Earth Constants (WGS-84)
EARTH_RADIUS_KM = 6378.137
SPEED_OF_LIGHT_MS = 299792458.0

class SatelliteOrbit:
    def __init__(
        self,
        name: str,
        norad_id: int,
        inclination_deg: float,
        altitude_km: float,
        period_minutes: float,
        raan_deg: float = 0.0,
        epoch_timestamp: Optional[float] = None
    ):
        self.name = name
        self.norad_id = norad_id
        self.inclination_rad = math.radians(inclination_deg)
        self.altitude_km = altitude_km
        self.orbit_radius_km = EARTH_RADIUS_KM + altitude_km
        self.period_minutes = period_minutes
        self.mean_motion_rad_s = (2.0 * math.pi) / (period_minutes * 60.0)
        self.raan_rad = math.radians(raan_deg)
        self.epoch_timestamp = epoch_timestamp or time.time()

    def get_position_at(self, timestamp: float) -> Tuple[float, float, float]:
        """Calculates satellite geodetic position (lat_deg, lon_deg, alt_km)."""
        dt_s = timestamp - self.epoch_timestamp
        mean_anomaly = (dt_s * self.mean_motion_rad_s) % (2.0 * math.pi)

        # Simplified circular orbit projection
        lat_rad = math.asin(math.sin(self.inclination_rad) * math.sin(mean_anomaly))
        
        # Earth rotation adjustment (15 deg / hour = 7.292115e-5 rad/s)
        earth_rot_rad = 7.292115e-5 * dt_s
        lon_rad = (math.atan2(
            math.cos(self.inclination_rad) * math.sin(mean_anomaly),
            math.cos(mean_anomaly)
        ) + self.raan_rad - earth_rot_rad) % (2.0 * math.pi)

        if lon_rad > math.pi:
            lon_rad -= 2.0 * math.pi

        return math.degrees(lat_rad), math.degrees(lon_rad), self.altitude_km


class GroundStation:
    def __init__(self, name: str, latitude_deg: float, longitude_deg: float, altitude_m: float = 50.0):
        self.name = name
        self.lat_rad = math.radians(latitude_deg)
        self.lon_rad = math.radians(longitude_deg)
        self.lat_deg = latitude_deg
        self.lon_deg = longitude_deg
        self.altitude_km = altitude_m / 1000.0

    def calculate_look_angles(self, sat_lat_deg: float, sat_lon_deg: float, sat_alt_km: float) -> Tuple[float, float, float]:
        """
        Calculates Azimuth (0-360 deg), Elevation (-90 to 90 deg), and Slant Range (km).
        """
        sat_lat_rad = math.radians(sat_lat_deg)
        sat_lon_rad = math.radians(sat_lon_deg)
        r_sat = EARTH_RADIUS_KM + sat_alt_km
        r_ground = EARTH_RADIUS_KM + self.altitude_km

        # Earth-Centered Earth-Fixed (ECEF) coordinates
        gx = r_ground * math.cos(self.lat_rad) * math.cos(self.lon_rad)
        gy = r_ground * math.cos(self.lat_rad) * math.sin(self.lon_rad)
        gz = r_ground * math.sin(self.lat_rad)

        sx = r_sat * math.cos(sat_lat_rad) * math.cos(sat_lon_rad)
        sy = r_sat * math.cos(sat_lat_rad) * math.sin(sat_lon_rad)
        sz = r_sat * math.sin(sat_lat_rad)

        # Range vector
        dx = sx - gx
        dy = sy - gy
        dz = sz - gz
        range_km = math.sqrt(dx*dx + dy*dy + dz*dz)

        # Topocentric SEZ (South, East, Zenith) coordinates
        sin_lat = math.sin(self.lat_rad)
        cos_lat = math.cos(self.lat_rad)
        sin_lon = math.sin(self.lon_rad)
        cos_lon = math.cos(self.lon_rad)

        s = sin_lat * cos_lon * dx + sin_lat * sin_lon * dy - cos_lat * dz
        e = -sin_lon * dx + cos_lon * dy
        z = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz

        # Elevation and Azimuth
        elevation_rad = math.asin(max(-1.0, min(1.0, z / max(1.0, range_km))))
        azimuth_rad = math.atan2(e, -s) % (2.0 * math.pi)

        return math.degrees(azimuth_rad), math.degrees(elevation_rad), range_km

    def calculate_doppler_shift(self, carrier_freq_hz: float, range_rate_kms: float) -> float:
        """Calculates Doppler shift in Hz: delta_f = -f0 * (v_r / c)."""
        v_ms = range_rate_kms * 1000.0
        return -carrier_freq_hz * (v_ms / SPEED_OF_LIGHT_MS)

    def predict_passes(
        self,
        satellite: SatelliteOrbit,
        duration_hours: float = 12.0,
        min_elevation_deg: float = 10.0,
        time_step_s: float = 30.0
    ) -> List[Dict[str, Any]]:
        """Predicts upcoming contact passes with AOS, TCA, LOS, and Max Elevation."""
        now = time.time()
        end_time = now + duration_hours * 3600.0
        t = now

        passes: List[Dict[str, Any]] = []
        in_pass = False
        current_pass: Dict[str, Any] = {}
        max_el = 0.0
        max_el_time = t
        aos_t = t

        while t < end_time:
            sat_lat, sat_lon, sat_alt = satellite.get_position_at(t)
            az, el, rng = self.calculate_look_angles(sat_lat, sat_lon, sat_alt)

            if el >= min_elevation_deg:
                if not in_pass:
                    in_pass = True
                    max_el = el
                    max_el_time = t
                    aos_t = t
                    current_pass = {
                        "satellite": satellite.name,
                        "norad_id": satellite.norad_id,
                        "aos_time": t,
                        "aos_azimuth": round(az, 1),
                        "max_elevation": round(el, 1),
                        "tca_time": t,
                        "duration_seconds": 0,
                    }
                else:
                    if el > max_el:
                        max_el = el
                        max_el_time = t
                        current_pass["max_elevation"] = round(el, 1)
                        current_pass["tca_time"] = t
            else:
                if in_pass:
                    in_pass = False
                    current_pass["los_time"] = t
                    current_pass["los_azimuth"] = round(az, 1)
                    current_pass["duration_seconds"] = int(t - aos_t)
                    passes.append(current_pass)
                    current_pass = {}

            t += time_step_s

        return passes
