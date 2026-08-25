"""Sovereign Mini Datacenter — Enterprise Application Registry & Scaffolding.

Provides registry management, directory discovery, schema validation, and turnkey
manifest scaffolding for enterprise application onboarding.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from sovereign_dc.enterprise.schema import (
    AppCategory,
    AppManifest,
    HealthProbe,
    NetworkPolicy,
    PowerPolicy,
    PowerPriority,
    ResourceQuotas,
    RuntimeType,
    StoragePolicy,
)

logger = logging.getLogger("smdc.enterprise.registry")

# Default system directories searched for enterprise app manifests
DEFAULT_APP_DIRECTORIES: list[Path] = [
    Path("/etc/smdc/apps"),
    Path.home() / ".smdc" / "apps",
    Path("./apps"),
    Path("./software/enterprise/apps"),
]


class EnterpriseRegistry:
    """Registry managing discovered and installed enterprise applications."""

    def __init__(self, registry_file: Path | None = None) -> None:
        self.registry_file = registry_file or (Path.home() / ".smdc" / "enterprise_registry.json")
        self._apps: dict[str, AppManifest] = {}
        self._app_paths: dict[str, Path] = {}
        self.load_registry()

    def load_registry(self) -> None:
        """Load registered apps from registry JSON cache if present."""
        if self.registry_file.exists():
            try:
                with open(self.registry_file, encoding="utf-8") as f:
                    data = json.load(f)
                    for app_data in data.get("apps", []):
                        manifest = AppManifest.from_dict(app_data)
                        self._apps[manifest.app_id] = manifest
                        if "manifest_path" in app_data and os.path.exists(app_data["manifest_path"]):
                            self._app_paths[manifest.app_id] = Path(app_data["manifest_path"])
                logger.debug("Loaded %d apps from registry cache %s", len(self._apps), self.registry_file)
            except Exception as e:
                logger.warning("Failed to load registry cache %s: %s", self.registry_file, e)

    def save_registry(self) -> None:
        """Persist registered apps to registry JSON file."""
        try:
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            apps_payload = []
            for app_id, manifest in self._apps.items():
                app_dict = manifest.to_dict()
                if app_id in self._app_paths:
                    app_dict["manifest_path"] = str(self._app_paths[app_id])
                apps_payload.append(app_dict)
            with open(self.registry_file, "w", encoding="utf-8") as f:
                json.dump({"version": "1.0.0", "apps": apps_payload}, f, indent=2)
            logger.debug("Saved %d apps to %s", len(self._apps), self.registry_file)
        except Exception as e:
            logger.error("Failed to save registry cache %s: %s", self.registry_file, e)

    def discover_apps(self, search_dirs: list[Path] | None = None) -> list[AppManifest]:
        """Scan directories for `smdc-app.yaml` / `smdc-app.json` manifests.

        Returns:
            List of successfully discovered and validated AppManifest objects.
        """
        dirs = search_dirs or DEFAULT_APP_DIRECTORIES
        discovered_map: dict[str, AppManifest] = {}

        for d in dirs:
            if not d.exists() or not d.is_dir():
                continue
            for pattern in ["smdc-app.yaml", "smdc-app.yml", "smdc-app.json", "**/smdc-app.yaml", "**/smdc-app.json"]:
                for match in d.glob(pattern):
                    manifest = self.load_manifest_file(match)
                    if manifest and manifest.app_id not in discovered_map:
                        self.register_app(manifest, manifest_path=match, persist=False)
                        discovered_map[manifest.app_id] = manifest

        self.save_registry()
        return list(discovered_map.values())

    def load_manifest_file(self, path: Path | str) -> AppManifest | None:
        """Parse and validate a manifest file from disk (YAML or JSON)."""
        p = Path(path)
        if not p.exists():
            logger.error("Manifest file not found: %s", p)
            return None

        try:
            content = p.read_text(encoding="utf-8")
            if p.suffix in [".yaml", ".yml"]:
                data = self._parse_yaml(content)
            else:
                data = json.loads(content)

            manifest = AppManifest.from_dict(data)
            errors = manifest.validate()
            if errors:
                logger.error("Validation failed for %s: %s", p, "; ".join(errors))
                return None
            return manifest
        except Exception as e:
            logger.error("Error reading manifest %s: %s", p, e)
            return None

    def register_app(
        self, manifest: AppManifest, manifest_path: Path | str | None = None, persist: bool = True
    ) -> tuple[bool, list[str]]:
        """Register an enterprise application in the local registry."""
        errors = manifest.validate()
        if errors:
            return False, errors

        self._apps[manifest.app_id] = manifest
        if manifest_path:
            self._app_paths[manifest.app_id] = Path(manifest_path)

        if persist:
            self.save_registry()
        logger.info("Registered enterprise application '%s' (%s)", manifest.name, manifest.app_id)
        return True, []

    def unregister_app(self, app_id: str, persist: bool = True) -> bool:
        """Unregister an application from the registry."""
        if app_id in self._apps:
            del self._apps[app_id]
            self._app_paths.pop(app_id, None)
            if persist:
                self.save_registry()
            logger.info("Unregistered enterprise app %s", app_id)
            return True
        return False

    def get_app(self, app_id: str) -> AppManifest | None:
        """Retrieve manifest for a registered app by ID."""
        return self._apps.get(app_id)

    def get_app_path(self, app_id: str) -> Path | None:
        """Get filesystem path to registered manifest."""
        return self._app_paths.get(app_id)

    def list_apps(self) -> list[AppManifest]:
        """Return all registered application manifests."""
        return list(self._apps.values())

    @staticmethod
    def _parse_yaml(content: str) -> dict[str, Any]:
        """Parse YAML content using PyYAML if present, or fallback simple parser."""
        try:
            import yaml

            parsed = yaml.safe_load(content)
            if isinstance(parsed, dict):
                return parsed
        except ImportError:
            pass

        # Fallback basic JSON or simple line parser
        try:
            return json.loads(content)
        except Exception:
            pass

        # Lightweight rudimentary YAML parser for core key-values
        result: dict[str, Any] = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if v.isdigit():
                    result[k] = int(v)
                elif v.lower() == "true":
                    result[k] = True
                elif v.lower() == "false":
                    result[k] = False
                else:
                    result[k] = v
        return result

    @classmethod
    def scaffold_manifest(
        cls,
        name: str,
        app_id: str,
        category: AppCategory = AppCategory.CUSTOM,
        runtime: RuntimeType = RuntimeType.PROCESS,
        entrypoint: str = "python3 app.py",
        gpu_required: bool = False,
        power_tier: PowerPriority = PowerPriority.L1_STANDARD,
        ports: list[int] | None = None,
    ) -> AppManifest:
        """Generate a pre-configured turnkey manifest template for a given archetype."""
        if category == AppCategory.IOT:
            resources = ResourceQuotas(
                cpu_cores=0.5, ram_mb=256, gpu_vram_mb=0, storage_mb=512, gpu_required=False, max_power_w=8.0
            )
            power = PowerPolicy(tier=PowerPriority.L0_CRITICAL, min_battery_soc=15.0, min_solar_watts=0.0)
            network = NetworkPolicy(ports=ports or [8081], expose_wireguard=True, lora_heartbeat=True)
            health = HealthProbe(type="http", endpoint="/health", port=8081)
        elif category == AppCategory.AI_INFERENCE:
            resources = ResourceQuotas(
                cpu_cores=4.0,
                ram_mb=4096,
                gpu_vram_mb=4096,
                storage_mb=8192,
                gpu_required=True,
                max_power_w=65.0,
            )
            power = PowerPolicy(
                tier=power_tier if power_tier != PowerPriority.L1_STANDARD else PowerPriority.L2_BACKGROUND,
                min_battery_soc=45.0,
                min_solar_watts=300.0,
            )
            network = NetworkPolicy(ports=ports or [8000], expose_wireguard=True)
            health = HealthProbe(type="http", endpoint="/v1/health", port=8000)
        elif category == AppCategory.SPATIAL_MEDIA:
            resources = ResourceQuotas(
                cpu_cores=2.0,
                ram_mb=2048,
                gpu_vram_mb=2048,
                storage_mb=4096,
                gpu_required=gpu_required,
                max_power_w=40.0,
            )
            power = PowerPolicy(tier=PowerPriority.L1_STANDARD, min_battery_soc=30.0)
            network = NetworkPolicy(ports=ports or [8085], expose_wireguard=True)
            health = HealthProbe(type="http", endpoint="/health", port=8085)
        elif category == AppCategory.DISTRIBUTED:
            resources = ResourceQuotas(
                cpu_cores=2.0, ram_mb=2048, gpu_vram_mb=0, storage_mb=4096, gpu_required=False, max_power_w=20.0
            )
            power = PowerPolicy(tier=PowerPriority.L1_STANDARD, min_battery_soc=25.0)
            network = NetworkPolicy(
                ports=ports or [9000, 9001], expose_wireguard=True, space_dtn_enabled=True, lora_heartbeat=True
            )
            health = HealthProbe(type="tcp", endpoint="localhost", port=9000)
        else:
            resources = ResourceQuotas(
                cpu_cores=1.0,
                ram_mb=512,
                gpu_vram_mb=1024 if gpu_required else 0,
                storage_mb=1024,
                gpu_required=gpu_required,
                max_power_w=25.0,
            )
            power = PowerPolicy(tier=power_tier, min_battery_soc=30.0)
            network = NetworkPolicy(ports=ports or [8080], expose_wireguard=True)
            health = HealthProbe(type="http", endpoint="/health", port=8080)

        return AppManifest(
            name=name,
            app_id=app_id,
            version="1.0.0",
            description=f"Enterprise {category.value} workload for Sovereign Mini Datacenter.",
            author="Enterprise Engineer",
            category=category,
            runtime=runtime,
            entrypoint=entrypoint,
            resources=resources,
            power=power,
            network=network,
            storage=StoragePolicy(persistent_volume=f"{app_id}-data", mount_point=f"/var/lib/smdc/apps/{app_id}/data"),
            health_check=health,
        )

    @classmethod
    def create_project_scaffold(
        cls, target_dir: Path | str, manifest: AppManifest, create_sample_code: bool = True
    ) -> Path:
        """Generate a complete enterprise application project directory."""
        dest = Path(target_dir)
        dest.mkdir(parents=True, exist_ok=True)

        manifest_file = dest / "smdc-app.yaml"
        manifest_json_file = dest / "smdc-app.json"

        # Write manifest JSON for deterministic compatibility
        manifest_json_file.write_text(manifest.to_json(indent=2), encoding="utf-8")

        # Write YAML representation
        yaml_content = f"""# Sovereign Mini Datacenter (SMDC) — Enterprise App Manifest
