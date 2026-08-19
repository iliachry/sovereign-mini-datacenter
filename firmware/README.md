# ESP32 Sovereign Telemetry & Micro-Grid Bridge

This directory contains embedded microcontroller firmware for bridging physical sensors directly into the Sovereign Mini Datacenter monitoring stack.

---

## ?? Hardware Pinout

| Subsystem | ESP32 Pin | Sensor / Protocol | Voltage Level |
| :--- | :--- | :--- | :--- |
| **Victron VE.Direct** | `GPIO 16 (RX)` / `GPIO 17 (TX)` | UART 19,200 baud 8N1 | 3.3V / 5V Optoisolated |
| **LiFePO4 BMS RS485** | `GPIO 21 (RX)` / `GPIO 22 (TX)` | MAX485 Modbus RTU 9,600 baud | RS485 Differential A/B |
| **Coolant Temp Probes** | `GPIO 4` | Dallas DS18B20 (4.7kO Pull-up) | 3.3V 1-Wire Digital |

---

## ?? Endpoints Provided

* **HTTP Prometheus Metrics:** `http://<ESP32_IP>:80/metrics`
* **Health Check:** `http://<ESP32_IP>:80/health`
* **Home Assistant MQTT:** Auto-discovery topics published under `homeassistant/sensor/sovereign/`