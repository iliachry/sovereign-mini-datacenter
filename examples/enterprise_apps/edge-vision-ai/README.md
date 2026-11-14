# Edge Vision AI Pipeline

This template demonstrates an **$L_2$ Background** GPU-accelerated computer vision workload on SMDC.

## Architecture
- **Power Priority**: `L2_BACKGROUND`. When the node publishes a load-shedding event of level 2 or higher, the `EnterpriseManager` marks this app `paused`, and it marks it `running` again when shedding returns to level 0. The manifest thresholds (`min_battery_soc: 40`, `min_solar_watts: 300`) are declarative metadata; nothing evaluates them automatically.
- **Pause delivery**: `app.py` reacts through `AppLifecycleHandler`, which follows the manager's `enterprise.edge-vision-ai.paused` / `.resumed` events on the in-process event bus. That only reaches the workload when it runs in the same Python process as the manager. Started as a separate process (`python3 app.py`), it is not paused automatically; the manager does not launch or signal OS processes.
- **GPU Acceleration**: NVIDIA Jetson Orin AGX TensorRT / CUDA runtime.
- **Space DTN Integration**: Spools RFC 9171 BPv7 telemetry bundles to orbital satellites when severe visual anomalies are identified.

## Quickstart
```bash
# Validate manifest
smdc app validate examples/enterprise_apps/edge-vision-ai/

# Register application
smdc app register examples/enterprise_apps/edge-vision-ai/

# Start pipeline
smdc app start edge-vision-ai

# Package application with Post-Quantum cryptographic signature
smdc app package examples/enterprise_apps/edge-vision-ai/ --output vision-ai-1.0.0.smdc-app
```
