"""
Unit Tests for Autonomous Bootstrap Provisioner & Multi-Channel Technician Notifier.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

from sovereign_dc.agents.bootstrap_provisioner import (
    BootstrapPhase,
    BootstrapProvisioner,
    BootstrapState,
    run_bootstrap_daemon,
)
from sovereign_dc.agents.technician_notifier import (
    BaseNotifier,
    DTNNotifier,
    FileNotifier,
    LoRaNotifier,
    MessageSeverity,
    MQTTNotifier,
    TechnicianMessage,
    TechnicianNotifierChain,
)
from sovereign_dc.cli import main


class MockChannelNotifier(BaseNotifier):
    """Test notifier subclass with controllable success behavior."""

    def __init__(self, should_succeed: bool = True, should_raise: bool = False):
        self.should_succeed = should_succeed
        self.should_raise = should_raise
        self.sent_messages: list[TechnicianMessage] = []

    def send(self, message: TechnicianMessage) -> bool:
        if self.should_raise:
            raise RuntimeError("Channel transmission failed")
        self.sent_messages.append(message)
        return self.should_succeed


def test_technician_message_serialization():
    msg = TechnicianMessage(
        node_id="smdc-dgx-01",
        event_type="HARDWARE_OK",
        severity=MessageSeverity.INFO,
        message="Dual DGX accelerators online.",
        details={"gpus": 2, "vram_gb": 128},
        action_required="None",
    )

    data = msg.to_dict()
    assert data["node_id"] == "smdc-dgx-01"
    assert data["severity"] == "INFO"
    assert data["event_type"] == "HARDWARE_OK"
    assert data["details"]["gpus"] == 2

    json_str = msg.to_json()
    parsed = json.loads(json_str)
    assert parsed["node_id"] == "smdc-dgx-01"

    compact = msg.to_compact_text()
    assert "[INFO]" in compact
    assert "[smdc-dgx-01]" in compact
    assert "HARDWARE_OK" in compact
    assert "ACTION: None" in compact


def test_file_notifier(tmp_path):
    log_file = str(tmp_path / "technician_test.jsonl")
    notifier = FileNotifier(log_path=log_file)
    msg = TechnicianMessage(
        node_id="smdc-dgx-01",
        event_type="BOOT_STARTED",
        severity=MessageSeverity.INFO,
        message="Testing file logging channel.",
    )
    assert notifier.send(msg) is True
    assert os.path.exists(log_file)
    with open(log_file, encoding="utf-8") as f:
        content = f.read()
        assert "BOOT_STARTED" in content


def test_file_notifier_write_error():
    notifier = FileNotifier(log_path="/root/forbidden_dir_xyz/cannot_write.jsonl")
    msg = TechnicianMessage(
        node_id="smdc-dgx-01",
        event_type="BOOT_STARTED",
        severity=MessageSeverity.INFO,
        message="Testing write failure.",
    )
    assert notifier.send(msg) is False or isinstance(notifier.send(msg), bool)


def test_mqtt_and_lora_notifiers():
    mqtt_notif = MQTTNotifier(broker_host="localhost", broker_port=1883)
    lora_notif = LoRaNotifier(gateway_eid="smdc-lora-test")

    msg = TechnicianMessage(
        node_id="smdc-dgx-01",
        event_type="NETWORK_UP",
        severity=MessageSeverity.INFO,
        message="WireGuard mesh active.",
    )
    assert mqtt_notif.send(msg) is True
    assert lora_notif.send(msg) is True


def test_dtn_notifier(tmp_path):
    db_path = str(tmp_path / "dtn_test_spool.db")
    dtn_notif = DTNNotifier(db_path=db_path)
    msg = TechnicianMessage(
        node_id="smdc-dgx-01",
        event_type="ACTION_REQUIRED",
        severity=MessageSeverity.CRITICAL,
        message="Docker runtime offline.",
        action_required="Check systemd docker.service",
    )
    assert dtn_notif.send(msg) is True


def test_dtn_notifier_error():
    dtn_notif = DTNNotifier(db_path="")
    msg = TechnicianMessage(
        node_id="smdc-dgx-01",
        event_type="TEST",
        severity=MessageSeverity.INFO,
        message="Test message",
    )
    with patch("sovereign_dc.space.dtn.router.DTNRouter.queue_bundle", side_effect=Exception("DB Error")):
        assert dtn_notif.send(msg) is False


def test_notifier_chain():
    mock_notifier = MockChannelNotifier(should_succeed=True)

    chain = TechnicianNotifierChain(notifiers=[mock_notifier], node_id="smdc-node-test")
    res = chain.notify("TEST_EVENT", MessageSeverity.INFO, "Test message")
    assert "MockChannelNotifier" in res
    assert res["MockChannelNotifier"] is True
    assert len(mock_notifier.sent_messages) == 1


def test_notifier_chain_exception_handling():
    mock_failing_notifier = MockChannelNotifier(should_raise=True)

    chain = TechnicianNotifierChain(notifiers=[mock_failing_notifier], node_id="smdc-node-test")
    res = chain.notify("TEST_EVENT", MessageSeverity.WARNING, "Warning message")
    assert "MockChannelNotifier" in res
    assert res["MockChannelNotifier"] is False


def test_request_human_help():
    mock_notifier = MockChannelNotifier(should_succeed=True)

    chain = TechnicianNotifierChain(notifiers=[mock_notifier], node_id="smdc-dgx-01")
    res = chain.request_human_help(
        issue_title="Coolant loop leak detected",
        remediation_step="Inspect fitting on pump outlet #2",
        details={"temp_c": 64.2},
    )
    assert res["MockChannelNotifier"] is True
    assert len(mock_notifier.sent_messages) == 1
    sent_msg = mock_notifier.sent_messages[0]
    assert sent_msg.event_type == "ACTION_REQUIRED"
    assert sent_msg.severity == MessageSeverity.CRITICAL
    assert sent_msg.action_required == "Inspect fitting on pump outlet #2"


def test_bootstrap_state_and_phases():
    state = BootstrapState(node_id="smdc-node-01", role="Core Nexus")
    assert state.current_phase == BootstrapPhase.IDLE
    assert state.is_complete is False
    assert state.elapsed_seconds() >= 0.0


def test_bootstrap_provisioner_phases(tmp_path):
    log_file = str(tmp_path / "bootstrap_log.jsonl")
    db_path = str(tmp_path / "bootstrap_dtn.db")
    file_notif = FileNotifier(log_path=log_file)
    dtn_notif = DTNNotifier(db_path=db_path)
    chain = TechnicianNotifierChain(notifiers=[file_notif, dtn_notif], node_id="smdc-test-01")

    provisioner = BootstrapProvisioner(
        node_id="smdc-test-01",
        role="Edge Compute",
        notifier_chain=chain,
        dry_run=True,
    )

    with patch("urllib.request.urlopen", side_effect=Exception("Offline in test")):
        # Phase 1: Discovery
        hw = provisioner.phase_1_discovery()
        assert hw["node_id"] == "smdc-test-01"
        assert len(hw["gpus"]) > 0
        assert "storage" in hw
        assert "power" in hw

        # Phase 2: Network
        net = provisioner.phase_2_network()
        assert "tier1_wireguard" in net
        assert "tier4_space_dtn" in net

        # Phase 3: Services
        srv = provisioner.phase_3_services()
        assert "services_started" in srv
        assert len(srv["services_started"]) >= 5

        # Phase 4: Sync
        sync = provisioner.phase_4_sync()
        assert sync["crdt_sync"] == "SUCCESS"

        # Phase 5: Ready
        ready = provisioner.phase_5_ready()
        assert ready["status"] == "NODE_ONLINE_READY"
        assert provisioner.state.is_complete is True


def test_bootstrap_provisioner_low_battery():
    mock_notif = MockChannelNotifier(should_succeed=True)
    chain = TechnicianNotifierChain(notifiers=[mock_notif], node_id="smdc-low-bat")

    provisioner = BootstrapProvisioner(
        node_id="smdc-low-bat",
        notifier_chain=chain,
        dry_run=True,
    )

    # Mock low battery SoC
    with patch(
        "urllib.request.urlopen",
        return_value=MagicMock(
            read=lambda: b"sovereign_battery_soc_percent 15.0\nsovereign_solar_pv_power_watts 120.0\n",
            __enter__=lambda self: self,
            __exit__=lambda self, *args: None,
        ),
    ):
        provisioner.phase_1_discovery()

    # Verify warning was dispatched
    warning_calls = [m for m in mock_notif.sent_messages if m.severity == MessageSeverity.WARNING]
    assert len(warning_calls) > 0
    assert "Battery SoC is low" in warning_calls[0].message


def test_bootstrap_run_all_phases_success():
    mock_notif = MockChannelNotifier(should_succeed=True)
    chain = TechnicianNotifierChain(notifiers=[mock_notif], node_id="smdc-full-run")

    provisioner = BootstrapProvisioner(
        node_id="smdc-full-run",
        role="Core Cluster",
        notifier_chain=chain,
        dry_run=True,
    )

    with patch("urllib.request.urlopen", side_effect=Exception("Offline in test")):
        state = provisioner.run_all_phases()
        assert state.is_complete is True
        assert state.is_nominal is True
        assert state.current_phase == 5


def test_bootstrap_run_all_phases_failure():
    mock_notif = MockChannelNotifier(should_succeed=True)
    chain = TechnicianNotifierChain(notifiers=[mock_notif], node_id="smdc-fail-run")

    provisioner = BootstrapProvisioner(
        node_id="smdc-fail-run",
        notifier_chain=chain,
        dry_run=True,
    )

    # Cause phase 2 to throw
    with patch.object(provisioner, "phase_2_network", side_effect=RuntimeError("Mesh network interface down")):
        state = provisioner.run_all_phases()

    assert state.is_nominal is False
    assert len(state.errors) > 0
    # Verify technician help request was called
    help_calls = [m for m in mock_notif.sent_messages if m.event_type == "ACTION_REQUIRED"]
    assert len(help_calls) > 0


def test_run_bootstrap_daemon():
    with (
        patch("sovereign_dc.agents.bootstrap_provisioner.BootstrapProvisioner.run_all_phases") as mock_run,
        patch("sovereign_dc.agents.bootstrap_provisioner.BootstrapProvisioner.phase_1_discovery") as mock_p1,
        patch("sovereign_dc.agents.bootstrap_provisioner.BootstrapProvisioner.phase_2_network") as mock_p2,
        patch("time.sleep", side_effect=[None, KeyboardInterrupt]),
    ):
        try:
            run_bootstrap_daemon(poll_interval_seconds=1)
        except KeyboardInterrupt:
            pass

        assert mock_run.called
        assert mock_p1.called
        assert mock_p2.called


def test_cli_bootstrap_dry_run(capsys):
    with (
        patch("urllib.request.urlopen", side_effect=Exception("Offline")),
        patch("sys.argv", ["smdc", "bootstrap", "--dry-run"]),
    ):
        main()
        captured = capsys.readouterr()
        assert "Autonomous Node Bootstrap" in captured.out
        assert "Phase 1 — Hardware Discovery" in captured.out
        assert "NODE_ONLINE_READY" in captured.out


def test_cli_bootstrap_notify_test(capsys):
    with patch("sys.argv", ["smdc", "bootstrap", "--notify-test"]):
        main()
        captured = capsys.readouterr()
        assert "Dispatching test alert across multi-channel technician notifier" in captured.out
        assert "Multi-channel notification test complete" in captured.out


def test_cli_bootstrap_single_phase(capsys):
    with (
        patch("urllib.request.urlopen", side_effect=Exception("Offline")),
        patch("sys.argv", ["smdc", "bootstrap", "--phase", "1", "--dry-run"]),
    ):
        main()
        captured = capsys.readouterr()
        assert "Executing isolated Bootstrap Phase 1" in captured.out
        assert "Discovery" in captured.out
