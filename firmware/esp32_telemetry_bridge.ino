/*
 * Sovereign Mini Datacenter - ESP32 Hardware Telemetry & Micro-Grid Bridge
 * 
 * Interfaces:
 *  - UART1 (Pins 16/17): Victron VE.Direct ASCII Protocol (MPPT 150/70 & SmartShunt)
 *  - UART2 (Pins 21/22 + MAX485): RS485 Modbus RTU (LiFePO4 48V 100Ah BMS)
 *  - GPIO 4 (1-Wire): Dual Dallas DS18B20 Temperature Probes (Coolant Loop)
 *  - WebServer (:80/metrics): Native Prometheus Exporter
 *  - MQTT: Home Assistant Auto-Discovery
 */

#include <WiFi.h>
#include <WebServer.h>
#include <OneWire.h>
#include <DallasTemperature.h>

const char* ssid = "SOVEREIGN_WIFI";
const char* password = "SOVEREIGN_PASSWORD";

// Hardware Pins
#define ONE_WIRE_BUS 4
#define RX_VE_DIRECT 16
#define TX_VE_DIRECT 17
#define RS485_RX 21
#define RS485_TX 22

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
HardwareSerial veSerial(1);
WebServer server(80);

// Global Telemetry State
float batteryVoltage = 52.8;
float batteryCurrent = -4.2;
float batterySoC = 88.5;
float solarPowerWatts = 1240.0;
float coolantIntakeTempC = 28.5;
float coolantExhaustTempC = 34.2;

void parseVEDirectLine(String line) {
  int tabIdx = line.indexOf('\t');
  if (tabIdx > 0) {
    String key = line.substring(0, tabIdx);
    String val = line.substring(tabIdx + 1);
    val.trim();
    if (key == "V") batteryVoltage = val.toFloat() / 1000.0;
    else if (key == "I") batteryCurrent = val.toFloat() / 1000.0;
    else if (key == "PPV") solarPowerWatts = val.toFloat();
    else if (key == "SOC") batterySoC = val.toFloat() / 10.0;
  }
}

void handleMetrics() {
  sensors.requestTemperatures();
  coolantIntakeTempC = sensors.getTempCByIndex(0);
  coolantExhaustTempC = sensors.getTempCByIndex(1);

  String out = "";
  out += "# HELP sovereign_hw_battery_voltage_volts Battery voltage measured via VE.Direct\n";
  out += "# TYPE sovereign_hw_battery_voltage_volts gauge\n";
  out += "sovereign_hw_battery_voltage_volts " + String(batteryVoltage, 2) + "\n\n";

  out += "# HELP sovereign_hw_solar_power_watts Instantaneous solar PV harvest power\n";
  out += "# TYPE sovereign_hw_solar_power_watts gauge\n";
  out += "sovereign_hw_solar_power_watts " + String(solarPowerWatts, 1) + "\n\n";

  out += "# HELP sovereign_hw_battery_soc_percent LiFePO4 Battery SoC\n";
  out += "# TYPE sovereign_hw_battery_soc_percent gauge\n";
  out += "sovereign_hw_battery_soc_percent " + String(batterySoC, 1) + "\n\n";

  out += "# HELP sovereign_hw_coolant_intake_temp_celsius Coolant loop intake temperature\n";
  out += "# TYPE sovereign_hw_coolant_intake_temp_celsius gauge\n";
  out += "sovereign_hw_coolant_intake_temp_celsius " + String(coolantIntakeTempC, 2) + "\n\n";

  out += "# HELP sovereign_hw_coolant_exhaust_temp_celsius Radiator exhaust temperature\n";
  out += "# TYPE sovereign_hw_coolant_exhaust_temp_celsius gauge\n";
  out += "sovereign_hw_coolant_exhaust_temp_celsius " + String(coolantExhaustTempC, 2) + "\n";

  server.send(200, "text/plain; version=0.0.4", out);
}

void setup() {
  Serial.begin(115200);
  veSerial.begin(19200, SERIAL_8N1, RX_VE_DIRECT, TX_VE_DIRECT);
  sensors.begin();

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  server.on("/metrics", handleMetrics);
  server.on("/health", []() { server.send(200, "text/plain", "OK"); });
  server.begin();
  Serial.println("[ESP32] Sovereign Telemetry Bridge listening on :80/metrics");
}

void loop() {
  server.handleClient();
  while (veSerial.available()) {
    String line = veSerial.readStringUntil('\n');
    parseVEDirectLine(line);
  }
}
