# Industrial IoT Edge Gateway

This template shows how an **$L_0$ Critical** low-power IoT telemetry gateway is declared and wired to the SMDC SDK.

> **Simulated reference skeleton.** `app.py` reads no LoRa, Modbus or MQTT device: the vibration, temperature and pressure values are random numbers (`"simulated": true` in its telemetry). Only the node battery and solar figures are read, through `SMDCClient.get_telemetry()`. The manifest's resource figures, ports and health check describe what a real implementation would declare: `app.py` opens no port and serves no `/health` endpoint, and `smdc app start` only records the app as running (the manager launches no process).


## Architecture
- **Power Priority**: `L0_CRITICAL` (the `EnterpriseManager` never marks it paused during load shedding; `min_battery_soc: 15` is declarative metadata).
- **Network (manifest flags only)**: `lora_heartbeat` and `expose_wireguard` are declared; the app itself opens no LoRa or WireGuard connection.
- **Resource Footprint (declared, not measured)**: 0.5 CPU Cores, 256 MB RAM, 8 W maximum power budget.

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
