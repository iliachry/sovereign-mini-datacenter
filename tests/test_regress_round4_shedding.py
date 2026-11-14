"""Regression tests for the load-shedding contract (tri-council round 4, finding 1).

One SoC policy: ``SovereignConfig.shedding_l1..l4_soc`` (50 / 30 / 20 / 10 % by default,
``SHEDDING_L1_SOC`` .. ``SHEDDING_L4_SOC``). The standalone sentinel, both exporter copies,
``load_shedder.sh``, the Compose overlays and the two documents that state the thresholds
must agree with it, and the enterprise manager must apply the documented tier mapping.
"""

from __future__ import annotations

import importlib.util
import re
from argparse import Namespace
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest

from sovereign_dc import cli, telemetry
from sovereign_dc.agents import sentinel_copilot
from sovereign_dc.config import SovereignConfig
from sovereign_dc.enterprise import AppManifest, AppStatus, EnterpriseManager, EnterpriseRegistry, PowerPriority
from sovereign_dc.enterprise import manager as manager_module
from sovereign_dc.enterprise.schema import (
    POWER_POLICY_RESERVED_FIELDS,
    POWER_POLICY_RESERVED_NOTICE,
    SHED_PAUSE_LEVEL,
    PowerPolicy,
)
from sovereign_dc.events import Event, SovereignEventBus, get_event_bus

ROOT = Path(__file__).resolve().parent.parent
ENV_NAMES = ("SHEDDING_L1_SOC", "SHEDDING_L2_SOC", "SHEDDING_L3_SOC", "SHEDDING_L4_SOC")
DOCS_WITH_LEVEL_TABLE = ("ENTERPRISE_ONBOARDING.md", "ARCHITECTURE.md")
ALL_TIERS = tuple(PowerPriority)


def _config_defaults() -> tuple[float, float, float, float]:
    cfg = SovereignConfig()
    return cfg.shedding_l1_soc, cfg.shedding_l2_soc, cfg.shedding_l3_soc, cfg.shedding_l4_soc


@pytest.fixture(autouse=True)
def _no_shedding_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


# --- one set of thresholds ----------------------------------------------------------------------


def test_standalone_sentinel_defaults_equal_the_config_defaults() -> None:
    assert tuple(name for name, _ in sentinel_copilot.SHEDDING_SOC_DEFAULTS) == ENV_NAMES
    assert tuple(default for _, default in sentinel_copilot.SHEDDING_SOC_DEFAULTS) == _config_defaults()
    assert sentinel_copilot.shedding_thresholds() == _config_defaults() == (50.0, 30.0, 20.0, 10.0)
    assert sentinel_copilot.SHEDDING_THRESHOLDS == _config_defaults()


def test_sentinel_and_config_read_the_same_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    overrides = {"SHEDDING_L1_SOC": "61.5", "SHEDDING_L2_SOC": "44", "SHEDDING_L3_SOC": "23", "SHEDDING_L4_SOC": "7"}
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    cfg = SovereignConfig.from_env()
    thresholds = sentinel_copilot.shedding_thresholds()  # what a sentinel started in this environment uses
    assert thresholds == (61.5, 44.0, 23.0, 7.0)
    assert (cfg.shedding_l1_soc, cfg.shedding_l2_soc, cfg.shedding_l3_soc, cfg.shedding_l4_soc) == thresholds
    for step in range(0, 201):
        soc = step / 2.0
        assert sentinel_copilot.shedding_level(soc, thresholds) == cfg.get_shedding_level(soc), soc
    # The whole decision follows the overridden tiers once the sentinel has (re)started with them.
    monkeypatch.setattr(sentinel_copilot, "SHEDDING_THRESHOLDS", thresholds)
    state = sentinel_copilot.evaluate_state({sentinel_copilot.SOC_METRIC: 40.0}, 0.0)
    assert state is not None and (state.level, state.mode) == (2, "ECO_PRESERVATION")


