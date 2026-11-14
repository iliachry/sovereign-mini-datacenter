"""Regression tests for the enterprise onboarding framework (SDK, manager, registry, schema)."""

from __future__ import annotations

import ast
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from sovereign_dc.enterprise import (
    AppLifecycleHandler,
    AppManifest,
    AppStatus,
    EnterpriseManager,
    EnterpriseRegistry,
    PowerPolicy,
    PowerPriority,
    ResourceQuotas,
    SMDCClient,
)
from sovereign_dc.enterprise import manager as manager_module
from sovereign_dc.enterprise import schema as schema_module
from sovereign_dc.events import Event, SovereignEventBus, get_event_bus
from sovereign_dc.security import pqc
from sovereign_dc.security.pqc import PQCAlgorithm, PQCSigner, get_node_pqc_keypair
from sovereign_dc.space.dtn.router import DTNRouter

UNREACHABLE = "http://127.0.0.1:9"


def _manifest(app_id: str, tier: PowerPriority = PowerPriority.L3_DEFERRABLE, **kwargs: Any) -> AppManifest:
    return AppManifest(
        name=kwargs.pop("name", app_id),
        app_id=app_id,
        entrypoint="python3 app.py",
        power=PowerPolicy(tier=tier),
        **kwargs,
    )


@pytest.fixture
def registry(tmp_path: Path) -> EnterpriseRegistry:
    return EnterpriseRegistry(registry_file=tmp_path / "registry.json")


@pytest.fixture
def manager(registry: EnterpriseRegistry) -> EnterpriseManager:
    return EnterpriseManager(registry=registry, event_bus=SovereignEventBus())


def _shed(mgr: EnterpriseManager, **payload: Any) -> None:
    mgr.event_bus.publish(Event(event_type="load_shedding.changed", source="test", payload=payload))


# --------------------------------------------------------------------------------------
# Finding 9: emit_telemetry fallback must reach the process-wide bus
# --------------------------------------------------------------------------------------


def test_emit_telemetry_fallback_reaches_manager_on_global_bus(registry: EnterpriseRegistry) -> None:
    registry.register_app(_manifest("sensor-node"))
    mgr = EnterpriseManager(registry=registry)  # subscribes to get_event_bus()
    mgr.start_app("sensor-node")

    client = SMDCClient(base_url=UNREACHABLE, timeout_sec=0.2)
    assert client.emit_telemetry("sensor-node", {"temperature_c": 22.4}) is True

    state = mgr.get_runtime_state("sensor-node")
    assert state is not None
    assert state.custom_metrics == {"temperature_c": 22.4}


# --------------------------------------------------------------------------------------
# Finding 10: one flat telemetry schema whichever path supplies it
# --------------------------------------------------------------------------------------


def test_get_telemetry_flattens_the_dashboard_status_document(monkeypatch: pytest.MonkeyPatch) -> None:
    api_document = {
        "node_id": "n1",
        "power": {"battery_soc": 17.5, "solar_watts": 42.0, "load_shedding_active": True},
        "thermal": {"coolant_celsius": 51.0, "rack_inlet_celsius": 23.0},
    }
    client = SMDCClient(base_url=UNREACHABLE)
    monkeypatch.setattr(client, "_http_get", lambda endpoint: api_document)

    telem = client.get_telemetry()

    assert telem["battery_soc"] == 17.5  # previously absent -> examples defaulted to 100 %
    assert telem["solar_watts"] == 42.0
    assert telem["coolant_temp_c"] == 51.0
    assert telem["ambient_temp_c"] == 23.0
    assert telem["power_state"] == "LOAD_SHEDDING"
    assert telem["source"] == "api"
    assert telem["power"]["battery_soc"] == 17.5  # nested sections stay available


def test_get_telemetry_fallback_uses_the_same_flat_keys() -> None:
    telem = SMDCClient(base_url=UNREACHABLE, timeout_sec=0.2).get_telemetry()
    assert {"solar_watts", "battery_soc", "coolant_temp_c", "ambient_temp_c", "power_state", "source"} <= set(telem)
    assert telem["source"] == "hal"
    assert isinstance(telem["battery_soc"], float)