name: "{manifest.name}"
app_id: "{manifest.app_id}"
version: "{manifest.version}"
description: "{manifest.description}"
author: "{manifest.author}"
category: "{manifest.category.value}"
runtime: "{manifest.runtime.value}"
entrypoint: "{manifest.entrypoint}"

resources:
  cpu_cores: {manifest.resources.cpu_cores}
  ram_mb: {manifest.resources.ram_mb}
  gpu_vram_mb: {manifest.resources.gpu_vram_mb}
  storage_mb: {manifest.resources.storage_mb}
  gpu_required: {str(manifest.resources.gpu_required).lower()}
  max_power_w: {manifest.resources.max_power_w}

power:
  tier: "{manifest.power.tier.value}"
  min_battery_soc: {manifest.power.min_battery_soc}
  max_ambient_temp_c: {manifest.power.max_ambient_temp_c}
  allow_solar_burst: {str(manifest.power.allow_solar_burst).lower()}
  min_solar_watts: {manifest.power.min_solar_watts}

network:
  ports: {manifest.network.ports}
  expose_wireguard: {str(manifest.network.expose_wireguard).lower()}
  space_dtn_enabled: {str(manifest.network.space_dtn_enabled).lower()}
  lora_heartbeat: {str(manifest.network.lora_heartbeat).lower()}

