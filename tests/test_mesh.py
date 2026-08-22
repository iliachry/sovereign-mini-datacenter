import logging
import os
from unittest.mock import MagicMock, patch

import pytest
import yaml

from sovereign_dc.mesh.lora.meshtastic_gateway import (
    decode_packet,
    encode_packet,
    forward_to_space_dtn,
    run_lora_daemon,
)


def test_cluster_config_validity():
    config_path = os.path.join(os.path.dirname(__file__), "..", "software", "mesh", "cluster_config.yaml")
    assert os.path.exists(config_path)
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert "cluster_name" in data
    assert "nodes" in data
    assert len(data["nodes"]) >= 3
    for node in data["nodes"]:
        assert "id" in node
        assert "wireguard_ip" in node
        assert "role" in node


def test_lora_packet_encoding_and_decoding():
    src = "smdc-node-01"
    dst = "smdc-node-02"
    payload = {"soc": 89.5, "solar_w": 1240, "alert": False}

    raw_packet = encode_packet(src, dst, payload)
    assert isinstance(raw_packet, bytes)

    decoded = decode_packet(raw_packet)
    assert decoded["from"] == src
    assert decoded["to"] == dst
    assert decoded["data"]["soc"] == 89.5
    assert decoded["data"]["solar_w"] == 1240


def test_lora_packet_corrupt_decoding():
    corrupted = b"\xff\xfe\x00\x12INVALID"
    decoded = decode_packet(corrupted)
    assert "error" in decoded
    assert "Corrupt packet" in decoded["error"]


def test_forward_to_space_dtn(caplog):
    caplog.set_level(logging.INFO)
    mock_router = MagicMock()
    with patch("sovereign_dc.space.dtn.router.DTNRouter", return_value=mock_router):
        forward_to_space_dtn("sensor-node-4", "BATTERY_CRITICAL_8_PERCENT")
        mock_router.queue_bundle.assert_called_once()
        assert "Emergency bundle" in caplog.text


def test_forward_to_space_dtn_failure(caplog):
    caplog.set_level(logging.INFO)
    with patch("sovereign_dc.space.dtn.router.DTNRouter", side_effect=Exception("DB Locked")):
        forward_to_space_dtn("sensor-node-4", "BATTERY_CRITICAL")
        assert "DTN routing fallback notice" in caplog.text


def test_run_lora_daemon(caplog):
    caplog.set_level(logging.INFO)
    with patch("time.sleep", side_effect=[None, StopIteration("End test")]):
        with pytest.raises(StopIteration):
            run_lora_daemon()
    assert "Starting Sovereign LoRa" in caplog.text
