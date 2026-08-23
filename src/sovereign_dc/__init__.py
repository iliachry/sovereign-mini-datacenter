"""
Sovereign Mini Datacenter CLI, Telemetry, Config, HAL & Autonomous Event Engine
"""

from sovereign_dc.config import SovereignConfig, get_config
from sovereign_dc.events import Event, EventType, SovereignEventBus, get_event_bus
from sovereign_dc.log import get_logger, setup_logging

__version__ = "1.4.0"
__author__ = "Ilias Chrysovergis"
__license__ = "MIT"

__all__ = [
    "SovereignConfig",
    "get_config",
    "Event",
    "EventType",
    "SovereignEventBus",
    "get_event_bus",
    "get_logger",
    "setup_logging",
    "__version__",
]
