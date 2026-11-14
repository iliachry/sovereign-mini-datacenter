# Edge Vision AI Pipeline

This template shows how an **$L_2$ Background** computer vision workload is declared, paused and resumed on SMDC.

> **Simulated reference skeleton.** `app.py` loads no model, reads no camera and uses no GPU, TensorRT or CUDA: frame counts, latencies and detections are random numbers (`"simulated": true` in its telemetry). The manifest's resource figures, ports and health check describe what a real implementation would declare: `app.py` opens no port and serves no `/health` endpoint, and `smdc app start` only records the app as running (the manager launches no process).


## Architecture
- **Power Priority**: `L2_BACKGROUND`. When the node publishes a load-shedding event of level 2 or higher, the `EnterpriseManager` marks this app `paused`, and it marks it `running` again when shedding returns to level 0. The manifest thresholds (`min_battery_soc: 40`, `min_solar_watts: 300`) are declarative metadata; nothing evaluates them automatically.
- **Pause delivery**: `app.py` reacts through `AppLifecycleHandler`, which follows the manager's `enterprise.edge-vision-ai.paused` / `.resumed` events on the in-process event bus. That only reaches the workload when it runs in the same Python process as the manager. Started as a separate process (`python3 app.py`), it is not paused automatically; the manager does not launch or signal OS processes.
- **GPU acceleration (not implemented)**: the manifest declares `gpu_required: true` and 4 GB of VRAM as an example of what a TensorRT / CUDA pipeline would request. Replace the simulated loop in `app.py` with a real inference engine.
- **Space DTN integration**: on a random 5% of iterations (standing in for "anomaly detected") the app calls `client.send_dtn_bundle`, which queues a BPv7 bundle in the node's DTN spool when the node API is reachable. No radio link transmits it.

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
