"""Tests for the structured logging module."""

import json
import logging

from sovereign_dc.log import (
    ConsoleFormatter,
    JSONFormatter,
    get_logger,
    setup_from_env,
    setup_logging,
)


class TestJSONFormatter:
    """Test JSON log formatter."""

    def test_json_output_structure(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="TestLogger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "TestLogger"
        assert data["message"] == "Test message"
        assert "timestamp" in data

    def test_json_with_exception(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="TestLogger",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="Error occurred",
                args=None,
                exc_info=sys.exc_info(),
            )
            output = formatter.format(record)
            data = json.loads(output)
            assert "exception" in data
            assert "ValueError" in data["exception"]


class TestConsoleFormatter:
    """Test console log formatter."""

    def test_console_output_contains_message(self):
        formatter = ConsoleFormatter()
        record = logging.LogRecord(
            name="TestLogger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        assert "Test message" in output
        assert "TestLogger" in output

    def test_console_color_levels(self):
        formatter = ConsoleFormatter()
        for level_name in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            record = logging.LogRecord(
                name="Test",
                level=getattr(logging, level_name),
                pathname="test.py",
                lineno=1,
                msg="msg",
                args=None,
                exc_info=None,
            )
            output = formatter.format(record)
            assert level_name in output


class TestSetupLogging:
    """Test logging configuration."""

    def test_setup_default(self):
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_setup_debug_level(self):
        setup_logging(level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_setup_json_mode(self):
        setup_logging(json_mode=True)
        root = logging.getLogger()
        assert len(root.handlers) > 0
        assert isinstance(root.handlers[-1].formatter, JSONFormatter)

    def test_setup_module_levels(self):
        setup_logging(module_levels={"HAL.GPU": "DEBUG", "SovereignEventBus": "WARNING"})
        gpu_logger = logging.getLogger("HAL.GPU")
        bus_logger = logging.getLogger("SovereignEventBus")
        assert gpu_logger.level == logging.DEBUG
        assert bus_logger.level == logging.WARNING

    def test_setup_from_env(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"SOVEREIGN_LOG_LEVEL": "DEBUG", "SOVEREIGN_LOG_FORMAT": "json"}):
            setup_from_env()
            root = logging.getLogger()
            assert root.level == logging.DEBUG


class TestGetLogger:
    """Test logger factory."""

    def test_get_named_logger(self):
        lg = get_logger("BootstrapProvisioner")
        assert lg.name == "BootstrapProvisioner"
        assert isinstance(lg, logging.Logger)