def test_examples_and_scaffold_only_read_keys_the_sdk_provides(tmp_path: Path) -> None:
    provided = set(SMDCClient(base_url=UNREACHABLE, timeout_sec=0.2).get_telemetry())
    repo_root = Path(__file__).resolve().parents[1]
    sources = sorted((repo_root / "examples" / "enterprise_apps").glob("*/app.py"))
    assert sources, "example apps not found"

    scaffold_dir = EnterpriseRegistry.create_project_scaffold(tmp_path / "scaffold", _manifest("scaffold-app"))
    sources.append(scaffold_dir / "app.py")

    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        telemetry_vars = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "get_telemetry"
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in telemetry_vars
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                assert node.args[0].value in provided, f"{source}: unknown telemetry key {node.args[0].value!r}"


# --------------------------------------------------------------------------------------
# Finding 11: load-shedding level parsing and operator pauses
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("level", [2, 2.0, "2", "2.0", "L2", " l3 ", "critical", 3.7])
def test_numeric_and_named_levels_trigger_shedding(manager: EnterpriseManager, level: Any) -> None:
    manager.registry.register_app(_manifest("bg-job"))
    manager.start_app("bg-job")
    _shed(manager, level=level)
    state = manager.get_runtime_state("bg-job")
    assert state is not None and state.status == AppStatus.PAUSED


@pytest.mark.parametrize(
    "payload",
    [
        {"level": "banana"},
        {"level": None},
        {},
        {"level": float("nan")},
        {"level": True},
        {"mode": "???"},
        {"level": -1},
    ],
)
def test_unparseable_level_is_a_no_op_not_a_resume(manager: EnterpriseManager, payload: dict[str, Any]) -> None:
    manager.registry.register_app(_manifest("bg-job"))
    manager.start_app("bg-job")
    _shed(manager, level=2)
    assert manager.get_runtime_state("bg-job").status == AppStatus.PAUSED  # type: ignore[union-attr]

    _shed(manager, **payload)

    assert manager.get_runtime_state("bg-job").status == AppStatus.PAUSED  # type: ignore[union-attr]


def test_level_zero_resumes_only_apps_paused_by_shedding(manager: EnterpriseManager) -> None:
    manager.registry.register_app(_manifest("shed-job"))
    manager.registry.register_app(_manifest("operator-job"))
    manager.registry.register_app(_manifest("critical", tier=PowerPriority.L0_CRITICAL))
    for app_id in ("shed-job", "operator-job", "critical"):
        manager.start_app(app_id)

    manager.pause_app("operator-job", reason="maintenance window")
    _shed(manager, level=2)
    assert manager.get_runtime_state("shed-job").status == AppStatus.PAUSED  # type: ignore[union-attr]
    assert manager.get_runtime_state("critical").status == AppStatus.RUNNING  # type: ignore[union-attr]

    _shed(manager, level=0)

    assert manager.get_runtime_state("shed-job").status == AppStatus.RUNNING  # type: ignore[union-attr]
    assert manager.get_runtime_state("operator-job").status == AppStatus.PAUSED  # type: ignore[union-attr]

    ok, _ = manager.resume_app("operator-job", reason="maintenance done")
    assert ok


# --------------------------------------------------------------------------------------
# Honesty: estimated metrics, no process supervision; lifecycle handler wiring
# --------------------------------------------------------------------------------------


def test_runtime_metrics_are_labelled_as_estimates(manager: EnterpriseManager) -> None:
    manager.registry.register_app(_manifest("job", resources=ResourceQuotas(max_power_w=40.0)))
    before = manager.list_runtime_states()[0].to_dict()
    assert before["metrics_source"] == "none"

    manager.start_app("job")
    running = manager.get_runtime_state("job").to_dict()  # type: ignore[union-attr]
    assert running["metrics_source"] == "estimated"
    assert running["process_supervised"] is False
    assert running["pid"] is None
    assert running["health_status"] != "healthy"  # nothing probes health

    manager.stop_app("job")
    assert manager.get_runtime_state("job").to_dict()["metrics_source"] == "none"  # type: ignore[union-attr]


