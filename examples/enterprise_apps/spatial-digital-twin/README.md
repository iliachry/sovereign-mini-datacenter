# Spatial 3D Digital Twin Engine

This template shows how an **$L_1$ Standard** digital-twin streaming workload is declared and wired to the SMDC SDK.

> **Simulated reference skeleton.** `app.py` renders nothing and serves no WebGL or WebSocket stream: the frame rate, viewer count and mesh count it publishes are fixed placeholder constants (`"simulated": true`). Only the node battery and coolant readings are real SDK reads. The project's actual Three.js twin lives in `docs/`. The manifest's resource figures, ports and health check describe what a real implementation would declare: `app.py` opens no port and serves no `/health` endpoint, and `smdc app start` only records the app as running (the manager launches no process).


## Architecture
- **Power Priority**: `L1_STANDARD` (an in-process `EnterpriseManager` marks the app paused at load-shedding level 4, battery SoC below 10 %, or while an event reports a battery SoC below the manifest's `min_battery_soc: 25`).
- **Visualization (not implemented)**: a real implementation would stream node telemetry to a Three.js WebGL client.
- **Resource Footprint (declared, not measured)**: 2.0 CPU Cores, 2048 MB RAM, 25 W max power budget.

## Quickstart
```bash
# Validate manifest
smdc app validate examples/enterprise_apps/spatial-digital-twin/

# Register and start
smdc app register examples/enterprise_apps/spatial-digital-twin/
smdc app start spatial-digital-twin
```