def test_sentinel_level_equals_config_level_for_a_sweep_of_soc() -> None:
    cfg = SovereignConfig()
    seen: set[int] = set()
    for step in range(-20, 1021):
        soc = step / 10.0
        level = sentinel_copilot.shedding_level(soc)
        assert level == cfg.get_shedding_level(soc), soc
        seen.add(level)
    assert seen == {0, 1, 2, 3, 4}
    # The boundaries themselves belong to the lower level (strict "below").
    assert [sentinel_copilot.shedding_level(soc) for soc in (50.0, 30.0, 20.0, 10.0)] == [0, 1, 2, 3]


def test_unusable_threshold_keeps_the_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SHEDDING_L2_SOC", "thirty")
    monkeypatch.setenv("SHEDDING_L3_SOC", "nan")
    assert sentinel_copilot.shedding_thresholds() == (50.0, 30.0, 20.0, 10.0)
    assert "SHEDDING_L2_SOC" in caplog.text and "SHEDDING_L3_SOC" in caplog.text
    assert SovereignConfig.from_env().shedding_l2_soc == 30.0


@pytest.mark.parametrize(
    ("soc", "solar", "level", "mode"),
    [
        (90.0, 1500.0, 0, "SOLAR_SURPLUS_COMPUTE"),
        (90.0, 900.0, 0, "NORMAL"),
        (75.0, 1500.0, 0, "NORMAL"),  # surplus needs SoC above 75 %
        (50.0, 0.0, 0, "NORMAL"),
        (49.9, 3000.0, 1, "NORMAL"),  # level 1 never unlocks surplus compute
        (39.0, 0.0, 1, "NORMAL"),  # the old "below 40 % with a low forecast" early ECO rule is gone
        (29.9, 0.0, 2, "ECO_PRESERVATION"),
        (19.9, 0.0, 3, "ECO_PRESERVATION"),
        (9.9, 0.0, 4, "ECO_PRESERVATION"),
    ],
)
def test_mode_is_derived_from_the_level(soc: float, solar: float, level: int, mode: str) -> None:
    metrics = {sentinel_copilot.SOC_METRIC: soc, sentinel_copilot.SOLAR_METRIC: solar}
    state = sentinel_copilot.evaluate_state(metrics, forecast=0.0)
    assert state is not None
    assert (state.level, state.mode, state.soc) == (level, mode, soc)
    assert sentinel_copilot.evaluate_mode(metrics, forecast=0.0) == (mode, state.reason)
    # The forecast is informational only.
    other = sentinel_copilot.evaluate_state(metrics, forecast=3000.0)
    assert other is not None and (other.level, other.mode) == (level, mode)


def test_unknown_soc_yields_no_state() -> None:
    assert sentinel_copilot.evaluate_state({}, 0.0) is None
    assert sentinel_copilot.evaluate_state({sentinel_copilot.SOC_METRIC: float("nan")}, 0.0) is None


def _run_sentinel(sequence: list[dict[str, float]]) -> list[dict[str, Any]]:
    """Runs run_copilot() over ``sequence`` and returns the payloads it published."""
    payloads: list[dict[str, Any]] = []

    def record(event: Event) -> None:
        payloads.append(dict(event.payload))

    feed = iter(sequence)

    def telemetry_feed() -> dict[str, float]:
        try:
            return next(feed)
        except StopIteration:
            raise KeyboardInterrupt from None

    bus = get_event_bus()
    bus.subscribe("load_shedding.changed", record)
    try:
        with (
            patch.object(sentinel_copilot, "get_telemetry", side_effect=telemetry_feed),
            patch("time.sleep", return_value=None),
            pytest.raises(KeyboardInterrupt),
        ):
            sentinel_copilot.run_copilot()
    finally:
        bus.unsubscribe("load_shedding.changed", record)
    return payloads


def _sample(soc: float) -> dict[str, float]:
    return {sentinel_copilot.SOC_METRIC: soc, sentinel_copilot.SOLAR_METRIC: 0.0}


