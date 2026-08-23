"""Sovereign Mini Datacenter — Structured Logging Configuration.

Provides consistent logging setup for all modules with support for:
- Console output with human-readable formatting
- JSON structured output for production log aggregation
- Per-module log level configuration
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for production deployments."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "event_type"):
            log_entry["event_type"] = record.event_type
        if hasattr(record, "node_id"):
            log_entry["node_id"] = record.node_id
        return json.dumps(log_entry)


class ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter with colored level indicators."""

    LEVEL_COLORS = {
        "DEBUG": "\033[90m",  # Gray
        "INFO": "\033[1;32m",  # Green
        "WARNING": "\033[1;33m",  # Yellow
        "ERROR": "\033[1;31m",  # Red
        "CRITICAL": "\033[1;35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, "")
        reset = self.RESET
        timestamp = datetime.now().strftime("%H:%M:%S")
        return f"{color}{timestamp} [{record.name}] {record.levelname}: {record.getMessage()}{reset}"


def setup_logging(
    level: str | int = "INFO",
    json_mode: bool = False,
    module_levels: dict[str, str] | None = None,
) -> None:
    """Configure the root logger and module-specific log levels.

    Args:
        level: Default log level (e.g. ``"INFO"``, ``"DEBUG"``, or ``logging.INFO``).
        json_mode: If True, use JSON structured output instead of console formatting.
        module_levels: Optional dict mapping logger names to specific levels.
            Example: ``{"HAL.GPU": "DEBUG", "SovereignEventBus": "WARNING"}``
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # Create handler
    handler = logging.StreamHandler(sys.stderr)

    if json_mode:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(ConsoleFormatter())

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicate output
    for existing in root_logger.handlers[:]:
        root_logger.removeHandler(existing)
    root_logger.addHandler(handler)

    # Apply module-specific levels
    if module_levels:
        for logger_name, logger_level in module_levels.items():
            mod_logger = logging.getLogger(logger_name)
            if isinstance(logger_level, str):
                mod_logger.setLevel(getattr(logging, logger_level.upper(), logging.INFO))
            else:
                mod_logger.setLevel(logger_level)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger for a Sovereign Mini Datacenter module.

    This is a convenience wrapper that ensures consistent logger naming.

    Args:
        name: Module or component name (e.g. ``"BootstrapProvisioner"``).

    Returns:
        Configured Logger instance.
    """
    return logging.getLogger(name)


def setup_from_env() -> None:
    """Configure logging from environment variables.

    Reads:
    - ``SOVEREIGN_LOG_LEVEL``: Default log level (default: ``INFO``)
    - ``SOVEREIGN_LOG_FORMAT``: ``json`` or ``console`` (default: ``console``)
    """
    level = os.getenv("SOVEREIGN_LOG_LEVEL", "INFO")
    fmt = os.getenv("SOVEREIGN_LOG_FORMAT", "console")
    setup_logging(level=level, json_mode=(fmt.lower() == "json"))
