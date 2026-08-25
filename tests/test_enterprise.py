"""Unit and Integration Tests for Sovereign Mini Datacenter Enterprise Application Framework.

Verifies schema validation, application registry, directory scaffolding, lifecycle supervision,
load shedding adaptation, zero-dependency SDK, PQC packaging, and CLI commands.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sovereign_dc.cli import (
    cmd_app_init,
    cmd_app_list,
    cmd_app_package,
    cmd_app_register,
    cmd_app_restart,
    cmd_app_start,
    cmd_app_status,
    cmd_app_stop,
    cmd_app_test,
    cmd_app_unregister,
    cmd_app_validate,
)
from sovereign_dc.enterprise import (
    AppCategory,
    AppLifecycleHandler,
    AppManifest,
    AppStatus,
    EnterpriseManager,
    EnterpriseRegistry,
    HealthProbe,
    NetworkPolicy,
    PowerPolicy,
    PowerPriority,
    ResourceQuotas,
    RuntimeType,
    SMDCClient,
    StoragePolicy,
)
from sovereign_dc.events import Event, SovereignEventBus


class TestEnterpriseSchema(unittest.TestCase):
    """Tests schema validation, enums, serialization, and deserialization."""

    def test_enums(self) -> None:
        self.assertEqual(AppCategory.IOT, "iot")
        self.assertEqual(AppCategory.AI_INFERENCE, "ai_inference")
        self.assertEqual(PowerPriority.L0_CRITICAL, "L0_CRITICAL")
        self.assertEqual(RuntimeType.PROCESS, "process")
        self.assertEqual(AppStatus.RUNNING, "running")

    def test_resource_quotas(self) -> None:
        q = ResourceQuotas(cpu_cores=2.0, ram_mb=1024, gpu_vram_mb=2048, storage_mb=4096, gpu_required=True)
        d = q.to_dict()
        self.assertEqual(d["cpu_cores"], 2.0)
        self.assertEqual(d["gpu_vram_mb"], 2048)
        reloaded = ResourceQuotas.from_dict(d)
        self.assertEqual(reloaded.ram_mb, 1024)
        self.assertTrue(reloaded.gpu_required)

        # Empty dict fallback
        empty = ResourceQuotas.from_dict(None)
        self.assertEqual(empty.cpu_cores, 1.0)

    def test_power_policy(self) -> None:
        p = PowerPolicy(tier=PowerPriority.L0_CRITICAL, min_battery_soc=20.0, min_solar_watts=0.0)
        d = p.to_dict()
        self.assertEqual(d["tier"], "L0_CRITICAL")
        reloaded = PowerPolicy.from_dict(d)
        self.assertEqual(reloaded.tier, PowerPriority.L0_CRITICAL)
        self.assertEqual(reloaded.min_battery_soc, 20.0)

        # Invalid tier fallback
        fallback = PowerPolicy.from_dict({"tier": "INVALID_TIER"})
        self.assertEqual(fallback.tier, PowerPriority.L1_STANDARD)

    def test_network_and_storage_policy(self) -> None:
        net = NetworkPolicy(ports=[8080, 8081], expose_wireguard=True, space_dtn_enabled=True)
        self.assertIn(8080, net.ports)
        self.assertTrue(net.space_dtn_enabled)
        reloaded_net = NetworkPolicy.from_dict(net.to_dict())
        self.assertEqual(reloaded_net.ports, [8080, 8081])

        st = StoragePolicy(persistent_volume="app-vol", mount_point="/data", backup_enabled=True)
        self.assertEqual(st.mount_point, "/data")
        reloaded_st = StoragePolicy.from_dict(st.to_dict())
        self.assertEqual(reloaded_st.persistent_volume, "app-vol")

        # Health probe
        probe = HealthProbe(type="http", endpoint="/healthz", port=8080)
        reloaded_probe = HealthProbe.from_dict(probe.to_dict())
        self.assertEqual(reloaded_probe.endpoint, "/healthz")

    def test_manifest_validation(self) -> None:
        # Valid manifest
        manifest = AppManifest(
            name="Valid Workload",
            app_id="valid-workload-01",
            version="1.0.0",
            category=AppCategory.IOT,
            entrypoint="python3 main.py",
        )
        errors = manifest.validate()
        self.assertEqual(len(errors), 0)

        # Invalid manifest errors
        invalid_manifest = AppManifest(
            name="",
            app_id="Invalid ID With Spaces!",
            entrypoint="",
            resources=ResourceQuotas(cpu_cores=-1.0, ram_mb=10),
            power=PowerPolicy(min_battery_soc=150.0),
            network=NetworkPolicy(ports=[99999]),
        )
        errs = invalid_manifest.validate()
        self.assertGreater(len(errs), 4)

    def test_manifest_json_serialization(self) -> None:
        manifest = AppManifest(
            name="Test App",
            app_id="test-app",
            version="2.1.0",
            category=AppCategory.AI_INFERENCE,
            entrypoint="python3 run.py",
            resources=ResourceQuotas(gpu_vram_mb=4096, gpu_required=True),
        )
        json_str = manifest.to_json()
        reloaded = AppManifest.from_json(json_str)
        self.assertEqual(reloaded.name, "Test App")
        self.assertEqual(reloaded.app_id, "test-app")
        self.assertEqual(reloaded.version, "2.1.0")
        self.assertEqual(reloaded.category, AppCategory.AI_INFERENCE)
        self.assertTrue(reloaded.resources.gpu_required)


class TestEnterpriseRegistry(unittest.TestCase):
    """Tests application scaffolding, discovery, registration, and manifest file I/O."""

    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.test_dir.name)
        self.reg_file = self.base_path / "registry.json"
        self.registry = EnterpriseRegistry(registry_file=self.reg_file)

    def tearDown(self) -> None:
        self.test_dir.cleanup()

    def test_scaffold_archetypes(self) -> None:
        # 1. IoT Archetype
        iot = EnterpriseRegistry.scaffold_manifest("IoT Gateway", "iot-gw", category=AppCategory.IOT)
        self.assertEqual(iot.power.tier, PowerPriority.L0_CRITICAL)
        self.assertTrue(iot.network.lora_heartbeat)

        # 2. AI Inference Archetype
        ai = EnterpriseRegistry.scaffold_manifest(
            "Vision AI", "vision-ai", category=AppCategory.AI_INFERENCE, gpu_required=True
        )
        self.assertTrue(ai.resources.gpu_required)
        self.assertEqual(ai.power.tier, PowerPriority.L2_BACKGROUND)

        # 3. Spatial Media Archetype
        spatial = EnterpriseRegistry.scaffold_manifest(
            "Spatial Twin", "spatial-twin", category=AppCategory.SPATIAL_MEDIA
        )
        self.assertEqual(spatial.category, AppCategory.SPATIAL_MEDIA)

        # 4. Distributed Archetype
        dist = EnterpriseRegistry.scaffold_manifest("DePIN Node", "depin-node", category=AppCategory.DISTRIBUTED)
        self.assertTrue(dist.network.space_dtn_enabled)

    def test_create_project_scaffold_and_load(self) -> None:
        app_dir = self.base_path / "my-iot-app"
        manifest = EnterpriseRegistry.scaffold_manifest("My IoT App", "my-iot-app", category=AppCategory.IOT)
        created_path = EnterpriseRegistry.create_project_scaffold(app_dir, manifest, create_sample_code=True)

        self.assertTrue((created_path / "smdc-app.yaml").exists())
        self.assertTrue((created_path / "smdc-app.json").exists())
        self.assertTrue((created_path / "app.py").exists())
        self.assertTrue((created_path / "Dockerfile").exists())
        self.assertTrue((created_path / "README.md").exists())

        # Load from disk
        loaded = self.registry.load_manifest_file(created_path / "smdc-app.yaml")
        self.assertIsNotNone(loaded)
        if loaded:
            self.assertEqual(loaded.app_id, "my-iot-app")

    def test_register_and_unregister_app(self) -> None:
        manifest = AppManifest(name="Service Alpha", app_id="service-alpha", entrypoint="python3 main.py")
        ok, errs = self.registry.register_app(manifest, manifest_path=self.base_path / "app.yaml")
        self.assertTrue(ok)
        self.assertEqual(len(self.registry.list_apps()), 1)
        self.assertEqual(self.registry.get_app("service-alpha"), manifest)

        # Persistence reload
        reloaded_reg = EnterpriseRegistry(registry_file=self.reg_file)
        self.assertEqual(len(reloaded_reg.list_apps()), 1)
        self.assertIsNotNone(reloaded_reg.get_app("service-alpha"))

        # Unregister
        unreg_ok = reloaded_reg.unregister_app("service-alpha")
        self.assertTrue(unreg_ok)
        self.assertEqual(len(reloaded_reg.list_apps()), 0)

    def test_discover_apps(self) -> None:
        app1_dir = self.base_path / "app1"
        app2_dir = self.base_path / "app2"
        m1 = AppManifest(name="App One", app_id="app-one", entrypoint="python3 a.py")
        m2 = AppManifest(name="App Two", app_id="app-two", entrypoint="python3 b.py")
        EnterpriseRegistry.create_project_scaffold(app1_dir, m1)
        EnterpriseRegistry.create_project_scaffold(app2_dir, m2)

        discovered = self.registry.discover_apps(search_dirs=[self.base_path])
        self.assertEqual(len(discovered), 2)
        app_ids = [a.app_id for a in discovered]
        self.assertIn("app-one", app_ids)
        self.assertIn("app-two", app_ids)


class TestEnterpriseManager(unittest.TestCase):
    """Tests application process supervision, health probing, load shedding, and packaging."""

    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.test_dir.name)
        self.reg_file = self.base_path / "registry.json"
        self.event_bus = SovereignEventBus()
        self.registry = EnterpriseRegistry(registry_file=self.reg_file)
        self.manager = EnterpriseManager(registry=self.registry, event_bus=self.event_bus)

    def tearDown(self) -> None:
        self.test_dir.cleanup()

    def test_lifecycle_start_stop_restart(self) -> None:
        manifest = AppManifest(
            name="Batch Processor",
            app_id="batch-proc",
            category=AppCategory.AI_INFERENCE,
            entrypoint="python3 batch.py",
            resources=ResourceQuotas(cpu_cores=2.0, ram_mb=1024, max_power_w=50.0),
            power=PowerPolicy(tier=PowerPriority.L2_BACKGROUND),
        )
        self.registry.register_app(manifest)

        # 1. Start
        ok, msg = self.manager.start_app("batch-proc")
        self.assertTrue(ok)
        state = self.manager.get_runtime_state("batch-proc")
        self.assertIsNotNone(state)
        if state:
            self.assertEqual(state.status, AppStatus.RUNNING)
            self.assertGreater(state.power_draw_w, 0.0)

        # 2. Duplicate start
        ok_dup, _ = self.manager.start_app("batch-proc")
        self.assertTrue(ok_dup)

        # 3. Restart
        restart_ok, _ = self.manager.restart_app("batch-proc")
        self.assertTrue(restart_ok)
        self.assertEqual(self.manager.get_runtime_state("batch-proc").restart_count, 1)

        # 4. Stop
        stop_ok, _ = self.manager.stop_app("batch-proc")
        self.assertTrue(stop_ok)
        self.assertEqual(self.manager.get_runtime_state("batch-proc").status, AppStatus.STOPPED)

    def test_load_shedding_event_adaptation(self) -> None:
        # Register an L0 (Critical) app and an L2 (Background) app
        m_crit = AppManifest(
            name="Critical Telemetry",
            app_id="crit-telem",
            entrypoint="python3 telem.py",
            power=PowerPolicy(tier=PowerPriority.L0_CRITICAL),
        )
        m_bg = AppManifest(
            name="Model Retraining",
            app_id="retrain-job",
            entrypoint="python3 train.py",
            power=PowerPolicy(tier=PowerPriority.L2_BACKGROUND),
        )
        self.registry.register_app(m_crit)
        self.registry.register_app(m_bg)

        self.manager.start_app("crit-telem")
        self.manager.start_app("retrain-job")

        # Simulate Battery Drop (Load Shedding Level 2)
        self.event_bus.publish(
            Event(
                event_type="load_shedding.changed",
                source="sentinel_copilot",
                payload={"level": 2, "battery_soc": 35.0},
            )
        )

        # Verify: L2 background job paused, L0 critical job still running
        self.assertEqual(self.manager.get_runtime_state("retrain-job").status, AppStatus.PAUSED)
        self.assertEqual(self.manager.get_runtime_state("crit-telem").status, AppStatus.RUNNING)

        # Simulate Solar Recovery (Load Shedding Level 0)
        self.event_bus.publish(
            Event(
                event_type="load_shedding.changed",
                source="sentinel_copilot",
                payload={"level": 0, "battery_soc": 85.0, "solar_w": 1200.0},
            )
        )

        # Verify: L2 job resumed
        self.assertEqual(self.manager.get_runtime_state("retrain-job").status, AppStatus.RUNNING)

    def test_telemetry_event_ingestion(self) -> None:
        manifest = AppManifest(name="Sensor Node", app_id="sensor-node", entrypoint="python3 sensor.py")
        self.registry.register_app(manifest)
        self.manager.start_app("sensor-node")

        self.event_bus.publish(
            Event(
                event_type="enterprise.sensor-node.telemetry",
                source="sensor-node",
                payload={"app_id": "sensor-node", "metrics": {"temperature_c": 22.4, "pressure_bar": 1.01}},
            )
        )

        state = self.manager.get_runtime_state("sensor-node")
        self.assertIsNotNone(state)
        if state:
            self.assertEqual(state.custom_metrics.get("temperature_c"), 22.4)

    def test_package_app_with_pqc_signature(self) -> None:
        app_dir = self.base_path / "pkg-app"
        manifest = AppManifest(name="Package Test", app_id="pkg-test", entrypoint="python3 main.py")
        EnterpriseRegistry.create_project_scaffold(app_dir, manifest)

        out_pkg = self.base_path / "pkg-test-1.0.0.smdc-app"
        ok, msg, meta = self.manager.package_app(app_dir, output_path=out_pkg, sign_pqc=True)

        self.assertTrue(ok)
        self.assertTrue(out_pkg.exists())
        self.assertIn("sha256", meta)
        self.assertIsNotNone(meta["pqc_signature"])
        self.assertEqual(meta["pqc_signature"]["algorithm"], "NIST-FIPS-204-ML-DSA-87")


class TestEnterpriseSDK(unittest.TestCase):
    """Tests zero-dependency SMDCClient and AppLifecycleHandler."""

    def test_client_fallback_telemetry(self) -> None:
        client = SMDCClient(base_url="http://127.0.0.1:9999")  # Non-existent server
        telem = client.get_telemetry()
        self.assertIn("solar_watts", telem)
        self.assertIn("battery_soc", telem)

    def test_client_emit_telemetry(self) -> None:
        client = SMDCClient(base_url="http://127.0.0.1:9999")
        ok = client.emit_telemetry("my-app", {"temperature": 25.0})
        self.assertTrue(ok)

    def test_client_send_dtn_bundle(self) -> None:
        client = SMDCClient(base_url="http://127.0.0.1:9999")
        res = client.send_dtn_bundle("dtn://ground-station.earth/data", b"Sample data payload")
        self.assertIn("status", res)
        self.assertIn("bundle_id", res)

    def test_client_dynamic_pricing(self) -> None:
        client = SMDCClient(base_url="http://127.0.0.1:9999")
        quote = client.get_dynamic_pricing(soc=85.0, solar_w=850.0)
        self.assertIn("final_unit_price", quote)

    def test_lifecycle_handler(self) -> None:
        pause_called = []
        resume_called = []

        handler = AppLifecycleHandler(
            app_id="app-1",
            on_pause=lambda: pause_called.append(True),
            on_resume=lambda: resume_called.append(True),
        )

        self.assertTrue(handler.is_running)
        self.assertFalse(handler.is_paused)

        handler.pause()
        self.assertTrue(handler.is_paused)
        self.assertEqual(len(pause_called), 1)

        handler.resume()
        self.assertFalse(handler.is_paused)
        self.assertEqual(len(resume_called), 1)

        handler.stop()
        self.assertFalse(handler.is_running)


class TestEnterpriseCLI(unittest.TestCase):
    """Tests all CLI commands under `smdc app`."""

    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.test_dir.name)

    def tearDown(self) -> None:
        self.test_dir.cleanup()

    def test_cli_init_validate_register_list_status_package(self) -> None:
        app_dir = self.base_path / "my-cli-app"

        import argparse

        # 1. Init
        args_init = argparse.Namespace(
            name="Smart Factory IoT",
            app_id="smart-factory-iot",
            category="iot",
            runtime="process",
            entrypoint="python3 sensor.py",
            gpu=False,
            power_tier="L0_CRITICAL",
            target_dir=str(app_dir),
        )
        cmd_app_init(args_init)
        self.assertTrue((app_dir / "smdc-app.yaml").exists())

        # 2. Validate
        args_val = argparse.Namespace(path=str(app_dir))
        cmd_app_validate(args_val)

        # 3. Register
        args_reg = argparse.Namespace(path=str(app_dir))
        cmd_app_register(args_reg)

        # 4. List
        args_list = argparse.Namespace()
        cmd_app_list(args_list)

        # 5. Start, Status, Restart, Stop
        args_action = argparse.Namespace(app_id="smart-factory-iot")
        cmd_app_start(args_action)
        cmd_app_status(args_action)
        cmd_app_restart(args_action)
        cmd_app_stop(args_action)

        # 6. Package
        pkg_out = self.base_path / "factory.smdc-app"
        args_pkg = argparse.Namespace(path=str(app_dir), output=str(pkg_out), no_pqc=False)
        cmd_app_package(args_pkg)
        self.assertTrue(pkg_out.exists())

        # 7. Unregister
        cmd_app_unregister(args_action)

    def test_cli_framework_self_test(self) -> None:
        import argparse

        args_test = argparse.Namespace()
        cmd_app_test(args_test)