def test_sentinel_publishes_level_and_soc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mode_file = tmp_path / "sovereign_mode"
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(mode_file))
    payloads = _run_sentinel(
        [
            _sample(60.0),  # first evaluation: always published
            _sample(60.4),  # same mode and level, SoC moved by less than 1 point: nothing
            _sample(45.0),  # level 1, mode still NORMAL: published without a mode-file write
            {},  # telemetry lost: hold, publish nothing
            _sample(44.5),  # less than 1 point: nothing
            _sample(43.0),  # SoC moved by 2 points at the same level: published for per-app floors
            _sample(25.0),  # level 2: ECO_PRESERVATION
            _sample(8.0),  # level 4, mode unchanged
        ]
    )
    assert payloads == [
        {"mode": "NORMAL", "level": 0, "battery_soc": 60.0},
        {"mode": "NORMAL", "level": 1, "battery_soc": 45.0},
        {"mode": "NORMAL", "level": 1, "battery_soc": 43.0},
        {"mode": "ECO_PRESERVATION", "level": 2, "battery_soc": 25.0},
        {"mode": "ECO_PRESERVATION", "level": 4, "battery_soc": 8.0},
    ]
    assert mode_file.read_text(encoding="utf-8") == "ECO_PRESERVATION"


def test_set_mode_without_a_level_publishes_the_level_implied_by_the_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(tmp_path / "sovereign_mode"))
    payloads: list[dict[str, Any]] = []

    def record(event: Event) -> None:
        payloads.append(dict(event.payload))

    bus = get_event_bus()
    bus.subscribe("load_shedding.changed", record)
    try:
        assert sentinel_copilot.set_mode("ECO_PRESERVATION") is True
        assert sentinel_copilot.set_mode("NORMAL") is True
        assert sentinel_copilot.set_mode("ECO_PRESERVATION", 4, 6.5) is True
    finally:
        bus.unsubscribe("load_shedding.changed", record)
    assert payloads == [
        {"mode": "ECO_PRESERVATION", "level": 2},
        {"mode": "NORMAL", "level": 0},
        {"mode": "ECO_PRESERVATION", "level": 4, "battery_soc": 6.5},
    ]


# --- exporter, host script and Compose defaults -------------------------------------------------


