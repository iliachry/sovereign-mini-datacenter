"""Tests for the centralized configuration module."""

import os
from unittest.mock import patch

from sovereign_dc.config import (
    SovereignConfig,
    _coerce,
    get_config,
    reset_config,
    set_config,
)


class TestSovereignConfigDefaults:
    """Test default configuration values."""

    def test_default_node_id(self):
        cfg = SovereignConfig()
        assert cfg.node_id == "smdc-dgx-01"

    def test_default_role(self):
        cfg = SovereignConfig()
        assert cfg.node_role == "Primary Compute Core"

    def test_default_hal_mode(self):
        cfg = SovereignConfig()
        assert cfg.hal_mode == "simulation"
        assert cfg.is_simulation() is True

    def test_default_ollama_url(self):
        cfg = SovereignConfig()
        assert cfg.ollama_url == "http://localhost:11434"

    def test_default_qdrant_url(self):
        cfg = SovereignConfig()
        assert cfg.qdrant_url == "http://localhost:6333"

    def test_default_exporter_port(self):
        cfg = SovereignConfig()
        assert cfg.exporter_port == 9101

    def test_default_shedding_thresholds(self):
        cfg = SovereignConfig()
        assert cfg.shedding_l1_soc == 50.0
        assert cfg.shedding_l2_soc == 30.0
        assert cfg.shedding_l3_soc == 20.0
        assert cfg.shedding_l4_soc == 10.0

    def test_dtn_db_path_auto_resolved(self):
        cfg = SovereignConfig()
        assert cfg.dtn_db_path != ""
        assert "dtn_spool.db" in cfg.dtn_db_path

    def test_log_dir_auto_resolved(self):
        cfg = SovereignConfig()
        assert cfg.log_dir != ""
        assert "sovereign_logs" in cfg.log_dir


class TestSovereignConfigEnv:
    """Test configuration loading from environment variables."""

    def test_from_env_node_id(self):
        with patch.dict(os.environ, {"NODE_ID": "smdc-edge-42"}):
            cfg = SovereignConfig.from_env()
            assert cfg.node_id == "smdc-edge-42"

    def test_from_env_legacy_ollama(self):
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://gpu-node:11434"}, clear=False):
            cfg = SovereignConfig.from_env()
            assert cfg.ollama_url == "http://gpu-node:11434"

    def test_from_env_legacy_qdrant(self):
        with patch.dict(os.environ, {"QDRANT_BASE_URL": "http://vector-db:6333"}, clear=False):
            cfg = SovereignConfig.from_env()
            assert cfg.qdrant_url == "http://vector-db:6333"

    def test_from_env_int_coercion(self):
        with patch.dict(os.environ, {"EXPORTER_PORT": "9999"}):
            cfg = SovereignConfig.from_env()
            assert cfg.exporter_port == 9999

    def test_from_env_float_coercion(self):
        with patch.dict(os.environ, {"GROUND_STATION_LAT": "40.6892"}):
            cfg = SovereignConfig.from_env()
            assert cfg.ground_station_lat == 40.6892

    def test_from_env_records_source(self):
        cfg = SovereignConfig.from_env()
        assert "env" in cfg._loaded_from

    def test_canonical_takes_precedence_over_legacy(self):
        with patch.dict(os.environ, {"OLLAMA_URL": "http://canonical:11434", "OLLAMA_BASE_URL": "http://legacy:11434"}):
            cfg = SovereignConfig.from_env()
            assert cfg.ollama_url == "http://canonical:11434"


class TestSovereignConfigYaml:
    """Test configuration loading from YAML files."""

    def test_from_yaml_missing_file(self):
        cfg = SovereignConfig.from_yaml_and_env("nonexistent.yaml")
        assert cfg.node_id == "smdc-dgx-01"
        assert "env" in cfg._loaded_from

    def test_from_yaml_valid_file(self, tmp_path):
        yaml_file = tmp_path / "test_config.yaml"
        yaml_file.write_text("node_id: smdc-yaml-node\nexporter_port: 8888\n")
        cfg = SovereignConfig.from_yaml_and_env(str(yaml_file))
        assert cfg.node_id == "smdc-yaml-node"
        assert cfg.exporter_port == 8888
        assert any("yaml" in s for s in cfg._loaded_from)

    def test_env_overrides_yaml(self, tmp_path):
        yaml_file = tmp_path / "test_config.yaml"
        yaml_file.write_text("node_id: from-yaml\n")
        with patch.dict(os.environ, {"NODE_ID": "from-env"}):
            cfg = SovereignConfig.from_yaml_and_env(str(yaml_file))
            assert cfg.node_id == "from-env"

    def test_yaml_invalid_content(self, tmp_path):
        yaml_file = tmp_path / "bad_config.yaml"
        yaml_file.write_text("not_a_dict_string")
        cfg = SovereignConfig.from_yaml_and_env(str(yaml_file))
        assert cfg.node_id == "smdc-dgx-01"  # Falls back to defaults


class TestSheddingLevel:
    """Test load shedding level calculation."""

    def test_l0_nominal(self):
        cfg = SovereignConfig()
        assert cfg.get_shedding_level(85.0) == 0

    def test_l0_boundary(self):
        cfg = SovereignConfig()
        assert cfg.get_shedding_level(50.0) == 0

    def test_l1_mild(self):
        cfg = SovereignConfig()
        assert cfg.get_shedding_level(45.0) == 1

    def test_l2_heavy(self):
        cfg = SovereignConfig()
        assert cfg.get_shedding_level(25.0) == 2

    def test_l3_critical(self):
        cfg = SovereignConfig()
        assert cfg.get_shedding_level(15.0) == 3

    def test_l4_blackout(self):
        cfg = SovereignConfig()
        assert cfg.get_shedding_level(5.0) == 4

    def test_custom_thresholds(self):
        cfg = SovereignConfig(shedding_l1_soc=60.0, shedding_l4_soc=5.0)
        assert cfg.get_shedding_level(55.0) == 1
        assert cfg.get_shedding_level(3.0) == 4


class TestConfigSingleton:
    """Test global configuration singleton management."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_get_config_creates_default(self):
        cfg = get_config()
        assert isinstance(cfg, SovereignConfig)

    def test_get_config_returns_same_instance(self):
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_set_config_overrides(self):
        custom = SovereignConfig(node_id="custom-node")
        set_config(custom)
        assert get_config().node_id == "custom-node"

    def test_reset_config_clears(self):
        get_config()
        reset_config()
        # After reset, next get_config should create a new instance
        cfg = get_config()
        assert isinstance(cfg, SovereignConfig)


class TestCoercion:
    """Test type coercion helper."""

    def test_coerce_int(self):
        assert _coerce("42", "int") == 42

    def test_coerce_float(self):
        assert _coerce("3.14", "float") == 3.14

    def test_coerce_bool_true(self):
        assert _coerce("true", "bool") is True
        assert _coerce("1", "bool") is True
        assert _coerce("yes", "bool") is True

    def test_coerce_bool_false(self):
        assert _coerce("false", "bool") is False
        assert _coerce("no", "bool") is False

    def test_coerce_passthrough(self):
        assert _coerce("hello", "str") == "hello"
        assert _coerce(42, "int") == 42


class TestIsSimulation:
    """Test simulation mode detection."""

    def test_simulation_mode(self):
        cfg = SovereignConfig(hal_mode="simulation")
        assert cfg.is_simulation() is True

    def test_hardware_mode(self):
        cfg = SovereignConfig(hal_mode="hardware")
        assert cfg.is_simulation() is False
