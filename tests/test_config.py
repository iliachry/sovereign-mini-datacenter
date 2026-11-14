"""Tests for the centralized configuration module."""

import os
from unittest.mock import patch

import pytest

from sovereign_dc import config as config_module
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


class TestPrivateStateDirectory:
    """Node-local state defaults to the private state directory, never to shared /tmp."""

    @pytest.fixture
    def state_dir(self, tmp_path, monkeypatch):
        for name in ("DTN_DB_PATH", "LOG_DIR", "SOVEREIGN_LOG_DIR"):
            monkeypatch.delenv(name, raising=False)
        path = tmp_path / "state"
        monkeypatch.setenv("SMDC_STATE_DIR", str(path))
        return path

    @pytest.fixture
    def legacy_dir(self, tmp_path, monkeypatch):
        path = tmp_path / "legacy_tmp"
        path.mkdir()
        path.chmod(0o1777)  # like /tmp
        monkeypatch.setattr(config_module, "LEGACY_TMP_DIR", str(path))
        return path

    def test_get_state_dir_is_created_private(self, state_dir):
        assert config_module.get_state_dir() == str(state_dir)
        assert (state_dir.stat().st_mode & 0o777) == 0o700
        state_dir.chmod(0o755)
        config_module.get_state_dir()
        assert (state_dir.stat().st_mode & 0o777) == 0o700

    def test_get_state_dir_defaults_to_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SMDC_STATE_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        assert config_module.get_state_dir() == str(tmp_path / "home" / ".smdc")

    def test_unusable_state_dir_falls_back_to_a_verified_private_dir(self, tmp_path, monkeypatch):
        blocker = tmp_path / "a-file"
        blocker.write_text("x")
        monkeypatch.setenv("SMDC_STATE_DIR", str(blocker / "nested"))
        tmp_root = tmp_path / "tmproot"
        tmp_root.mkdir()
        monkeypatch.setattr(config_module.tempfile, "gettempdir", lambda: str(tmp_root))
        monkeypatch.setattr(config_module.tempfile, "tempdir", str(tmp_root))
        monkeypatch.setattr(config_module, "_FALLBACK_STATE_DIR", None)
        # Someone else pre-created the predictable fallback name with loose permissions.
        planted = tmp_root / f"smdc-{os.getuid()}"
        planted.mkdir(mode=0o777)
        planted.chmod(0o777)

        fallback = config_module.get_state_dir()
        assert fallback != str(planted)
        assert os.path.dirname(fallback) == str(tmp_root)
        assert (os.stat(fallback).st_mode & 0o777) == 0o700
        assert config_module.get_state_dir() == fallback  # stable for the rest of the process

    def test_defaults_resolve_inside_state_dir(self, state_dir, legacy_dir):
        cfg = SovereignConfig()
        assert cfg.dtn_db_path == str(state_dir / "dtn_spool.db")
        assert cfg.log_dir == str(state_dir / "sovereign_logs")
        assert config_module.default_mode_file() == str(state_dir / "sovereign_mode")
        assert not cfg.dtn_db_path.startswith("/tmp/") and not cfg.log_dir.startswith("/tmp/")

    def test_explicit_paths_are_untouched(self, state_dir, monkeypatch):
        monkeypatch.setenv("DTN_DB_PATH", "/var/spool/dtn/bundles.db")
        monkeypatch.setenv("SOVEREIGN_LOG_DIR", "/var/log/smdc")
        cfg = SovereignConfig.from_env()
        assert cfg.dtn_db_path == "/var/spool/dtn/bundles.db"
        assert cfg.log_dir == "/var/log/smdc"
        assert SovereignConfig(dtn_db_path="/x/y.db").dtn_db_path == "/x/y.db"

    def test_trusted_legacy_spool_is_kept_until_a_new_one_exists(self, state_dir, legacy_dir):
        legacy = legacy_dir / "dtn_spool.db"
        legacy.write_bytes(b"")
        legacy.chmod(0o644)
        assert SovereignConfig().dtn_db_path == str(legacy)
        (legacy_dir / "dtn_spool.db-wal").write_bytes(b"")
        assert SovereignConfig().dtn_db_path == str(legacy)
        # Once the state directory has its own spool, that one is authoritative.
        (state_dir / "dtn_spool.db").write_bytes(b"")
        assert SovereignConfig().dtn_db_path == str(state_dir / "dtn_spool.db")

    @pytest.mark.parametrize(
        "flaw", ["group_writable", "world_writable", "symlink", "directory", "foreign_owner", "wal"]
    )
    def test_untrusted_legacy_spool_is_ignored(self, state_dir, legacy_dir, tmp_path, monkeypatch, flaw):
        legacy = legacy_dir / "dtn_spool.db"
        if flaw == "symlink":
            target = tmp_path / "victim.db"
            target.write_bytes(b"")
            legacy.symlink_to(target)
        elif flaw == "directory":
            legacy.mkdir()
        else:
            legacy.write_bytes(b"")
            legacy.chmod({"group_writable": 0o664, "world_writable": 0o666}.get(flaw, 0o600))
        if flaw == "wal":  # the database is ours but its write-ahead log could be replaced by anyone
            wal = legacy_dir / "dtn_spool.db-wal"
            wal.write_bytes(b"")
            wal.chmod(0o666)
        if flaw == "foreign_owner":
            monkeypatch.setattr(config_module.os, "getuid", lambda: 4_000_000)
            assert config_module.is_trusted_legacy_file(str(legacy)) is False
        assert SovereignConfig().dtn_db_path == str(state_dir / "dtn_spool.db")
