"""Sovereign Mini Datacenter — Enterprise Application Registry & Scaffolding.

Provides registry management, directory discovery, schema validation, and turnkey
manifest scaffolding for enterprise application onboarding.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import tempfile
import time
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
        # Registry entries that could not be parsed; written back verbatim on save so one
        # bad entry never causes data loss for itself or its neighbours.
        self._unparsed_entries: list[Any] = []
        # Set when the registry file is unreadable AND could not be backed up: saving is
        # then refused so the only copy of the operator's data is never overwritten.
        self._save_blocked = False
        self.load_registry()

    def _backup_unreadable_registry(self) -> Path | None:
        """Copies an unparseable registry file aside. Returns the backup path, or None on failure."""
        backup = self.registry_file.with_name(f"{self.registry_file.name}.corrupt-{int(time.time())}")
        counter = 0
        while backup.exists():
            counter += 1
            backup = self.registry_file.with_name(f"{self.registry_file.name}.corrupt-{int(time.time())}-{counter}")
        try:
            shutil.copy2(self.registry_file, backup)
            return backup
        except OSError as e:
            logger.error("Could not back up unreadable registry %s: %s", self.registry_file, e)
            return None

    def load_registry(self) -> None:
        """Load registered apps from registry JSON cache if present.

        Entries are parsed independently: a corrupt entry is skipped (and preserved for the
        next save) without affecting the others. A file that cannot be parsed at all is
        copied to ``<name>.corrupt-<timestamp>`` before any later save may replace it.
        """
        if not self.registry_file.exists():
            return
        try:
            with open(self.registry_file, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("apps", []), list):
                raise ValueError("registry root must be an object with an 'apps' list")
        except Exception as e:
            backup = self._backup_unreadable_registry()
            self._save_blocked = backup is None
            logger.error(
                "Registry cache %s is unreadable (%s); %s",
                self.registry_file,
                e,
                f"original preserved at {backup}" if backup else "saving is disabled to protect it",
            )
            return

        for index, app_data in enumerate(data.get("apps", [])):
            try:
                if not isinstance(app_data, dict):
                    raise ValueError("entry is not an object")
                manifest = AppManifest.from_dict(app_data)
                if not isinstance(manifest.app_id, str) or not manifest.app_id:
                    raise ValueError("entry has no app_id")
                self._apps[manifest.app_id] = manifest
                manifest_path = app_data.get("manifest_path")
                if isinstance(manifest_path, str) and os.path.exists(manifest_path):
                    self._app_paths[manifest.app_id] = Path(manifest_path)
            except Exception as e:
                self._unparsed_entries.append(app_data)
                logger.warning(
                    "Skipping corrupt entry #%d in registry cache %s: %s (entry preserved)",
                    index,
                    self.registry_file,
                    e,
                )
        logger.debug("Loaded %d apps from registry cache %s", len(self._apps), self.registry_file)

    def save_registry(self) -> None:
        """Persist registered apps to registry JSON file (atomic replace)."""
        if self._save_blocked:
            logger.error(
                "Not saving registry: %s failed to parse and could not be backed up; "
                "repair or move the file, then reload",
                self.registry_file,
            )
            return
        tmp_name: str | None = None
        try:
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            apps_payload: list[Any] = []
            for app_id, manifest in self._apps.items():
                app_dict = manifest.to_dict()
                if app_id in self._app_paths:
                    app_dict["manifest_path"] = str(self._app_paths[app_id])
                apps_payload.append(app_dict)
            apps_payload.extend(self._unparsed_entries)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self.registry_file.name}.", suffix=".tmp", dir=self.registry_file.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": "1.0.0", "apps": apps_payload}, f, indent=2)
            os.replace(tmp_name, self.registry_file)
            tmp_name = None
            logger.debug("Saved %d apps to %s", len(self._apps), self.registry_file)
        except Exception as e:
            logger.error("Failed to save registry cache %s: %s", self.registry_file, e)
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

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

    @staticmethod
    def render_manifest_yaml(manifest: AppManifest) -> str:
        """Serializes a manifest as an ``smdc-app.yaml`` document using a real YAML emitter."""
        header = "# Sovereign Mini Datacenter (SMDC) — Enterprise App Manifest\n"
        payload = manifest.to_dict()
        try:
            import yaml
        except ImportError:
            # JSON is a YAML subset, so this stays a loadable smdc-app.yaml.
            return header + json.dumps(payload, indent=2) + "\n"
        return header + yaml.safe_dump(payload, sort_keys=False, default_flow_style=False, allow_unicode=True)

    @staticmethod
    def _render_sample_app(manifest: AppManifest) -> str:
        """Renders the sample ``app.py``. Manifest strings are embedded only as ``repr()`` literals."""
        return f'''"""Sample Enterprise Workload for Sovereign Mini Datacenter."""

from __future__ import annotations

import logging
import time

from sovereign_dc.enterprise.sdk import AppLifecycleHandler, SMDCClient

APP_ID = {str(manifest.app_id)!r}
APP_NAME = {str(manifest.name)!r}

logger = logging.getLogger(APP_ID)


def main() -> None:
    logger.info("Starting %s (%s)...", APP_NAME, APP_ID)
    client = SMDCClient()
    lifecycle = AppLifecycleHandler(APP_ID, client)

    logger.info("Reading SMDC power telemetry...")
    telemetry = client.get_telemetry()
    logger.info(
        "Current Solar: %.1f W | Battery SoC: %.1f%% (source: %s)",
        telemetry.get("solar_watts", 0.0),
        telemetry.get("battery_soc", 0.0),
        telemetry.get("source", "unknown"),
    )

    try:
        while lifecycle.is_running:
            # is_paused turns True when the EnterpriseManager sharing this process's event
            # bus pauses this app (operator request or load shedding).
            if lifecycle.is_paused:
                logger.warning("Workload paused by the SMDC enterprise manager. Idling...")
                time.sleep(5)
                continue

            logger.info("Executing enterprise workload iteration...")
            # Perform custom business logic here (IoT aggregation, AI inference, etc.)
            client.emit_telemetry(APP_ID, {{"status": "ok", "processed_records": 10}})
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Gracefully shutting down %s...", APP_ID)
    finally:
        lifecycle.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
'''

    @staticmethod
    def _render_dockerfile(manifest: AppManifest) -> str:
        """Renders the Dockerfile; the entrypoint is emitted in JSON exec form only."""
        try:
            argv = shlex.split(str(manifest.entrypoint))
        except ValueError:
            argv = str(manifest.entrypoint).split()
        if not argv:
            argv = ["python3", "app.py"]
        ports: list[int] = []
        for port in manifest.network.ports:
            try:
                port_num = int(port)
            except (TypeError, ValueError):
                continue
            if 1 <= port_num <= 65535:
                ports.append(port_num)
        expose = " ".join(str(p) for p in ports) if ports else "8080"
        return (
            "FROM python:3.11-slim\n"
            "WORKDIR /app\n"
            "COPY smdc-app.yaml smdc-app.json app.py /app/\n"
            "RUN pip install --no-cache-dir sovereign-dc || true\n"
            f"EXPOSE {expose}\n"
            f"ENTRYPOINT {json.dumps(argv[:1])}\n"
            f"CMD {json.dumps(argv[1:])}\n"
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

        # Write YAML representation. Values are serialized by a YAML emitter (never string
        # interpolation), so quotes, colons or newlines in a name cannot corrupt the manifest.
        manifest_file.write_text(cls.render_manifest_yaml(manifest), encoding="utf-8")

        if create_sample_code:
            sample_py = dest / "app.py"
            sample_py.write_text(cls._render_sample_app(manifest), encoding="utf-8")

            # Create Dockerfile
            dockerfile = dest / "Dockerfile"
            dockerfile.write_text(cls._render_dockerfile(manifest), encoding="utf-8")

            # Create README.md
            readme = dest / "README.md"
            # Collapse whitespace/newlines and drop backticks so the values stay inside their
            # heading / inline-code span / shell line.
            safe_name = " ".join(str(manifest.name).split()) or "Enterprise App"
            safe_id = shlex.quote(str(manifest.app_id).replace("`", ""))
            readme_content = f"""# {safe_name}

> Enterprise Onboarding Package for **Sovereign Mini Datacenter (SMDC)**
> **App ID**: `{safe_id}` | **Category**: `{manifest.category.value}` | **Power Tier**: `{manifest.power.tier.value}`

## Quickstart

```bash
# 1. Validate manifest against local SMDC node
smdc app validate .

# 2. Register application with local SMDC node
smdc app register .

# 3. Start application runtime
smdc app start {safe_id}

# 4. Check application telemetry and power budget
smdc app status {safe_id}
```
"""
            readme.write_text(readme_content, encoding="utf-8")

        return dest
