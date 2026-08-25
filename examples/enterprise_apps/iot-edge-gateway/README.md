# Industrial IoT Edge Gateway

This template demonstrates an **$L_0$ Critical** low-power IoT telemetry gateway running directly on the Sovereign Mini Datacenter edge stack.

## Architecture
- **Power Priority**: `L0_CRITICAL` (Never paused during load shedding; maintains telemetry continuity down to 15% SoC).
- **Network**: Sub-GHz LoRa mesh heartbeat + WireGuard peer tunneling.
- **Resource Footprint**: 0.5 CPU Cores, 256 MB RAM, 8 W maximum power budget.

## Quickstart
```bash
# Validate manifest
smdc app validate examples/enterprise_apps/iot-edge-gateway/

# Register application on local node
smdc app register examples/enterprise_apps/iot-edge-gateway/

# Start workload
smdc app start iot-edge-gateway

# Check status
smdc app status iot-edge-gateway
```
