import logging
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from sovereign_dc.mesh import mesh_sync
from sovereign_dc.mesh.mesh_sync import MeshNode, check_peer_health, load_peers, sync_state_with_peer


def test_mesh_node_init():
    node = MeshNode("test-node", "100.64.0.99", "dtn://test.space", "Edge")
    assert node.node_id == "test-node"
    assert node.wireguard_ip == "100.64.0.99"
    assert node.dtn_eid == "dtn://test.space"
    assert node.role == "Edge"
    assert node.is_online is False


def test_load_peers_defaults():
    peers = load_peers()
    assert len(peers) == 3
    node_ids = [p.node_id for p in peers]
    assert "smdc-node-01" in node_ids
    assert "smdc-node-02" in node_ids
    assert "smdc-node-03" in node_ids


def test_check_peer_health_self():
    node = MeshNode(mesh_sync.NODE_ID, "100.64.0.1", "dtn://local.space", "Core")
    assert check_peer_health(node) is True


def test_check_peer_health_online():
    node = MeshNode("smdc-node-02", "100.64.0.2", "dtn://remote.space", "Edge")
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        assert check_peer_health(node) is True


def test_check_peer_health_offline():
    node = MeshNode("smdc-node-02", "100.64.0.2", "dtn://remote.space", "Edge")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        assert check_peer_health(node) is False


def test_sync_state_with_peer(caplog):
    caplog.set_level(logging.INFO)
    node = MeshNode("smdc-node-02", "100.64.0.2", "dtn://remote.space", "Edge")
    sync_state_with_peer(node)
    assert "Syncing state with mesh peer 'smdc-node-02'" in caplog.text


def test_run_mesh_daemon_loop(caplog):
    caplog.set_level(logging.INFO)
    peer1 = MeshNode("smdc-node-99", "100.64.0.99", "dtn://peer.space", "Edge")

    with patch("sovereign_dc.mesh.mesh_sync.load_peers", return_value=[peer1]):
        with patch("sovereign_dc.mesh.mesh_sync.check_peer_health", side_effect=[True, False]):
            with patch("time.sleep", side_effect=StopIteration("End test")):
                with pytest.raises(StopIteration):
                    mesh_sync.run_mesh_daemon()
    assert "Starting Sovereign Mesh Daemon" in caplog.text
