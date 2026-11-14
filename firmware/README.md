# ESP32 Sovereign Telemetry & Micro-Grid Bridge

This directory contains embedded microcontroller firmware for bridging physical sensors directly into the Sovereign Mini Datacenter monitoring stack.

Two firmware flavours target the same VE.Direct and DS18B20 wiring. They are not feature-equivalent:

| File | Toolchain | What it implements |
| :--- | :--- | :--- |
| `esp32_telemetry_bridge.ino` | Arduino IDE / arduino-cli | VE.Direct text protocol (checksum-validated), 2x DS18B20, Prometheus `/metrics` and `/health` on port 80, status log on USB serial. **No** MQTT, **no** OLED, **no** RS485/Modbus. |
| `esphome_smdc_bridge.yaml` | [ESPHome](https://esphome.io) ≥ 2025.x | VE.Direct text protocol (checksum-validated), 2x DS18B20, Prometheus `/metrics`, native Home Assistant API, MQTT auto-discovery, SSD1306 OLED pages. **No** RS485/Modbus. |

Neither flavour talks to the battery packs' own BMS over RS485/Modbus. Battery voltage, current and state of charge come from a Victron SmartShunt over VE.Direct.

### One VE.Direct port, one Victron device

Both flavours read a single VE.Direct port, and what they can report depends on what is plugged into it:

| Connected device | `V` / `I` | `SOC` | `PPV` / `VPV` / `H20` |
| :--- | :---: | :---: | :---: |
| SmartShunt / BMV battery monitor | yes | yes | no |
| SmartSolar / BlueSolar MPPT | yes | **no** (an MPPT never sends SOC) | yes |

Fields the connected device does not send are not exported (Arduino) or stay `unknown` (ESPHome). Reading the shunt and the MPPT at the same time needs a second UART, which neither flavour configures yet.

---

## 🔌 Hardware Pinout

| Subsystem | ESP32 Pin | Sensor / Protocol | Voltage Level |
| :--- | :--- | :--- | :--- |
| **Victron VE.Direct** | `GPIO 16 (RX)` / `GPIO 17 (TX)` | UART 19,200 baud 8N1 | 3.3V / 5V Optoisolated |
| **Coolant Temp Probes** | `GPIO 4` | Dallas DS18B20 (4.7kΩ Pull-up) | 3.3V 1-Wire Digital |
| **Status OLED** | `GPIO 21 (SDA)` / `GPIO 22 (SCL)` | SSD1306 128x64 I²C @ `0x3C` (ESPHome flavour only) | 3.3V |

> The Arduino flavour leaves GPIO 21/22 unused. The VE.Direct port must be connected through an isolated VE.Direct interface or level shifter suited to the device's signal level; check the Victron documentation for your device before wiring it to a 3.3V GPIO.

---

## 📡 Endpoints Provided

| Endpoint | Arduino flavour | ESPHome flavour |
| :--- | :---: | :---: |
| Prometheus metrics `http://<ESP32_IP>:80/metrics` | yes | yes |
| Health check `http://<ESP32_IP>:80/health` | yes | no |
| Home Assistant MQTT auto-discovery (prefix `homeassistant`) | no | yes |
| Home Assistant native API, encrypted, port `6053` | no | yes |

### Metric validity (Arduino flavour)

The Arduino flavour exports a metric only after it has really been measured:

* VE.Direct values appear after the first checksum-valid frame and are withdrawn when no valid frame has arrived for 10 s.
* `sovereign_hw_vedirect_last_frame_age_seconds` is the time since the last valid frame; `sovereign_hw_vedirect_frames_valid_total` and `sovereign_hw_vedirect_checksum_errors_total` count good and rejected frames.
* A DS18B20 probe that does not answer (the library's `-127 °C` sentinel) is skipped, not exported. Conversions run in the background, so a scrape never blocks on the sensor.

---

## 🧪 Testing

Neither flavour has been run against a real Victron device yet, so the host tests below are the evidence that the VE.Direct parser is right. CI (job `quality-gate-firmware-validation`) runs all three steps on every push.

**1. Host-side parser tests** — need only a C++17 compiler and `python3`, no Arduino / PlatformIO / ESPHome toolchain:

```bash
tests/firmware/run.sh             # both flavours, one line per test, non-zero exit on failure
tests/firmware/run.sh checksum    # only the tests whose name contains "checksum"
CXX=g++ tests/firmware/run.sh     # pick the compiler (default: c++)
```

The runner compiles the **unmodified** `esp32_telemetry_bridge.ino` against stub headers (`tests/firmware/stubs/arduino/`), and the VE.Direct lambdas **extracted from `esphome_smdc_bridge.yaml` at build time** (`tests/firmware/extract_esphome_lambdas.py`) against `tests/firmware/stubs/esphome/`. Both run the same frame corpus (`tests/firmware/ve_shared.h`), so the two parsers cannot drift apart. It builds with AddressSanitizer + UBSan when the compiler can run them (CI requires it via `SMDC_REQUIRE_SANITIZE=1`).

Covered: the byte-exact BMV-700 example frame from Victron's [VE.Direct Protocol FAQ](https://www.victronenergy.com/live/vedirect_protocol:faq) (Q8), battery-monitor and MPPT frames with unit conversion, every single-byte corruption of every frame, frames split across arbitrary reads, interleaved HEX messages, `---` placeholders, garbage / over-long lines, the 10 s staleness limit, the DS18B20 `-127 °C` sentinel and the exact `/metrics` text.

**2. Cross-compile the Arduino sketch for a real ESP32** (PlatformIO; the first run downloads the toolchain):

```bash
work="$(mktemp -d)" && mkdir "$work/src"
cp tests/firmware/platformio/platformio.ini "$work/"
cp firmware/esp32_telemetry_bridge.ino "$work/src/main.ino"
pio run -d "$work"                # or: uvx platformio run -d "$work"
```

**3. Validate the ESPHome configuration** (schema check, no compile):

```bash
cp firmware/secrets.yaml.example firmware/secrets.yaml   # skip if you already have one
esphome config firmware/esphome_smdc_bridge.yaml         # or: uvx esphome config ...
```

---

## 🚀 Flashing the Arduino Firmware

1. Copy `secrets.h.example` to `secrets.h` (git-ignored) and set `WIFI_SSID` / `WIFI_PASSWORD`. Without `secrets.h` the sketch compiles with placeholder credentials that cannot join a network.
2. Install the `OneWire` and `DallasTemperature` libraries and the ESP32 board package, then build and upload with the Arduino IDE or `arduino-cli`. Both tools require the sketch to sit in a folder with the same name as the `.ino` file, so copy `esp32_telemetry_bridge.ino` and `secrets.h` into a folder named `esp32_telemetry_bridge/` first.

---

## 🚀 Flashing the ESPHome Firmware

1. Copy `secrets.yaml.example` to `secrets.yaml` and fill in Wi-Fi, MQTT, the OTA password and a fresh API encryption key:

   ```bash
   cp firmware/secrets.yaml.example firmware/secrets.yaml
   openssl rand -base64 32   # paste into api_encryption_key
   ```

   The `api_encryption_key` shipped in `secrets.yaml.example` is a **public placeholder, not a secret**: it is a syntactically valid base64 32-byte value only so that `esphome config` / `esphome compile` accept the unmodified example. Never flash a device with it; always replace it with the output of `openssl rand -base64 32`.

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

The ESPHome flavour reads the Victron **text protocol** (`LABEL<TAB>VALUE` lines) on a `uart` bus every 100 ms without blocking and publishes the fields to standard `template` sensors once the frame's checksum is verified (the modulo-256 sum of all bytes of a frame, including the checksum byte, must be 0) — no custom C++ component is required:

| VE.Direct label | Sensor | Unit conversion |
| :--- | :--- | :--- |
| `V` | Battery Voltage | mV → V |
| `I` | Battery Current | mA → A |
| `PPV` | Solar Harvest Power | W |
| `VPV` | Solar Array Voltage | mV → V |
| `SOC` | Battery SoC | ‰ → % |
| `H20` | Solar Yield Today | 0.01 kWh → Wh |

`SOC` is only sent by a battery monitor (SmartShunt / BMV); `PPV`, `VPV` and `H20` only by an MPPT.

### Stale VE.Direct readings (ESPHome flavour)

The last reading is not reported forever. When no checksum-valid frame has arrived for 10 s (cable unplugged, Victron device off), a 1 s watchdog publishes `NaN` to all six VE.Direct sensors: Home Assistant shows them as unknown, and `/metrics` reports them as failed (`esphome_sensor_failed 1`) instead of exporting a value. They are published again with the next valid frame. The diagnostic sensor **VE.Direct Last Valid Frame Age** (seconds, updated every 5 s) is unknown until the first valid frame and keeps counting while the readings are withdrawn.

### DS18B20 addresses

The two `dallas_temp` addresses in `esphome_smdc_bridge.yaml` are **placeholders**. Flash once, read the 1-Wire addresses the device logs at boot, and replace them; until then the temperature sensors stay unavailable.

### No automatic reboot without Home Assistant

`api.reboot_timeout` and `mqtt.reboot_timeout` are set to `0s`, so the bridge keeps serving `/metrics` when no Home Assistant client or MQTT broker is connected (the ESPHome default is a reboot every 15 minutes).
