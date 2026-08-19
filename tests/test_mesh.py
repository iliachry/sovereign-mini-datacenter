import pytest
import os
import yaml
from sovereign_dc.mesh.lora.meshtastic_gateway import encode_packet, decode_packet

def test_cluster_config_validity():
    config_path = os.path.join(os.path.dirname(__file__), "..", "software", "mesh", "cluster_config.yaml")
    assert os.path.exists(config_path)
    with open(config_path, "r", encoding="utf-8") as f:
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