def _load_standalone_exporter() -> ModuleType:
    path = ROOT / "software" / "telemetry" / "power_exporter.py"
    spec = importlib.util.spec_from_file_location("standalone_power_exporter_round4", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exporter_flag_uses_the_level_three_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    standalone = _load_standalone_exporter()
    l3 = SovereignConfig().shedding_l3_soc
    for module in (telemetry, standalone):
        assert module.SHEDDING_L3_SOC_ENV == "SHEDDING_L3_SOC"
        assert module.DEFAULT_LOAD_SHEDDING_SOC_PERCENT == l3
        assert module.load_shedding_soc_percent() == l3
        monkeypatch.setenv("SHEDDING_L3_SOC", "27.5")
        assert module.load_shedding_soc_percent() == 27.5
        monkeypatch.setenv("SHEDDING_L3_SOC", "not-a-number")
        assert module.load_shedding_soc_percent() == l3
        monkeypatch.delenv("SHEDDING_L3_SOC")
        help_text = next(
            text for name, text, _, _ in module.CANONICAL_SERIES if name == "sovereign_load_shedding_active"
        )
        assert "SHEDDING_L3_SOC" in help_text and f"default {l3:g} %" in help_text


@pytest.mark.parametrize(("soc", "flag"), [(19.9, 1.0), (20.0, 0.0), (55.0, 0.0)])
def test_exporter_flag_follows_the_shedding_level(soc: float, flag: float, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry, "LOAD_SHEDDING_SOC_PERCENT", telemetry.load_shedding_soc_percent())
    values, _ = telemetry.bridge_values({"battery_soc": soc}, max_frame_age=10.0)
    assert values["load_shedding"] == flag
    assert (SovereignConfig().get_shedding_level(soc) >= 3) is bool(flag)


def test_load_shedder_script_defaults_equal_the_config_defaults() -> None:
    script = (ROOT / "software" / "telemetry" / "load_shedder.sh").read_text(encoding="utf-8")
    l1, _, l3, _ = _config_defaults()
    low = re.search(r'^SOC_THRESHOLD_LOW="\$\{SHEDDING_L3_SOC:-([0-9.]+)\}"$', script, re.MULTILINE)
    normal = re.search(r'^SOC_THRESHOLD_NORMAL="\$\{SHEDDING_L1_SOC:-([0-9.]+)\}"$', script, re.MULTILINE)
    assert low is not None and float(low.group(1)) == l3
    assert normal is not None and float(normal.group(1)) == l1


def test_compose_overlays_pass_the_thresholds_with_the_config_defaults() -> None:
    defaults = dict(zip(ENV_NAMES, _config_defaults(), strict=True))
    found: dict[str, set[str]] = {}
    for path in sorted((ROOT / "software").rglob("docker-compose*.yml")):
        for name, default in re.findall(r"\$\{(SHEDDING_L[1-4]_SOC):-([0-9.]+)\}", path.read_text(encoding="utf-8")):
            assert float(default) == defaults[name], f"{path.name}: {name} default {default}"
            found.setdefault(path.name, set()).add(name)
    assert found["docker-compose.agents.yml"] == set(ENV_NAMES)  # the sentinel needs all four
    assert found["docker-compose.telemetry.yml"] == {"SHEDDING_L3_SOC"}  # the exporter flag


# --- documents cannot drift ---------------------------------------------------------------------


def _level_table(doc: str) -> list[list[str]]:
    text = (ROOT / doc).read_text(encoding="utf-8")
    block = text[text.index("<!-- shedding-levels:begin") : text.index("<!-- shedding-levels:end -->")]
    rows = [line for line in block.splitlines() if re.match(r"^\|\s*\*\*\d\*\*\s*\|", line)]
    return [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]


@pytest.mark.parametrize("doc", DOCS_WITH_LEVEL_TABLE)
def test_documented_level_table_matches_the_code(doc: str) -> None:
    rows = _level_table(doc)
    assert [row[0] for row in rows] == ["**0**", "**1**", "**2**", "**3**", "**4**"]
    l1, l2, l3, l4 = _config_defaults()

    assert rows[0][1] == f"at or above {l1:g} %"
    assert [row[1] for row in rows[1:]] == [f"below {value:g} %" for value in (l1, l2, l3, l4)]

    for level, row in enumerate(rows):
        probe = {0: 100.0, 1: l1 - 0.1, 2: l2 - 0.1, 3: l3 - 0.1, 4: l4 - 0.1}[level]
        state = sentinel_copilot.evaluate_state({sentinel_copilot.SOC_METRIC: probe}, 0.0)
        assert state is not None and state.level == level
        assert row[2].startswith(f"`{state.mode}`"), row
        documented_tiers = set(re.findall(r"`(L[0-4]_[A-Z]+)`", row[3]))
        paused = {tier.value for tier, pause_level in SHED_PAUSE_LEVEL.items() if level >= pause_level}
        assert documented_tiers == paused, row
        assert ("sovereign_load_shedding_active" in row[4] or "Same as level 3" in row[4]) is (level >= 3)

    surplus = rows[0][2]
    assert f"above {sentinel_copilot.SURPLUS_MIN_SOLAR_WATTS:g} W" in surplus
    assert f"SoC above {sentinel_copilot.SURPLUS_MIN_SOC_PERCENT:g} %" in surplus


@pytest.mark.parametrize("doc", DOCS_WITH_LEVEL_TABLE)
def test_documented_diagram_uses_the_code_thresholds(doc: str) -> None:
    text = (ROOT / doc).read_text(encoding="utf-8")
    start = text.index("<!-- shedding-levels:begin")
    section = text[max(0, start - 3000) : start + 9000]
    diagrams = [block for block in re.findall(r"```mermaid\n(.*?)```", section, re.DOTALL) if "SoC under" in block]
    assert len(diagrams) == 1
    diagram = diagrams[0]
    expected = sorted(int(value) for value in _config_defaults())
    assert sorted(int(value) for value in re.findall(r'"SoC under (\d+)%"', diagram)) == expected
    assert sorted(int(value) for value in re.findall(r'"SoC at or over (\d+)%"', diagram)) == expected
    # Mermaid rules of this repository: quoted labels, no raw angle brackets or bare pipes.
    assert "<" not in diagram and ">" not in diagram.replace("-->", "") and "|" not in diagram


def test_onboarding_guide_states_which_manifest_fields_are_enforced() -> None:
    guide = (ROOT / "ENTERPRISE_ONBOARDING.md").read_text(encoding="utf-8")
    assert "| `power.min_battery_soc` | **Enforced**" in guide
    for field_name in POWER_POLICY_RESERVED_FIELDS:
        assert f"| `power.{field_name}` | Reserved, not enforced |" in guide
        assert f"power.{field_name}" in POWER_POLICY_RESERVED_NOTICE
    assert f"plus {manager_module.SOC_FLOOR_HYSTERESIS_PERCENT:g} points" in guide
    for stale in ("throttled", "Immediately terminated", "Scheduled strictly", "SoC} < 65", "SoC} < 80"):
        assert stale not in guide
    for pause_level, tier in sorted((level, tier) for tier, level in SHED_PAUSE_LEVEL.items()):
        assert re.search(rf"\| `{tier.value}` \|[^|]*\| level {pause_level} \|", guide), tier


# --- enterprise manager: tier mapping -----------------------------------------------------------


def _manifest(app_id: str, tier: PowerPriority, min_battery_soc: float = 0.0) -> AppManifest:
    return AppManifest(
        name=app_id,
        app_id=app_id,
        entrypoint="python3 app.py",
        power=PowerPolicy(tier=tier, min_battery_soc=min_battery_soc),
    )


@pytest.fixture
def manager(tmp_path: Path) -> EnterpriseManager:
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    return EnterpriseManager(registry=registry, event_bus=SovereignEventBus())


def _shed(mgr: EnterpriseManager, **payload: Any) -> None:
    mgr.event_bus.publish(Event(event_type="load_shedding.changed", source="test", payload=payload))


def _status(mgr: EnterpriseManager, app_id: str) -> AppStatus:
    state = mgr.get_runtime_state(app_id)
    assert state is not None
    return state.status


def _start_one_app_per_tier(mgr: EnterpriseManager) -> dict[PowerPriority, str]:
    apps = {tier: f"app-{tier.value.lower().replace('_', '-')}" for tier in ALL_TIERS}
    for tier, app_id in apps.items():
        mgr.registry.register_app(_manifest(app_id, tier))
        assert mgr.start_app(app_id)[0]
    return apps


EXPECTED_PAUSED: dict[int, set[PowerPriority]] = {
    0: set(),
    1: {PowerPriority.L4_IDLE},
    2: {PowerPriority.L3_DEFERRABLE, PowerPriority.L4_IDLE},
    3: {PowerPriority.L2_BACKGROUND, PowerPriority.L3_DEFERRABLE, PowerPriority.L4_IDLE},
    4: {PowerPriority.L1_STANDARD, PowerPriority.L2_BACKGROUND, PowerPriority.L3_DEFERRABLE, PowerPriority.L4_IDLE},
}


def test_pause_level_mapping_is_the_documented_one() -> None:
    assert manager_module.SHED_PAUSE_LEVEL is SHED_PAUSE_LEVEL
    assert PowerPriority.L0_CRITICAL not in SHED_PAUSE_LEVEL
    for level, tiers in EXPECTED_PAUSED.items():
        assert {tier for tier, pause_level in SHED_PAUSE_LEVEL.items() if level >= pause_level} == tiers


@pytest.mark.parametrize("level", [0, 1, 2, 3, 4])
def test_each_level_pauses_exactly_its_tiers(manager: EnterpriseManager, level: int) -> None:
    apps = _start_one_app_per_tier(manager)
    _shed(manager, level=level)
    paused = {tier for tier, app_id in apps.items() if _status(manager, app_id) == AppStatus.PAUSED}
    assert paused == EXPECTED_PAUSED[level]
    running = {tier for tier, app_id in apps.items() if _status(manager, app_id) == AppStatus.RUNNING}
    assert running == set(ALL_TIERS) - EXPECTED_PAUSED[level]


def test_levels_above_four_are_treated_as_four(manager: EnterpriseManager) -> None:
    apps = _start_one_app_per_tier(manager)
    _shed(manager, level=9)
    assert _status(manager, apps[PowerPriority.L0_CRITICAL]) == AppStatus.RUNNING
    assert _status(manager, apps[PowerPriority.L1_STANDARD]) == AppStatus.PAUSED


def test_apps_resume_tier_by_tier_as_the_level_falls(manager: EnterpriseManager) -> None:
    apps = _start_one_app_per_tier(manager)
    for level in (1, 2, 3, 4, 3, 2, 1, 0, 3, 0):  # down the tiers, back up, then a jump
        _shed(manager, level=level)
        paused = {tier for tier, app_id in apps.items() if _status(manager, app_id) == AppStatus.PAUSED}
        assert paused == EXPECTED_PAUSED[level], level
    assert manager._shed_paused == {}


def test_operator_pause_and_stopped_apps_are_left_alone(manager: EnterpriseManager) -> None:
    apps = _start_one_app_per_tier(manager)
    operator_app = apps[PowerPriority.L3_DEFERRABLE]
    stopped_app = apps[PowerPriority.L4_IDLE]
    manager.pause_app(operator_app, reason="maintenance")
    manager.stop_app(stopped_app)

    _shed(manager, level=4)
    assert _status(manager, stopped_app) == AppStatus.STOPPED
    _shed(manager, level=0, battery_soc=95.0)
    assert _status(manager, operator_app) == AppStatus.PAUSED  # still the operator's pause
    assert _status(manager, stopped_app) == AppStatus.STOPPED
    assert _status(manager, apps[PowerPriority.L2_BACKGROUND]) == AppStatus.RUNNING

    # An operator pause that replaces a shedding pause is not lifted by recovery either.
    _shed(manager, level=3)
    background = apps[PowerPriority.L2_BACKGROUND]
    assert manager.resume_app(background)[0]
    assert manager.pause_app(background, reason="operator")[0]
    _shed(manager, level=0)
    assert _status(manager, background) == AppStatus.PAUSED


def test_pause_event_names_the_level_and_the_cause(manager: EnterpriseManager) -> None:
    manager.registry.register_app(_manifest("idle-job", PowerPriority.L4_IDLE))
    manager.start_app("idle-job")
    events: list[Event] = []
    manager.event_bus.subscribe("enterprise.idle-job.*", events.append)
    _shed(manager, level=1, battery_soc=47.25)
    _shed(manager, level=0, battery_soc=52.0)
    assert [event.event_type for event in events] == ["enterprise.idle-job.paused", "enterprise.idle-job.resumed"]
    assert events[0].payload["paused_by"] == "load_shedding"
    assert events[0].payload["reason"] == "Load shedding level 1, battery SoC 47.2% (tier)"
    assert events[1].payload["reason"] == "Load shedding eased to level 0, battery SoC 52.0%"


# --- enterprise manager: per-app min_battery_soc ------------------------------------------------


def test_min_battery_soc_pauses_below_the_floor_and_resumes_with_hysteresis(manager: EnterpriseManager) -> None:
    manager.registry.register_app(_manifest("api", PowerPriority.L1_STANDARD, min_battery_soc=40.0))
    manager.start_app("api")

    _shed(manager, level=1, battery_soc=40.0)  # at the floor: not below it
    assert _status(manager, "api") == AppStatus.RUNNING
    _shed(manager, level=1, battery_soc=39.9)
    assert _status(manager, "api") == AppStatus.PAUSED
    assert manager._shed_paused["api"] == {manager_module.CAUSE_SOC_FLOOR}

    _shed(manager, level=1, battery_soc=41.9)  # inside the 2-point hysteresis band
    assert _status(manager, "api") == AppStatus.PAUSED
    _shed(manager, level=0)  # no SoC in the event: a floor pause needs a reading to end
    assert _status(manager, "api") == AppStatus.PAUSED
    _shed(manager, level=1, battery_soc=float("nan"))
    assert _status(manager, "api") == AppStatus.PAUSED
    _shed(manager, level=1, battery_soc=42.0)
    assert _status(manager, "api") == AppStatus.RUNNING
    _shed(manager, level=1, battery_soc=40.5)  # running again: only "below the floor" pauses
    assert _status(manager, "api") == AppStatus.RUNNING


def test_min_battery_soc_and_tier_must_both_clear(manager: EnterpriseManager) -> None:
    manager.registry.register_app(_manifest("batch", PowerPriority.L3_DEFERRABLE, min_battery_soc=45.0))
    manager.start_app("batch")

    _shed(manager, level=2, battery_soc=28.0)
    assert manager._shed_paused["batch"] == {manager_module.CAUSE_TIER, manager_module.CAUSE_SOC_FLOOR}
    _shed(manager, level=1, battery_soc=33.0)  # tier cleared, floor not
    assert _status(manager, "batch") == AppStatus.PAUSED
    assert manager._shed_paused["batch"] == {manager_module.CAUSE_SOC_FLOOR}
    _shed(manager, level=2, battery_soc=48.0)  # floor cleared, tier back (a manual level-2 event)
    assert _status(manager, "batch") == AppStatus.PAUSED
    assert manager._shed_paused["batch"] == {manager_module.CAUSE_TIER}
    _shed(manager, level=1, battery_soc=48.0)
    assert _status(manager, "batch") == AppStatus.RUNNING


def test_min_battery_soc_is_ignored_for_critical_apps_and_without_soc(manager: EnterpriseManager) -> None:
    manager.registry.register_app(_manifest("vault", PowerPriority.L0_CRITICAL, min_battery_soc=90.0))
    manager.registry.register_app(_manifest("api", PowerPriority.L1_STANDARD, min_battery_soc=90.0))
    manager.start_app("vault")
    manager.start_app("api")

    _shed(manager, level=3)  # no SoC: the floor cannot be evaluated
    assert _status(manager, "api") == AppStatus.RUNNING
    _shed(manager, level=3, battery_soc=True)  # not a number
    assert _status(manager, "api") == AppStatus.RUNNING
    _shed(manager, level=4, battery_soc=5.0)
    assert _status(manager, "vault") == AppStatus.RUNNING
    assert _status(manager, "api") == AppStatus.PAUSED


def test_sentinel_event_drives_the_manager_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(tmp_path / "sovereign_mode"))
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    mgr = EnterpriseManager(registry=registry)  # global bus, as in a real process
    apps = _start_one_app_per_tier(mgr)
    try:
        for soc, level in ((45.0, 1), (25.0, 2), (15.0, 3), (5.0, 4), (70.0, 0)):
            state = sentinel_copilot.evaluate_state(_sample(soc), 0.0)
            assert state is not None and state.level == level
            sentinel_copilot.set_mode(state.mode, state.level, state.soc)
            paused = {tier for tier, app_id in apps.items() if _status(mgr, app_id) == AppStatus.PAUSED}
            assert paused == EXPECTED_PAUSED[level], soc
    finally:
        get_event_bus().unsubscribe("load_shedding.*", mgr._on_load_shedding_event)
        get_event_bus().unsubscribe("enterprise.*", mgr._on_enterprise_telemetry)