storage:
  persistent_volume: "{manifest.storage.persistent_volume}"
  mount_point: "{manifest.storage.mount_point}"
  backup_enabled: {str(manifest.storage.backup_enabled).lower()}

health_check:
  type: "{manifest.health_check.type}"
  endpoint: "{manifest.health_check.endpoint}"
  port: {manifest.health_check.port if manifest.health_check.port is not None else "null"}
  interval_sec: {manifest.health_check.interval_sec}
  timeout_sec: {manifest.health_check.timeout_sec}
"""
        manifest_file.write_text(yaml_content.strip() + "\n", encoding="utf-8")

        if create_sample_code:
            sample_py = dest / "app.py"
            sample_code = f'''"""Sample Enterprise Workload for Sovereign Mini Datacenter."""
import os
import sys
import time
import logging
from sovereign_dc.enterprise.sdk import SMDCClient, AppLifecycleHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("{manifest.app_id}")

def main():
    logger.info("Starting {manifest.name} ({manifest.app_id})...")
    client = SMDCClient()
    lifecycle = AppLifecycleHandler("{manifest.app_id}", client)

    logger.info("Connecting to SMDC Event Bus and Power Telemetry...")
    telemetry = client.get_telemetry()
    logger.info("Current Solar: %.1f W | Battery SoC: %.1f%%", telemetry.get("solar_watts", 0.0), telemetry.get("battery_soc", 100.0))

    try:
        while lifecycle.is_running:
            # Check if load shedding is requested
            if lifecycle.is_paused:
                logger.warning("Workload paused due to SMDC low-battery load shedding. Idling...")
                time.sleep(5)
                continue

            logger.info("Executing enterprise workload iteration...")
            # Perform custom business logic here (IoT aggregation, AI inference, etc.)
            client.emit_telemetry("{manifest.app_id}", {{"status": "ok", "processed_records": 10}})
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Gracefully shutting down {manifest.app_id}...")
    finally:
        lifecycle.stop()

if __name__ == "__main__":
    main()
'''
            sample_py.write_text(sample_code, encoding="utf-8")

            # Create Dockerfile
            dockerfile = dest / "Dockerfile"
            docker_content = f"""FROM python:3.11-slim
WORKDIR /app
COPY smdc-app.yaml smdc-app.json app.py /app/
RUN pip install --no-cache-dir sovereign-dc || true
EXPOSE {" ".join(str(p) for p in manifest.network.ports) if manifest.network.ports else "8080"}
ENTRYPOINT ["{manifest.entrypoint.split()[0]}"]
CMD {json.dumps(manifest.entrypoint.split()[1:])}
"""
            dockerfile.write_text(docker_content, encoding="utf-8")

            # Create README.md
            readme = dest / "README.md"
            readme_content = f"""# {manifest.name}

> Enterprise Onboarding Package for **Sovereign Mini Datacenter (SMDC)**
> **App ID**: `{manifest.app_id}` | **Category**: `{manifest.category.value}` | **Power Tier**: `{manifest.power.tier.value}`

## Quickstart

```bash
# 1. Validate manifest against local SMDC node
smdc app validate .

# 2. Register application with local SMDC node
smdc app register .

# 3. Start application runtime
smdc app start {manifest.app_id}

# 4. Check application telemetry and power budget
smdc app status {manifest.app_id}
```
"""
            readme.write_text(readme_content, encoding="utf-8")

        return dest
