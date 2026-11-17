"""Regression tests for tri-council review round 36.

1. ``EnterpriseRegistry.scaffold_manifest()``, ``smdc app init`` (``cli.py``), and MCP
   ``scaffold_enterprise_app`` (``mcp/tools.py``) honour explicit ``power_tier``,
   ``gpu_required``, and ``ports`` overrides across all categories while applying
   category defaults when omitted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from sovereign_dc import cli
from sovereign_dc.enterprise.manager import EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import AppCategory, AppStatus, PowerPriority
from sovereign_dc.events import Event, SovereignEventBus
from sovereign_dc.mcp import tools as mcp_tools


def _run_cli_init(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["smdc", "app", "init", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        assert int(exc.code or 0) == 0


def test_scaffold_manifest_cli_and_mcp_preserve_explicit_power_tier_and_category_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Explicit power_tier overrides across CLI and MCP across every archetype.
    explicit_cases = (
        ("spatial_media", "L0_CRITICAL"),
        ("distributed", "L0_CRITICAL"),
        ("iot", "L4_IDLE"),
        ("ai_inference", "L1_STANDARD"),
    )
    loaded_by_case: dict[str, EnterpriseRegistry] = {}
    for category, requested_tier in explicit_cases:
        app_id = f"cli-{category.replace('_', '-')}"
        app_dir = tmp_path / app_id
        _run_cli_init(
            monkeypatch,
            f"Workload {category}",
            "--app-id",
            app_id,
            "--category",
            category,
            "--power-tier",
            requested_tier,
            "--target-dir",
            str(app_dir),
        )
        raw_manifest = json.loads((app_dir / "smdc-app.json").read_text(encoding="utf-8"))
        assert raw_manifest["power"]["tier"] == requested_tier, (category, requested_tier)

        mcp_res = mcp_tools.tool_scaffold_enterprise_app(
            {
                "name": f"MCP {category}",
                "app_id": f"mcp-{category.replace('_', '-')}",
                "category": category,
                "power_tier": requested_tier,
            }
        )
        assert mcp_res["valid"] is True
        assert mcp_res["manifest"]["power"]["tier"] == requested_tier, (category, requested_tier)

    # Shedding behavior respects the scaffolded explicit tiers.
    bus = SovereignEventBus()
    reg = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    spatial_loaded = reg.load_manifest_file(tmp_path / "cli-spatial-media" / "smdc-app.yaml")
    iot_idle_loaded = reg.load_manifest_file(tmp_path / "cli-iot" / "smdc-app.yaml")
    assert spatial_loaded is not None and iot_idle_loaded is not None
    assert reg.register_app(spatial_loaded)[0]
    assert reg.register_app(iot_idle_loaded)[0]
    loaded_by_case["reg"] = reg

    manager = EnterpriseManager(registry=reg, event_bus=bus)
    assert manager.start_app("cli-spatial-media")[0]
    assert manager.start_app("cli-iot")[0]

    # At level 1 / SoC 48%: L4_IDLE iot is paused, L0_CRITICAL spatial stays running.
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 1, "battery_soc": 48.0}))
    assert manager.get_runtime_state("cli-iot").status == AppStatus.PAUSED  # type: ignore[union-attr]
    assert manager.get_runtime_state("cli-spatial-media").status == AppStatus.RUNNING  # type: ignore[union-attr]

    # At level 4 / SoC 35% (>= spatial min_battery_soc 30%): L0_CRITICAL spatial stays running.
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 35.0}))
    assert manager.get_runtime_state("cli-spatial-media").status == AppStatus.RUNNING  # type: ignore[union-attr]

    # Omitting --power-tier / power_tier applies category defaults on both CLI and MCP.
    default_cases = (
        ("iot", PowerPriority.L0_CRITICAL.value, False, 0),
        ("ai_inference", PowerPriority.L2_BACKGROUND.value, True, 4096),
        ("spatial_media", PowerPriority.L1_STANDARD.value, False, 2048),
        ("distributed", PowerPriority.L1_STANDARD.value, False, 0),
        ("custom", PowerPriority.L1_STANDARD.value, False, 0),
    )
    for category, exp_tier, exp_gpu, exp_vram in default_cases:
        app_id = f"def-{category.replace('_', '-')}"
        app_dir = tmp_path / app_id
        _run_cli_init(
            monkeypatch,
            f"Default {category}",
            "--app-id",
            app_id,
            "--category",
            category,
            "--target-dir",
            str(app_dir),
        )
        raw_manifest = json.loads((app_dir / "smdc-app.json").read_text(encoding="utf-8"))
        assert raw_manifest["power"]["tier"] == exp_tier, category
        assert raw_manifest["resources"]["gpu_required"] is exp_gpu, category
        assert raw_manifest["resources"]["gpu_vram_mb"] == exp_vram, category

        mcp_def = mcp_tools.tool_scaffold_enterprise_app(
            {"name": f"MCP Default {category}", "app_id": f"mdef-{category.replace('_', '-')}", "category": category}
        )
        assert mcp_def["manifest"]["power"]["tier"] == exp_tier, category
        assert mcp_def["manifest"]["resources"]["gpu_required"] is exp_gpu, category
        assert mcp_def["manifest"]["resources"]["gpu_vram_mb"] == exp_vram, category

    # Explicit GPU and custom port overrides.
    iot_gpu_dir = tmp_path / "iot-gpu"
    _run_cli_init(
        monkeypatch,
        "IoT GPU",
        "--app-id",
        "iot-gpu",
        "--category",
        "iot",
        "--gpu",
        "--target-dir",
        str(iot_gpu_dir),
    )
    iot_gpu_raw = json.loads((iot_gpu_dir / "smdc-app.json").read_text(encoding="utf-8"))
    assert iot_gpu_raw["resources"]["gpu_required"] is True
    assert iot_gpu_raw["resources"]["gpu_vram_mb"] == 1024

    cpu_ai = mcp_tools.tool_scaffold_enterprise_app(
        {"name": "CPU AI", "category": "ai_inference", "gpu_required": False}
    )
    assert cpu_ai["manifest"]["resources"]["gpu_required"] is False
    assert cpu_ai["manifest"]["resources"]["gpu_vram_mb"] == 0

    ported = EnterpriseRegistry.scaffold_manifest("Ported", "ported-app", category=AppCategory.IOT, ports=[9095])
    assert ported.network.ports == [9095]
    assert ported.health_check.port == 9095