def test_lifecycle_handler_follows_manager_pause_and_resume(registry: EnterpriseRegistry) -> None:
    registry.register_app(_manifest("vision"))
    registry.register_app(_manifest("other"))
    mgr = EnterpriseManager(registry=registry)  # global bus, as in a real process
    mgr.start_app("vision")
    mgr.start_app("other")

    calls: list[str] = []
    handler = AppLifecycleHandler(
        "vision",
        client=SMDCClient(base_url=UNREACHABLE),
        on_pause=lambda: calls.append("pause"),
        on_resume=lambda: calls.append("resume"),
    )
    bystander = AppLifecycleHandler("unrelated-app", client=SMDCClient(base_url=UNREACHABLE))

    get_event_bus().publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 2}))
    assert handler.is_paused is True
    assert bystander.is_paused is False

    get_event_bus().publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 0}))
    assert handler.is_paused is False
    assert calls == ["pause", "resume"]

    handler.stop()
    mgr.pause_app("vision")
    assert handler.is_paused is False  # unsubscribed after stop()


# --------------------------------------------------------------------------------------
# Finding 12: scaffold templates must not interpolate untrusted strings into code/YAML
# --------------------------------------------------------------------------------------

HOSTILE_NAMES = [
    'Acme "Edge" App: v2 #1',
    "it's {curly} %s \\ backslash",
    'x"\nimport os\nos.system("touch PWNED")\n#',
    '""" + __import__("os").system("touch PWNED") + """',
    "multi\nline: yes\n- item",
]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_scaffold_yaml_round_trips_hostile_names(tmp_path: Path, name: str) -> None:
    manifest = _manifest("hostile-app", name=name, description='desc with "quotes": and\nnewline')
    dest = EnterpriseRegistry.create_project_scaffold(tmp_path / "app", manifest)

    loaded = yaml.safe_load((dest / "smdc-app.yaml").read_text(encoding="utf-8"))
    assert loaded == manifest.to_dict()

    reloaded = EnterpriseRegistry(registry_file=tmp_path / "r.json").load_manifest_file(dest / "smdc-app.yaml")
    assert reloaded is not None
    assert reloaded.name == name
    assert reloaded.to_dict() == manifest.to_dict()


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_scaffold_app_py_embeds_names_as_inert_literals(tmp_path: Path, name: str) -> None:
    hostile_id = 'id"); __import__("os").system("touch PWNED"); ("'
    manifest = AppManifest(name=name, app_id=hostile_id, entrypoint="python3 app.py")
    dest = EnterpriseRegistry.create_project_scaffold(tmp_path / "app", manifest)

    tree = ast.parse((dest / "app.py").read_text(encoding="utf-8"))
    constants = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("APP_ID", "APP_NAME")
    }
    assert constants == {"APP_ID": hostile_id, "APP_NAME": name}

    # The only calls to names the template did not write would come from injected code.
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert called <= {"SMDCClient", "AppLifecycleHandler", "main"}
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom)) and any(alias.name == "os" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert not (tmp_path / "PWNED").exists()


def test_scaffold_dockerfile_uses_json_exec_form(tmp_path: Path) -> None:
    manifest = AppManifest(name="D", app_id="docker-app", entrypoint='python3 "my app.py" --flag \'a"]\nRUN evil\'')
    dest = EnterpriseRegistry.create_project_scaffold(tmp_path / "app", manifest)
    lines = (dest / "Dockerfile").read_text(encoding="utf-8").splitlines()

    assert not any(line.startswith("RUN evil") for line in lines)
    entrypoint = next(line for line in lines if line.startswith("ENTRYPOINT "))
    cmd = next(line for line in lines if line.startswith("CMD "))
    assert json.loads(entrypoint.removeprefix("ENTRYPOINT ")) == ["python3"]
    assert json.loads(cmd.removeprefix("CMD ")) == ["my app.py", "--flag", 'a"]\nRUN evil']


# --------------------------------------------------------------------------------------
# Finding 13: packaging filter depth, path traversal, verifiable signature
# --------------------------------------------------------------------------------------


def _make_app_dir(base: Path, version: str = "1.0.0") -> Path:
    app_dir = base / "src-app"
    manifest = AppManifest(name="Pkg", app_id="pkg-app", version=version, entrypoint="python3 app.py")
    EnterpriseRegistry.create_project_scaffold(app_dir, manifest)
    return app_dir


def test_package_excludes_dotfiles_and_caches_at_every_depth(manager: EnterpriseManager, tmp_path: Path) -> None:
    app_dir = _make_app_dir(tmp_path)
    (app_dir / ".env").write_text("TOP_SECRET=1", encoding="utf-8")
    nested = app_dir / "sub" / "deeper"
    nested.mkdir(parents=True)
    (app_dir / "sub" / ".env").write_text("NESTED_SECRET=1", encoding="utf-8")
    (app_dir / "sub" / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (nested / ".git").mkdir()
    (nested / ".git" / "config").write_text("[core]", encoding="utf-8")
    cache = app_dir / "sub" / "__pycache__"
    cache.mkdir()
    (cache / "keep.cpython-312.pyc").write_bytes(b"\x00")
    (nested / "data.txt").write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (app_dir / "sub" / "leak").symlink_to(outside)

    out = tmp_path / "out" / "pkg.smdc-app"
    ok, msg, _ = manager.package_app(app_dir, output_path=out, sign_pqc=False)
    assert ok, msg

    with tarfile.open(out) as tar:
        names = set(tar.getnames())
    assert {"app.py", "smdc-app.yaml", "sub/keep.py", "sub/deeper/data.txt"} <= names
    leaked = {n for n in names if any(part.startswith(".") or part == "__pycache__" for part in n.split("/"))}
    assert leaked == set()
    assert "sub/leak" not in names


@pytest.mark.parametrize("version", ["1/../../escaped/pwn", "../up", "..", ".hidden", "1.0\n", "a b", ""])
def test_package_rejects_versions_that_are_not_plain_tokens(
    manager: EnterpriseManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    app_dir = _make_app_dir(tmp_path)
    data = json.loads((app_dir / "smdc-app.json").read_text(encoding="utf-8"))
    data["version"] = version
    (app_dir / "smdc-app.yaml").unlink()
    (app_dir / "smdc-app.json").write_text(json.dumps(data), encoding="utf-8")

    workdir = tmp_path / "deep" / "cwd"
    workdir.mkdir(parents=True)
    monkeypatch.chdir(workdir)
    before = {p for p in tmp_path.rglob("*")}

    ok, _, meta = manager.package_app(app_dir)  # default output name embeds the version

    assert ok is False
    assert meta == {}
    assert {p for p in tmp_path.rglob("*")} == before  # nothing written anywhere
    assert AppManifest.from_dict(data).validate() != []


def test_package_default_output_stays_in_cwd(
    manager: EnterpriseManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir = _make_app_dir(tmp_path, version="2.1.0-rc.1+build5")
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    ok, msg, _ = manager.package_app(app_dir, sign_pqc=False)
    assert ok, msg
    assert sorted(p.name for p in workdir.iterdir()) == [
        "pkg-app-2.1.0-rc.1+build5.smdc-app",
        "pkg-app-2.1.0-rc.1+build5.smdc-app.sig",
    ]


def test_verify_package_accepts_only_the_trusted_signer_and_untampered_archive(
    manager: EnterpriseManager, tmp_path: Path
) -> None:
    app_dir = _make_app_dir(tmp_path)
    out = tmp_path / "pkg.smdc-app"
    ok, _, meta = manager.package_app(app_dir, output_path=out, sign_pqc=True)
    assert ok
    node_key = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87)
    assert meta["pqc_signature"]["public_key_hex"] == node_key.public_key.hex()

    verify_package = manager_module.verify_package
    assert verify_package(out, node_key.public_key) == (True, "ok")
    assert verify_package(out, node_key.public_key.hex()) == (True, "ok")

    # A different (untrusted) key - even a perfectly valid one - is refused.
    stranger = PQCSigner(PQCAlgorithm.ML_DSA_87).generate_keypair()
    assert verify_package(out, stranger.public_key)[0] is False

    # An attacker who re-signs with their own key and rewrites the sidecar is refused too.
    sidecar = manager_module.package_sidecar_path(out)
    original_sidecar = sidecar.read_text(encoding="utf-8")
    forged = json.loads(original_sidecar)
    forged["pqc_signature"]["public_key_hex"] = stranger.public_key.hex()
    sidecar.write_text(json.dumps(forged), encoding="utf-8")
    assert verify_package(out, node_key.public_key)[0] is False

    # Metadata covered by the signature cannot be rewritten.
    relabelled = json.loads(original_sidecar)
    relabelled["version"] = "9.9.9"
    sidecar.write_text(json.dumps(relabelled), encoding="utf-8")
    assert verify_package(out, node_key.public_key) == (False, "invalid ML-DSA-87 signature")
    sidecar.write_text(original_sidecar, encoding="utf-8")

    # Tampered archive.
    out.write_bytes(out.read_bytes() + b"\x00")
    ok_tampered, reason = verify_package(out, node_key.public_key)
    assert ok_tampered is False and "SHA-256" in reason

    unsigned = tmp_path / "unsigned.smdc-app"
    manager.package_app(app_dir, output_path=unsigned, sign_pqc=False)
    assert verify_package(unsigned, node_key.public_key) == (False, "package is not signed")
    assert verify_package(tmp_path / "missing.smdc-app", node_key.public_key)[0] is False


def test_packages_from_separate_processes_share_one_signing_identity(
    manager: EnterpriseManager, tmp_path: Path
) -> None:
    app_dir = _make_app_dir(tmp_path)
    _, _, first = manager.package_app(app_dir, output_path=tmp_path / "a.smdc-app")
    pqc._CACHED_NODE_KEYPAIRS.clear()  # what a fresh process starts with
    _, _, second = manager.package_app(app_dir, output_path=tmp_path / "b.smdc-app")

    assert first["pqc_signature"]["key_id"] == second["pqc_signature"]["key_id"]
    trusted = bytes.fromhex(first["pqc_signature"]["public_key_hex"])
    assert manager_module.verify_package(tmp_path / "b.smdc-app", trusted) == (True, "ok")


# --------------------------------------------------------------------------------------
# Finding 16 (SDK part): DTN fallback uses the configured spool
# --------------------------------------------------------------------------------------


def test_send_dtn_bundle_fallback_uses_configured_spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sovereign_dc.config as config_module

    configured = tmp_path / "configured" / "spool.db"
    cfg = config_module.SovereignConfig(dtn_db_path=str(configured))
    monkeypatch.setattr(config_module, "get_config", lambda: cfg)
    default_paths: list[str] = []
    real_init = DTNRouter.__init__

    def recording_init(self: DTNRouter, *args: Any, **kwargs: Any) -> None:
        default_paths.append(str(kwargs.get("db_path", args[0] if args else "<built-in default>")))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(DTNRouter, "__init__", recording_init)

    client = SMDCClient(base_url=UNREACHABLE, timeout_sec=0.2)
    res = client.send_dtn_bundle("dtn://ground.earth/data", b"payload")

    assert res["status"] == "spooled"
    assert default_paths == [str(configured)]  # never the router's hard-coded default
    monkeypatch.setattr(DTNRouter, "__init__", real_init)
    assert DTNRouter(db_path=str(configured)).get_queue_stats()["queued_bundle_count"] == 1

    assert client.send_dtn_bundle("dtn://ground.earth/data", b"late", ttl_sec=-5)["status"] == "failed"


# --------------------------------------------------------------------------------------
# Finding 17: registry corruption must not lose data
# --------------------------------------------------------------------------------------


def test_one_corrupt_registry_entry_does_not_drop_or_destroy_the_others(tmp_path: Path) -> None:
    reg_file = tmp_path / "registry.json"
    good_a = _manifest("app-a").to_dict()
    good_c = _manifest("app-c").to_dict()
    corrupt = {"name": "broken", "app_id": "app-b", "resources": {"cpu_cores": "not-a-number"}}
    reg_file.write_text(
        json.dumps({"version": "1.0.0", "apps": [good_a, corrupt, "garbage", good_c]}), encoding="utf-8"
    )

    registry = EnterpriseRegistry(registry_file=reg_file)
    assert sorted(a.app_id for a in registry.list_apps()) == ["app-a", "app-c"]

    registry.register_app(_manifest("app-d"))  # triggers a save

    saved = json.loads(reg_file.read_text(encoding="utf-8"))["apps"]
    assert corrupt in saved and "garbage" in saved  # preserved verbatim for repair
    assert sorted(e["app_id"] for e in saved if isinstance(e, dict)) == ["app-a", "app-b", "app-c", "app-d"]
    assert sorted(a.app_id for a in EnterpriseRegistry(registry_file=reg_file).list_apps()) == [
        "app-a",
        "app-c",
        "app-d",
    ]


@pytest.mark.parametrize("content", ['{"apps": [{"app_id": "x"', "[]", '{"apps": 5}', "\x00\x01"])
def test_unparseable_registry_is_backed_up_before_being_replaced(tmp_path: Path, content: str) -> None:
    reg_file = tmp_path / "registry.json"
    reg_file.write_text(content, encoding="utf-8")

    registry = EnterpriseRegistry(registry_file=reg_file)
    assert registry.list_apps() == []
    backups = list(tmp_path.glob("registry.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == content

    registry.register_app(_manifest("fresh-app"))
    assert backups[0].read_text(encoding="utf-8") == content  # original still recoverable
    assert [a.app_id for a in EnterpriseRegistry(registry_file=reg_file).list_apps()] == ["fresh-app"]


def test_registry_is_not_overwritten_when_backup_is_impossible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg_file = tmp_path / "registry.json"
    reg_file.write_text("{broken", encoding="utf-8")

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("sovereign_dc.enterprise.registry.shutil.copy2", refuse)
    registry = EnterpriseRegistry(registry_file=reg_file)
    registry.register_app(_manifest("fresh-app"))

    assert reg_file.read_text(encoding="utf-8") == "{broken"


# --------------------------------------------------------------------------------------
# Finding 18 (schema part)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("app_id", ["valid-app\n", "valid-app\r\n", "ab", "UPPER-case", "has space", "a" * 65])
def test_app_id_must_fully_match_the_pattern(app_id: str) -> None:
    errors = AppManifest(name="n", app_id=app_id, entrypoint="x").validate()
    assert any("app_id" in e for e in errors)


@pytest.mark.parametrize("app_id", [12345, None, ["a", "b"], {"a": 1}, 1.5])
def test_non_string_app_id_is_a_validation_error_not_a_crash(app_id: Any) -> None:
    manifest = AppManifest.from_dict({"name": "n", "app_id": app_id, "entrypoint": "x"})
    errors = manifest.validate()  # used to raise TypeError
    assert any("app_id" in e for e in errors)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("resources", "cpu_cores", "nan"),
        ("resources", "cpu_cores", "inf"),
        ("resources", "max_power_w", -5),
        ("resources", "max_power_w", "nan"),
        ("resources", "storage_mb", -1),
        ("power", "min_battery_soc", "nan"),
        ("power", "min_solar_watts", -10),
    ],
)
def test_resource_and_power_numbers_must_be_finite_and_non_negative(section: str, field: str, value: Any) -> None:
    manifest = AppManifest.from_dict({"name": "n", "app_id": "valid-app", "entrypoint": "x", section: {field: value}})
    assert any(field in e for e in manifest.validate())


def test_valid_manifest_still_validates() -> None:
    assert _manifest("valid-app").validate() == []
    assert (
        AppManifest.from_dict({"name": "n", "app_id": "valid-app", "entrypoint": "x", "version": 1.0}).validate() == []
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("My App! v2", "my-app-v2"),
        ("  Smart_Grid  Analytics ", "smart-grid-analytics"),
        ("AI", "app-ai"),
        ("!!!", "app"),
    ],
)
def test_slugify_app_id_always_yields_a_valid_id(name: str, expected: str) -> None:
    slug = schema_module.slugify_app_id(name)
    assert slug == expected
    assert AppManifest(name=name, app_id=slug, entrypoint="x").validate() == []
    long_slug = schema_module.slugify_app_id("x" * 200)
    assert AppManifest(name="x" * 200, app_id=long_slug, entrypoint="x").validate() == []


# --------------------------------------------------------------------------------------
# Review round 2: HAL fallback honours the node configuration; bearer token support
# --------------------------------------------------------------------------------------


def test_get_telemetry_in_hardware_mode_reports_unavailable_not_simulated_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sovereign_dc.config as config_module

    cfg = config_module.SovereignConfig(hal_mode="hardware", power_exporter_url=f"{UNREACHABLE}/metrics")
    monkeypatch.setattr(config_module, "get_config", lambda: cfg)

    telem = SMDCClient(base_url=UNREACHABLE, timeout_sec=0.2).get_telemetry()

    assert telem == {
        "solar_watts": 0.0,
        "battery_soc": 0.0,
        "coolant_temp_c": None,
        "ambient_temp_c": None,
        "power_state": "UNKNOWN",
        "source": "unavailable",
    }


def test_get_telemetry_fallback_reads_the_configured_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    import sovereign_dc.config as config_module
    from sovereign_dc.hal import power as power_module
    from sovereign_dc.hal import thermal as thermal_module

    cfg = config_module.SovereignConfig(hal_mode="hardware", power_exporter_url="http://exporter.test:9101/metrics")
    monkeypatch.setattr(config_module, "get_config", lambda: cfg)
    calls: list[tuple[str, str, bool]] = []

    def fake_power(exporter_url: str = "default", simulation: bool = True) -> Any:
        calls.append(("power", exporter_url, simulation))
        return power_module.PowerReading(
            battery_soc=41.0,
            solar_watts=12.0,
            battery_voltage=50.0,
            system_load_watts=100.0,
            load_shedding_active=True,
            is_simulated=False,
        )

    def fake_thermal(exporter_url: str = "default", simulation: bool = True) -> Any:
        calls.append(("thermal", exporter_url, simulation))
        return thermal_module.ThermalReading(
            coolant_celsius=33.0, rack_inlet_celsius=21.0, rack_exhaust_celsius=35.0, is_simulated=False
        )

    monkeypatch.setattr(power_module, "read_power", fake_power)
    monkeypatch.setattr(thermal_module, "read_thermal", fake_thermal)

    telem = SMDCClient(base_url=UNREACHABLE, timeout_sec=0.2).get_telemetry()

    assert calls == [
        ("power", "http://exporter.test:9101/metrics", False),
        ("thermal", "http://exporter.test:9101/metrics", False),
    ]
    assert telem["source"] == "hal"
    assert telem["battery_soc"] == 41.0
    assert telem["power_state"] == "LOAD_SHEDDING"
    assert telem["is_simulated"] is False


def _capture_requests(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from sovereign_dc.enterprise import sdk as sdk_module

    captured: list[Any] = []

    def fake_urlopen(req: Any, timeout: float = 0.0) -> Any:
        captured.append(req)
        raise OSError("no network in tests")

    monkeypatch.setattr(sdk_module, "urlopen", fake_urlopen)
    return captured


def test_sdk_sends_bearer_token_from_argument_or_environment(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    captured = _capture_requests(monkeypatch)
    caplog.set_level("DEBUG")

    SMDCClient(base_url="http://node.test")._http_post("/api/enterprise/telemetry", {"a": 1})
    assert captured[-1].get_header("Authorization") is None  # no token configured: header absent

    monkeypatch.setenv("SMDC_DASHBOARD_TOKEN", " env-secret-token \n")
    env_client = SMDCClient(base_url="http://node.test")
    env_client._http_post("/api/enterprise/telemetry", {"a": 1})
    assert captured[-1].get_header("Authorization") == "Bearer env-secret-token"
    assert captured[-1].get_header("Content-type") == "application/json"
    env_client._http_get("/api/status")
    assert captured[-1].get_header("Authorization") == "Bearer env-secret-token"

    explicit = SMDCClient(base_url="http://node.test", token="arg-secret-token")  # noqa: S106
    explicit._http_post("/api/control/dtn-transmit", {})
    assert captured[-1].get_header("Authorization") == "Bearer arg-secret-token"

    # An explicit empty token opts out even when the environment variable is set.
    SMDCClient(base_url="http://node.test", token="")._http_post("/api/x", {})
    assert captured[-1].get_header("Authorization") is None

    assert "secret-token" not in caplog.text
    assert "secret-token" not in repr(vars(explicit).keys())


# --------------------------------------------------------------------------------------
# Tri-council round 1: SDK <-> dashboard DTN contract (lossless payload, TTL, source, priority)
# --------------------------------------------------------------------------------------


def _through_dashboard(client: SMDCClient, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Routes the client's HTTP POSTs into the real dashboard handler; returns the requests seen."""
    import io
    from unittest.mock import MagicMock

    from sovereign_dc.web.dashboard import DashboardHandler

    seen: list[dict[str, Any]] = []

    def post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        seen.append(payload)
        handler = DashboardHandler.__new__(DashboardHandler)
        handler.path = endpoint
        handler.headers = {"Content-Length": str(len(body))}
        handler.client_address = ("127.0.0.1", 0)
        handler.sse_max_events = 1
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.do_POST()
        return json.loads(handler.wfile.getvalue()) if handler.send_response.call_args.args[0] == 200 else {}

    monkeypatch.setattr(client, "_http_post", post)
    return seen


def _spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import sovereign_dc.config as config_module

    spool = tmp_path / "contract" / "spool.db"
    monkeypatch.setenv("DTN_DB_PATH", str(spool))
    config_module.reset_config()
    return spool


def test_sdk_bundle_survives_the_http_path_byte_for_byte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    spool = _spool(tmp_path, monkeypatch)
    client = SMDCClient(base_url="http://node.test")
    _through_dashboard(client, monkeypatch)

    raw = b"\xff\x00\x80"
    res = client.send_dtn_bundle("dtn://ground.earth/data", raw, ttl_sec=60)

    (bundle,) = DTNRouter(db_path=str(spool)).get_outbound_queue()
    assert bundle.payload == raw  # was ef bf bd 00 ef bf bd
    assert bundle.lifetime_seconds == 60  # was the 7-day default
    assert bundle.priority == 1  # NORMAL (the HTTP path used to default to EXPEDITED)
    assert bundle.source_eid == "dtn://smdc-dgx-01/api"  # was the bare node id
    assert res["status"] == "spooled" and res["bundle_id"] == bundle.bundle_id
    assert res["ttl_seconds"] == 60 and res["payload_sha256"] == hashlib.sha256(raw).hexdigest()


def test_sdk_http_and_fallback_paths_store_the_same_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spool = _spool(tmp_path, monkeypatch)
    kwargs: dict[str, Any] = {"ttl_sec": 120, "priority": 2, "source_eid": "dtn://app.local/sensor"}

    via_http = SMDCClient(base_url="http://node.test")
    seen = _through_dashboard(via_http, monkeypatch)
    http_res = via_http.send_dtn_bundle("dtn://ground.earth/data", "héllo", **kwargs)
    fallback_res = SMDCClient(base_url=UNREACHABLE, timeout_sec=0.2).send_dtn_bundle(
        "dtn://ground.earth/data", "héllo", **kwargs
    )

    assert seen[0]["payload"] == "héllo"  # text is also sent in the legacy field for older nodes
    first, second = DTNRouter(db_path=str(spool)).get_outbound_queue()
    for field in ("payload", "lifetime_seconds", "priority", "source_eid", "destination_eid"):
        assert getattr(first, field) == getattr(second, field), field
    assert first.source_eid == "dtn://app.local/sensor" and first.priority == 2
    for key in ("source", "destination", "priority", "ttl_seconds", "payload_bytes", "payload_sha256", "status"):
        assert http_res[key] == fallback_res[key], key


def test_sdk_does_not_claim_a_binary_bundle_an_old_node_did_not_store(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SMDCClient(base_url="http://node.test")
    # A node that predates payload_b64 answers without confirming what it stored.
    monkeypatch.setattr(client, "_http_post", lambda _e, _p: {"success": True, "bundle_id": "b1", "queued_bundles": 1})

    assert client.send_dtn_bundle("dtn://g.earth/x", b"\xff\x00\x80")["status"] == "error"
    assert client.send_dtn_bundle("dtn://g.earth/x", "plain text")["status"] == "spooled"
