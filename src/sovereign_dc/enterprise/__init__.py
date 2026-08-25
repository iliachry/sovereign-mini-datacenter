"""Sovereign Mini Datacenter — Enterprise Application & Onboarding Framework.

Provides standardized schemas, registries, lifecycle supervision, and SDK interfaces
for onboarding custom enterprise applications and workloads onto the SMDC platform.
"""

from sovereign_dc.enterprise.manager import AppRuntimeState, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import (
    AppCategory,
    AppManifest,
    AppStatus,
    HealthProbe,
    NetworkPolicy,
    PowerPolicy,
    PowerPriority,
    ResourceQuotas,
    RuntimeType,
    StoragePolicy,
)
from sovereign_dc.enterprise.sdk import AppLifecycleHandler, SMDCClient

__all__ = [
    "AppCategory",
    "PowerPriority",
    "RuntimeType",
    "AppStatus",
    "ResourceQuotas",
    "PowerPolicy",
    "NetworkPolicy",
    "StoragePolicy",
    "HealthProbe",
    "AppManifest",
    "EnterpriseRegistry",
    "EnterpriseManager",
    "AppRuntimeState",
    "SMDCClient",
    "AppLifecycleHandler",
]
