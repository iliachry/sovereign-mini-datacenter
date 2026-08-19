"""
Sovereign Mini Datacenter - Space Transceiver Base & Channel Simulator
"""

import math
import time
import random
from typing import Dict, Any, Optional, Tuple
from ..dtn.bundle import Bundle
from ..orbital.propagator import GroundStation, SatelliteOrbit

class BaseTransceiver:
    def connect(self) -> bool:
        raise NotImplementedError

    def transmit_bundle(self, bundle: Bundle) -> bool:
        raise NotImplementedError

    def receive_bundle(self) -> Optional[Bundle]:
        raise NotImplementedError

    def get_link_status(self) -> Dict[str, Any]:
        raise NotImplementedError


class SpaceChannelSimulator(BaseTransceiver):
    def __init__(
        self,
        ground_station: GroundStation,
        carrier_freq_mhz: float = 2200.0,  # S-Band space downlink
        tx_power_dbm: float = 30.0,        # 1 Watt RF
        ground_antenna_gain_dbi: float = 18.0,
        sat_antenna_gain_dbi: float = 6.0,
    ):
        self.ground_station = ground_station
        self.carrier_freq_mhz = carrier_freq_mhz
        self.tx_power_dbm = tx_power_dbm
        self.ground_antenna_gain_dbi = ground_antenna_gain_dbi
        self.sat_antenna_gain_dbi = sat_antenna_gain_dbi
        self.connected = True
        self.total_tx_bytes = 0
        self.total_rx_bytes = 0

    def calculate_free_space_path_loss(self, distance_km: float) -> float:
        """FSPL (dB) = 20*log10(d_km) + 20*log10(f_MHz) + 32.44"""
        if distance_km <= 0:
            return 0.0
        return 20.0 * math.log10(distance_km) + 20.0 * math.log10(self.carrier_freq_mhz) + 32.44

    def get_active_link_metrics(self, satellite: SatelliteOrbit) -> Dict[str, Any]:
        """Calculates real-time RF link budget, SNR, elevation, and contact status."""
        sat_lat, sat_lon, sat_alt = satellite.get_position_at(time.time())
        az, el, rng = self.ground_station.calculate_look_angles(sat_lat, sat_lon, sat_alt)

        fspl_db = self.calculate_free_space_path_loss(rng)
        received_power_dbm = self.tx_power_dbm + self.ground_antenna_gain_dbi + self.sat_antenna_gain_dbi - fspl_db
        noise_floor_dbm = -110.0
        snr_db = max(-10.0, received_power_dbm - noise_floor_dbm)

        is_in_contact = (el >= 10.0)

        # Approximate Doppler shift
        range_rate_kms = math.cos(math.radians(el)) * 7.5 * (1.0 if az < 180 else -1.0)
        doppler_hz = self.ground_station.calculate_doppler_shift(self.carrier_freq_mhz * 1e6, range_rate_kms)

        return {
            "satellite_name": satellite.name,
            "norad_id": satellite.norad_id,
            "is_in_contact": is_in_contact,
            "azimuth_deg": round(az, 1),
            "elevation_deg": round(el, 1),
            "range_km": round(rng, 1),
            "path_loss_db": round(fspl_db, 1),
            "snr_db": round(snr_db, 1) if is_in_contact else 0.0,
            "doppler_shift_hz": round(doppler_hz, 1) if is_in_contact else 0.0,
            "link_margin_db": round(max(0.0, snr_db - 6.0), 1) if is_in_contact else 0.0,
        }

    def transmit_bundle(self, bundle: Bundle) -> bool:
        """Simulates transmitting a bundle across the space RF link."""
        raw = bundle.serialize()
        self.total_tx_bytes += len(raw)
        return True

    def receive_bundle(self) -> Optional[Bundle]:
        return None

    def get_link_status(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "total_tx_bytes": self.total_tx_bytes,
            "total_rx_bytes": self.total_rx_bytes,
        }
