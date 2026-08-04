# Bill of Materials (BOM) & Component Specifications

## 1. Compute & System Architecture

| Subsystem | Component Model | Spec / Description | Quantity | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **GPU Compute** | NVIDIA DGX Spark / Jetson Orin Industrial | 275 TOPS AI Performance, 64GB Unified Memory | 2 | Market |
| **Networking** | Mikrotik CRS309-1G-8S+IN | 8x 10GbE SFP+ Ports, L2/L3 Hardware Offload | 1 | $270 |
| **Storage (Primary)** | Samsung 990 PRO 4TB NVMe SSD | PCIe 4.0 (7,450 MB/s Read, 6,900 MB/s Write) | 2 | $320 |
| **System Host** | Custom Mini-ITX Server Board | Intel Xeon E-2300 or AMD EPYC 4004 Series | 1 | $650 |

---

## 2. Power & Solar Energy Subsystem

| Subsystem | Component Model | Spec / Description | Quantity | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Battery Storage** | Server Rack LiFePO4 48V 100Ah | 5.12 kWh nominal capacity, 6,000+ cycle life | 2 | $1,200 |
| **Solar Charge Controller** | Victron SmartSolar MPPT 150/70-Tr | Ultra-fast MPPT tracking, 150V Max PV, 70A Charge | 1 | $540 |
| **Hybrid Inverter/Charger** | Victron MultiPlus-II 48/3000/35-32 | 3000VA Continuous Output, Pure Sine Wave | 1 | $1,100 |
| **Solar PV Array** | Monocrystalline 410W Solar Panels | High Efficiency PERC, 1500V DC Rated | 4 | $180 |

---

## 3. Liquid Cooling Subsystem

| Subsystem | Component Model | Spec / Description | Quantity | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Cooling Radiators** | EK-Quantum Surface P360M | 360mm Triple Fan Copper Core Radiator | 2 | $140 |
| **Industrial Pump** | Alphacool VPP755 Eispumpe Single Edition | D5 Variant, 3500 RPM, 350 L/h Flow Rate | 1 | $90 |
| **High-PPC Fans** | Noctua NF-A12x25 industrialPPC-3000 | 120mm 3000 RPM PWM Industrial Fans | 6 | $32 |
| **Coolant Fluid** | EK-CryoFuel Clear Concentrate | Non-conductive anti-corrosive glycol blend | 2L | $30 |
