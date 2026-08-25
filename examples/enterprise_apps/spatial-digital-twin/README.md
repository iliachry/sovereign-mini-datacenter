# Spatial 3D Digital Twin Engine

This template demonstrates an **$L_1$ Standard** interactive 3D spatial digital twin workload on SMDC.

## Architecture
- **Power Priority**: `L1_STANDARD` (Standard web/UI service operational down to 25% SoC).
- **Visualization**: Three.js WebGL / WebSocket live telemetry stream.
- **Resource Footprint**: 2.0 CPU Cores, 2048 MB RAM, 25 W max power budget.

## Quickstart
```bash
# Validate manifest
smdc app validate examples/enterprise_apps/spatial-digital-twin/

# Register and start
smdc app register examples/enterprise_apps/spatial-digital-twin/
smdc app start spatial-digital-twin
```
