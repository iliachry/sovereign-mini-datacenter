#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Space Communications Prometheus Exporter
Runs on port 9102. Standard library implementation.
"""

import os
import sys
import time
import math
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

# Import local space modules
from software.space.dtn.bundle import Bundle, BundlePriority
from software.space.dtn.router import DTNRouter
from software.space.orbital.propagator import GroundStation
from software.space.orbital.tle_updater import get_active_satellites
from software.space.transceiver.simulated_link import SpaceChannelSimulator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PORT = int(os.getenv("SPACE_EXPORTER_PORT", "9102"))
GROUND_LAT = float(os.getenv("GROUND_STATION_LAT", "37.9838"))  # Athens / Southern Europe
GROUND_LON = float(os.getenv("GROUND_STATION_LON", "23.7275"))
GROUND_NAME = os.getenv("GROUND_STATION_NAME", "Sovereign-Ground-01")

# Initialize modules
ground_station = GroundStation(GROUND_NAME, GROUND_LAT, GROUND_LON)
satellites = get_active_satellites()
simulator = SpaceChannelSimulator(ground_station)
router = DTNRouter(db_path=os.getenv("DTN_DB_PATH", "/tmp/dtn_spool.db"))

def get_space_telemetry_metrics() -> str:
    now = time.time()
    
    # 1. Evaluate all satellites in constellation
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

    # 2. Predict next pass for primary relay
    passes = ground_station.predict_passes(satellites[0], duration_hours=6.0, min_elevation_deg=10.0)
    next_pass_seconds = 0
    if passes:
        next_pass_seconds = max(0, int(passes[0]["aos_time"] - now))

    # 3. Get DTN spool queue metrics
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

class SpaceMetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics" or self.path == "/":
            payload = get_space_telemetry_metrics().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.path == "/health" or self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def run():
    server = HTTPServer(("0.0.0.0", PORT), SpaceMetricsHandler)
    logging.info(f"Sovereign Space Communications Exporter listening on :http://0.0.0.0:{PORT}/metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down space exporter.")
        server.server_close()

if __name__ == "__main__":
    run()
