"""Regression tests for Tri-Council Round 39 findings."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from sovereign_dc import cli
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import (
    AppManifest,
    HealthProbe,
    NetworkPolicy,
    PowerPolicy,
    PowerPriority,
    ResourceQuotas,
    StoragePolicy,
)
from sovereign_dc.mcp.resources import read_manifest_resource

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_round39_manifest_from_dict_rejects_coercion_and_invalid_enums(tmp_path: Path) -> None:
    """Blank YAML scalars, float/bool coercions, and invalid enums fail manifest validation."""
    base_data = {
        "name": "Valid App",
        "app_id": "valid-app",
        "version": "1.0.0",
        "entrypoint": "python3 app.py",
        "environment": {"LOG_LEVEL": "info"},
    }
    valid = AppManifest.from_dict(base_data)
    assert valid.validate() == []
    assert AppManifest.from_json(valid.to_json()).validate() == []
    assert "_parse_errors" not in valid.to_dict()
    assert "_parse_errors" not in valid.resources.to_dict()
    assert "_parse_errors" not in valid.power.to_dict()
    assert "_parse_errors" not in valid.network.to_dict()
    assert "_parse_errors" not in valid.storage.to_dict()
    assert "_parse_errors" not in valid.health_check.to_dict()

    # Preserve standalone PowerPolicy fallback and YAML float version backward compatibility.
    assert PowerPolicy.from_dict({"tier": "INVALID_TIER"}).tier == PowerPriority.L1_STANDARD
    assert AppManifest.from_dict({**base_data, "version": 1.0}).validate() == []

    # Empty dicts return clean defaults; non-dict inputs record parse errors.
    for section_key, policy_cls in (
        ("resources", ResourceQuotas),
        ("power", PowerPolicy),
        ("network", NetworkPolicy),
        ("storage", StoragePolicy),
        ("health_check", HealthProbe),
    ):
        assert policy_cls.from_dict({}) == policy_cls()
        assert AppManifest(**base_data, **{section_key: policy_cls.from_dict("bad")}).validate()  # type: ignore[arg-type]

    assert AppManifest.from_dict("not-a-dict").validate()  # type: ignore[arg-type]

    # Invalid fields and explicit null/non-mapping sub-sections must fail validation.
    invalid_cases = [
        {**base_data, "name": None},
        {**base_data, "app_id": None},
        {**base_data, "version": None},
        {**base_data, "version": True},
        {**base_data, "entrypoint": None},
        {**base_data, "description": None},
        {**base_data, "author": None},
        {**base_data, "author": "   "},
        {**base_data, "category": "bogus-category"},
        {**base_data, "category": 123},
        {**base_data, "runtime": "bogus-runtime"},
        {**base_data, "runtime": 123},
        {**base_data, "environment": "not-a-dict"},
        {**base_data, "environment": {"FOO": None}},
        {**base_data, "environment": {"": "v"}},
        {**base_data, "environment": {"FOO": 123}},
        {**base_data, "environment": {"FOO": True}},
        {**base_data, "resources": None},
        {**base_data, "resources": "bad-section"},
        {**base_data, "resources": {"ram_mb": 512.9}},
        {**base_data, "resources": {"ram_mb": True}},
        {**base_data, "resources": {"gpu_vram_mb": 1024.5}},
        {**base_data, "resources": {"storage_mb": 2048.5}},
        {**base_data, "resources": {"cpu_cores": True}},
        {**base_data, "resources": {"gpu_required": "false"}},
        {**base_data, "resources": {"max_power_w": "bad"}},
        {**base_data, "power": None},
        {**base_data, "power": {"tier": "INVALID_TIER"}},
        {**base_data, "power": {"tier": 123}},
        {**base_data, "power": {"min_battery_soc": True}},
        {**base_data, "power": {"max_ambient_temp_c": True}},
        {**base_data, "power": {"allow_solar_burst": "no"}},
        {**base_data, "power": {"min_solar_watts": "bad"}},
        {**base_data, "network": "bad"},
        {**base_data, "network": {"ports": [65535.9]}},
        {**base_data, "network": {"ports": [True]}},
        {**base_data, "network": {"ports": "8080"}},
        {**base_data, "network": {"expose_wireguard": "yes"}},
        {**base_data, "network": {"space_dtn_enabled": 1}},
        {**base_data, "network": {"lora_heartbeat": "no"}},
        {**base_data, "storage": []},
        {**base_data, "storage": {"persistent_volume": None}},
        {**base_data, "storage": {"mount_point": 123}},
        {**base_data, "storage": {"backup_enabled": "true"}},
        {**base_data, "health_check": 123},
        {**base_data, "health_check": {"type": None}},
        {**base_data, "health_check": {"endpoint": None}},
        {**base_data, "health_check": {"port": 8080.9}},
        {**base_data, "health_check": {"port": True}},
        {**base_data, "health_check": {"port": "bad"}},
        {**base_data, "health_check": {"port": 70000}},
        {**base_data, "health_check": {"interval_sec": 15.5}},
        {**base_data, "health_check": {"timeout_sec": True}},
        {**base_data, "health_check": {"max_retries": "3"}},
        {**base_data, "health_check": {"max_retries": -1}},
    ]
    for case in invalid_cases:
        errs = AppManifest.from_dict(case).validate()
        assert errs, f"Expected validation error for {case!r}"

    # Direct Python construction with invalid runtime attribute types also fails validate().
    core_kwargs = {k: v for k, v in base_data.items() if k != "environment"}
    direct_invalid = [
        AppManifest(**core_kwargs, category="bad"),  # type: ignore[arg-type]
        AppManifest(**core_kwargs, runtime="bad"),  # type: ignore[arg-type]
        AppManifest(**core_kwargs, environment="bad"),  # type: ignore[arg-type]
        AppManifest(**core_kwargs, environment={"": "v"}),
        AppManifest(**core_kwargs, environment={"K": 1}),  # type: ignore[dict-item]
        AppManifest(**core_kwargs, resources=ResourceQuotas(ram_mb=512.5, gpu_required="no")),  # type: ignore[arg-type]
        AppManifest(
            **core_kwargs, power=PowerPolicy(tier="bad", max_ambient_temp_c=float("nan"), allow_solar_burst="yes")
        ),  # type: ignore[arg-type]
        AppManifest(**core_kwargs, network=NetworkPolicy(ports=[65535.9])),  # type: ignore[list-item]
        AppManifest(**core_kwargs, network=NetworkPolicy(ports="bad", expose_wireguard=1)),  # type: ignore[arg-type]
        AppManifest(**core_kwargs, storage=StoragePolicy(persistent_volume=None, mount_point=None, backup_enabled=1)),  # type: ignore[arg-type]
        AppManifest(
            **core_kwargs,
            health_check=HealthProbe(type="", endpoint=None, port=70000, interval_sec=0, timeout_sec=0, max_retries=-1),
        ),  # type: ignore[arg-type]
    ]
    for manifest in direct_invalid:
        assert manifest.validate()

    # Blank YAML entrypoint & invalid fields loaded through registry + CLI validator fail.
    bad_yaml = tmp_path / "bad.smdc-app.yaml"
    bad_yaml.write_text(
        "name: Blank Entrypoint\napp_id: blank-entry\nversion: 1.0.0\nentrypoint:\nnetwork:\n  ports:\n    - 65535.9\n",
        encoding="utf-8",
    )
    registry = EnterpriseRegistry()
    assert registry.load_manifest_file(bad_yaml) is None
    raw_bad = registry._parse_yaml(bad_yaml.read_text(encoding="utf-8"))
    assert AppManifest.from_dict(raw_bad).validate()
    assert cli.cmd_app_validate(Namespace(path=str(bad_yaml))) == 1

    # Non-dict YAML root must not pass via fallback parser.
    list_yaml = tmp_path / "list.smdc-app.yaml"
    list_yaml.write_text("- name: not-a-mapping\n- entrypoint: python3 app.py\n", encoding="utf-8")
    assert cli.cmd_app_validate(Namespace(path=str(list_yaml))) == 1

    # JSON manifests: valid JSON passes, non-dict JSON fails.
    good_json = tmp_path / "good.smdc-app.json"
    good_json.write_text(valid.to_json(), encoding="utf-8")
    assert registry.load_manifest_file(good_json) is not None
    assert cli.cmd_app_validate(Namespace(path=str(good_json))) == 0

    bad_json = tmp_path / "bad.smdc-app.json"
    bad_json.write_text("[1, 2]", encoding="utf-8")
    assert registry.load_manifest_file(bad_json) is None
    assert cli.cmd_app_validate(Namespace(path=str(bad_json))) == 1

    # Valid scaffolded project passes file load and CLI validation.
    app_dir = tmp_path / "good-app"
    scaffolded = EnterpriseRegistry.scaffold_manifest(name="Good App", app_id="good-app")
    EnterpriseRegistry.create_project_scaffold(app_dir, scaffolded, create_sample_code=False)
    assert cli.cmd_app_validate(Namespace(path=str(app_dir))) == 0


def test_round39_hardware_wiring_and_raid1_storage_consistency() -> None:
    """Wiring diagram matches S+RJ10/Cat6A BOM and RAID-1 docs report 4 TB usable capacity."""
    wiring = (REPO_ROOT / "hardware" / "WIRING_DIAGRAM.md").read_text(encoding="utf-8")
    components = (REPO_ROOT / "hardware" / "COMPONENTS.md").read_text(encoding="utf-8")

    assert "(10GbE DAC cables)" not in wiring
    assert "S+RJ10" in wiring
    assert "S+RJ10" in components
    assert "DGX Spark S+RJ10 10GbE & management links" in components

    arch = (REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    index_html = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    models_js = (REPO_ROOT / "docs" / "js" / "models.js").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    mcp_data = json.loads(read_manifest_resource())

    for text in (arch, index_html, models_js, readme):
        assert "8TB NVMe RAID-1" not in text
        assert "8 TB NVMe RAID-1" not in text
        assert "8TB total NVMe" not in text

    assert "2x4TB NVMe RAID-1 (4TB usable)" in arch
    assert "4 TB Usable (2×4 TB RAID-1)" in index_html
    assert "4TB usable (2×4TB RAID-1)" in models_js
    assert "4TB usable RAID-1 / 8TB raw" in readme
    assert "4TB usable" in mcp_data["specifications"]["storage"]
