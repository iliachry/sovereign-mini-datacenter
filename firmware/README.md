# ESP32 Sovereign Telemetry & Micro-Grid Bridge

This directory contains embedded microcontroller firmware for bridging physical sensors directly into the Sovereign Mini Datacenter monitoring stack.

Two interchangeable firmware flavours target the same wiring:

| File | Toolchain | Notes |
| :--- | :--- | :--- |
| `esp32_telemetry_bridge.ino` | Arduino IDE / arduino-cli | Bare-metal C++; serves `/metrics` & `/health` directly on port 80 |
| `esphome_smdc_bridge.yaml` | [ESPHome](https://esphome.io) ≥ 2025.x | Declarative YAML; native Home Assistant API, MQTT auto-discovery, Prometheus endpoint, OLED pages |

---

## 🔌 Hardware Pinout

| Subsystem | ESP32 Pin | Sensor / Protocol | Voltage Level |
| :--- | :--- | :--- | :--- |
| **Victron VE.Direct** | `GPIO 16 (RX)` / `GPIO 17 (TX)` | UART 19,200 baud 8N1 | 3.3V / 5V Optoisolated |
| **LiFePO4 BMS RS485** | `GPIO 21 (RX)` / `GPIO 22 (TX)` | MAX485 Modbus RTU 9,600 baud (Arduino flavour) | RS485 Differential A/B |
| **Coolant Temp Probes** | `GPIO 4` | Dallas DS18B20 (4.7kΩ Pull-up) | 3.3V 1-Wire Digital |
| **Status OLED** | `GPIO 21 (SDA)` / `GPIO 22 (SCL)` | SSD1306 128x64 I²C @ `0x3C` (ESPHome flavour) | 3.3V |

> GPIO 21/22 are shared: the Arduino flavour dedicates them to the RS485 BMS link, the ESPHome flavour to the I²C OLED. Re-map one of the two in the firmware if you need both peripherals simultaneously.

---

## 📡 Endpoints Provided

* **HTTP Prometheus Metrics:** `http://<ESP32_IP>:80/metrics`
* **Health Check:** `http://<ESP32_IP>:80/health`
* **Home Assistant MQTT:** Auto-discovery topics published under `homeassistant/sensor/sovereign/`
* **Home Assistant Native API (ESPHome flavour):** encrypted Noise transport on port `6053`

---

## 🚀 Flashing the ESPHome Firmware

1. Copy `secrets.yaml.example` to `secrets.yaml` and fill in Wi-Fi, MQTT, the OTA password and a fresh API encryption key:

   ```bash
   cp firmware/secrets.yaml.example firmware/secrets.yaml
   openssl rand -base64 32   # paste into api_encryption_key
   ```

2. Validate and build (no local toolchain required):

   ```bash
   docker run --rm -v "$PWD/firmware":/config ghcr.io/esphome/esphome:stable config  /config/esphome_smdc_bridge.yaml
   docker run --rm -v "$PWD/firmware":/config ghcr.io/esphome/esphome:stable compile /config/esphome_smdc_bridge.yaml
   ```

3. Flash over USB on first install, then OTA thereafter:

   ```bash
   docker run --rm --device=/dev/ttyUSB0 -v "$PWD/firmware":/config ghcr.io/esphome/esphome:stable run /config/esphome_smdc_bridge.yaml
   ```

`secrets.yaml` and the `.esphome/` build cache are git-ignored via `firmware/.gitignore`.

### VE.Direct parsing

The ESPHome flavour reads the Victron **text protocol** (`LABEL<TAB>VALUE` lines) on a `uart` bus and publishes the parsed fields to standard `template` sensors every 100 ms — no custom C++ component is required:

| VE.Direct label | Sensor | Unit conversion |
| :--- | :--- | :--- |
| `V` | Battery Voltage | mV → V |
| `I` | Battery Current | mA → A |
| `PPV` | Solar Harvest Power | W |
| `VPV` | Solar Array Voltage | mV → V |
| `SOC` | Battery SoC | ‰ → % |
| `H20` | Solar Yield Today | 0.01 kWh → Wh |