# --- manifest: what is enforced and what is reserved --------------------------------------------


def test_enforcement_summary_names_the_rule_and_the_reserved_fields() -> None:
    policy = PowerPolicy(tier=PowerPriority.L2_BACKGROUND, min_battery_soc=40.0, min_solar_watts=250.0)
    lines = policy.enforcement_summary()
    assert lines[0] == (
        "Paused (tracked state) from load-shedding level 3, "
        "or while a reported battery SoC is below min_battery_soc (40 %)."
    )
    assert lines[1].startswith("Reserved, not enforced: ")
    for field_name in POWER_POLICY_RESERVED_FIELDS:
        assert field_name in lines[1]
    critical = PowerPolicy(tier=PowerPriority.L0_CRITICAL).enforcement_summary()
    assert "never paused" in critical[0] and "min_battery_soc is ignored" in critical[0]


def test_app_validate_prints_enforced_and_reserved_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = EnterpriseRegistry.scaffold_manifest(
        name="Vision", app_id="vision-app", power_tier=PowerPriority.L3_DEFERRABLE
    )
    app_dir = EnterpriseRegistry.create_project_scaffold(tmp_path / "vision-app", manifest)
    assert cli.cmd_app_validate(Namespace(path=str(app_dir))) == 0
    out = capsys.readouterr().out
    assert "Paused (tracked state) from load-shedding level 2" in out
    assert "Reserved, not enforced: max_ambient_temp_c" in out
    assert "min_solar_watts" in out and "allow_solar_burst" in out
