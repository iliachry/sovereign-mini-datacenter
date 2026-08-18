# Bill of Materials (BOM) & Component Specifications

> **Total Estimated Build Cost:** ~$10,600 USD (compute at market price)

---

## 1. Compute & System Architecture

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **GPU Compute** | NVIDIA DGX Spark / Jetson Orin Industrial | 275 TOPS AI Performance, 64GB Unified Memory, NVLink | 2 | Market |
| **Networking** | Mikrotik CRS309-1G-8S+IN | 8x 10GbE SFP+, L2/L3 Hardware Offload, RouterOS | 1 | $270 |
| **Storage (Primary)** | Samsung 990 PRO 4TB NVMe SSD | PCIe 4.0 (7,450 MB/s Read, 6,900 MB/s Write) | 2 | $320 |
| **System Host** | Custom Mini-ITX Server Board | Intel Xeon E-2300 or AMD EPYC 4004 Series | 1 | $650 |
| **RAM (System Host)** | Kingston ECC DDR5-4800 RDIMM 32GB | ECC Registered, CL40, 1.1V | 2 | $160 |
| **Boot Drive** | Samsung 980 500GB NVMe | PCIe 3.0, OS boot drive for Ubuntu Server | 1 | $60 |

---

## 2. Power & Solar Energy Subsystem

> Total continuous DC-coupled solar capacity: ~1,640W peak. Autonomy at 500W average load: ~20 hours.

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Battery Storage** | Server Rack LiFePO4 48V 100Ah | 5.12 kWh per unit, 10.24 kWh total, 6,000+ cycles | 2 | $1,200 |
| **Solar Charge Controller** | Victron SmartSolar MPPT 150/70-Tr | Ultra-fast MPPT, 150V Max PV, 70A Charge, BLE | 1 | $540 |
| **Hybrid Inverter/Charger** | Victron MultiPlus-II 48/3000/35-32 | 3,000VA Continuous, Pure Sine, Grid-Tie Ready | 1 | $1,100 |
| **Solar PV Array** | Monocrystalline 410W PERC Panels | 2S2P Config: ~820W per string, 1500V DC Rated | 4 | $180 |
| **Battery BMS** | Victron BMS 12/200 | Pre-alarm, automatic load disconnect, 200A | 1 | $120 |
| **DC Breaker Panel** | Victron Lynx Distributor | Fused busbar for clean 48V DC distribution | 1 | $90 |
| **PDU (Rack)** | APC AP7930 8-Outlet Metered PDU | 230V, per-outlet monitoring, 1U rack mount | 1 | $250 |

---

## 3. Liquid Cooling Subsystem

> Dual 360mm loop handles sustained 600W+ GPU thermal dissipation.

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Cooling Radiators** | EK-Quantum Surface P360M | 360mm Triple Fan, Copper Core, 30mm Thick | 2 | $140 |
| **Industrial Pump** | Alphacool VPP755 Eispumpe Single | D5 Variant, 3,500 RPM, 350 L/h, PWM Control | 1 | $90 |
| **High-PPC Fans** | Noctua NF-A12x25 iPPC-3000 PWM | 120mm, 3,000 RPM, 102.1 m³/h, 49.5 dB(A) | 6 | $32 |
| **GPU Water Blocks** | EK-Quantum Vector DGX Block | Full-cover nickel+acetal, compatible with DGX Spark | 2 | $170 |
| **Coolant** | EK-CryoFuel Clear Concentrate (1:9) | Non-conductive, anti-corrosive glycol, UV stable | 2L | $30 |
| **Quick Disconnect Fittings** | EK-Quantum Torque STC-10/16 | Stainless steel, G1/4 thread, leak-free | 16 | $8 |
| **Tubing** | EK-Tube ZMT 16mm OD Matte Black | Soft tubing 10/16mm, UV-stable rubber, 3m | 1 | $25 |

---

## 4. Networking

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **SFP+ DAC Cables** | MikroTik S+DA0001 | 1m SFP+ Direct Attach Cable, 10GbE | 4 | $15 |
| **SFP+ to RJ45** | MikroTik S+RJ10 | 10GbE SFP+ to RJ45 Module | 2 | $45 |
| **Management Cable** | Cat6A Shielded Patch (0.3m) | Short run management links | 4 | $5 |

---

## 5. Enclosure & Mounting Hardware

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Rack Enclosure** | Custom 9U 540mm Deep Aluminum | Per `cad/rack_enclosure.scad`, 2mm 6061-T6 | 1 | $450 |
| **Rack Rails** | 19" M6 Tapped Steel Rails | 482.6mm spacing, 2mm cold-rolled steel | 2 | $30 |
| **Cable Management** | 1U Horizontal Cable Manager | D-ring brackets, brush insert, 1U | 2 | $20 |
| **M6 Cage Nut Kit** | Startech CABSCREWM62** | M6 cage nuts + screws, 50-pack | 1 | $12 |
