# Electrical & Thermal Engineering Specifications

## 1. Electrical Power Budget

| Subsystem | Component | Voltage (V) | Max Current (A) | Peak Power (W) | Avg Load (W) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Compute** | 2x Nvidia DGX Spark Nodes | 48V DC / 230V AC | 6.5A | 1,200 W | 650 W |
| **Cooling** | Dual Liquid Pumps & Radiator Fans | 12V DC | 4.0A | 48 W | 35 W |
| **Network** | Sovereign 10GbE Switch + Router | 12V DC | 2.5A | 30 W | 20 W |
| **Telemetry** | MPPT & Sensor Microcontroller | 5V DC | 1.0A | 5 W | 3 W |
| **TOTAL** | | | | **1,283 W** | **708 W** |

---

## 2. Solar PV & Battery Subsystem Sizing

* **Daily Energy Consumption (24h @ 708W avg):** `17.0 kWh / day`
* **Solar PV Generation Target (Greece/Med Solar Insolation = 5.2 peak sun hours):**
  * PV Array Wattage Required: `17,000 Wh / 5.2 hrs = 3,269 Wp` (approx. 8x 410W Monocrystalline PV panels).
* **LiFePO4 Battery Bank Sizing (24h Autonomy @ 80% Depth of Discharge):**
  * Required Capacity: `17.0 kWh / 0.80 = 21.25 kWh` (approx. 4x 5.12kWh 48V LiFePO4 rack modules).

---

## 3. Thermal Dissipation & Liquid Cooling Specifications

* **Thermal Load to Dissipate (Peak Compute Load):** `1,283 W = 4,378 BTU / hr`
* **Coolant Fluid:** Distilled Water + Ethylene Glycol (80/20) + Anti-corrosive / Anti-algae inhibitors.
* **Coolant Flow Rate:** `4.5 Liters / minute`
* **Radiator Surface Area:** Dual 360mm Copper Radiators (6x 120mm PWM Fans @ 1,800 RPM).
* **Maximum Allowed Fluid Temp:** `45°C` (ambient room/outdoor temp up to `38°C`).
