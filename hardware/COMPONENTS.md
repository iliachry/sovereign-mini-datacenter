# Bill of Materials (BOM) & Component Specifications

> **Total Estimated Build Cost:** $18,475 USD for the priced lines below (sum of quantity × estimated unit cost): $9,398 for the two DGX Spark compute nodes plus $9,077 for everything else (host, storage and switch $1,940; power and solar $5,230; liquid cooling $1,115; network cabling $170; enclosure $622). This **excludes** the items listed as not yet in the BOM in section 2 (battery fuses, DC cabling and lugs, PV breaker, earthing hardware, 12V supply), as well as shipping and taxes.

---

## 1. Compute & System Architecture

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **GPU Compute** | NVIDIA DGX Spark (Founders Edition) | NVIDIA GB10 Grace Blackwell Superchip (20-core Arm), 128GB unified LPDDR5x memory, 4TB NVMe M.2, 10GbE RJ-45 + ConnectX-7 (2× QSFP), 240W external power supply | 2 | $4,699 |
| **Networking** | Mikrotik CRS309-1G-8S+IN | 8x 10GbE SFP+, L2/L3 Hardware Offload, RouterOS | 1 | $270 |
| **Storage (Primary)** | Samsung 990 PRO 4TB NVMe SSD | PCIe 4.0 (7,450 MB/s Read, 6,900 MB/s Write) | 2 | $320 |
| **System Host** | Custom Mini-ITX Server Board | Intel Xeon E-2300 or AMD EPYC 4004 Series | 1 | $650 |
| **RAM (System Host)** | ECC Unbuffered 32GB module, matched to the board | DDR5 ECC unbuffered for EPYC 4004 (AM5) boards, DDR4 ECC unbuffered for Xeon E-2300 (LGA1200) boards; neither platform accepts registered (RDIMM) memory. Form factor (UDIMM or SO-DIMM) and speed per the board's QVL. | 2 | $160 |
| **Boot Drive** | Samsung 980 500GB NVMe | PCIe 3.0, OS boot drive for Ubuntu Server | 1 | $60 |

> DGX Spark price is NVIDIA's Founders Edition MSRP as announced in February 2026 ([NVIDIA developer forum](https://forums.developer.nvidia.com/t/2-23-2026-price-change-announcement/361713)); check current pricing before ordering. Specifications are from NVIDIA's [DGX Spark hardware overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html). A lower-power alternative is the NVIDIA Jetson AGX Orin 64GB; it is not the reference build and is not priced here.

---

## 2. Power & Solar Energy Subsystem

> Total DC-coupled solar capacity: ~1,640W peak, about 7.2 kWh/day on annual average (5.2 peak sun hours, 85% system efficiency). The reference duty cycle (both DGX Spark nodes at their 240W supply rating for 12 h/day, 45W idle estimate otherwise, 180W base load) draws about 11.2 kWh/day, a 465W average, so the 4-panel array covers roughly 65% of it. Zero-sun autonomy at that load: ~18.7 hours from the 8.70 kWh usable at 85% depth of discharge.

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Battery Storage** | Server Rack LiFePO4 48V 100Ah | 5.12 kWh per unit, 10.24 kWh total, 6,000+ cycles, integrated cell-level BMS in each pack | 2 | $1,200 |
| **Solar Charge Controller** | Victron SmartSolar MPPT 150/70-Tr | Ultra-fast MPPT, 150V Max PV, 70A Charge, BLE | 1 | $540 |
| **Hybrid Inverter/Charger** | Victron MultiPlus-II 48/3000/35-32 | 3,000VA Continuous, Pure Sine, Grid-Tie Ready | 1 | $1,100 |
| **Solar PV Array** | Monocrystalline 410W PERC Panels | 2S2P Config: ~820W per string, 1500V DC Rated | 4 | $180 |
| **Battery Monitor** | Victron SmartShunt 500A/50mV | Shunt-based battery monitor in the common battery negative: voltage, current and state of charge over VE.Direct and Bluetooth. Cell protection is done by the BMS built into each pack. | 1 | $130 |
| **DC Breaker Panel** | Victron Lynx Distributor | Fused busbar for clean 48V DC distribution | 1 | $90 |
| **PDU (Rack)** | 1U 230V metered rack PDU, 8× IEC C13 (model to be selected) | 230V AC input from the inverter, 1U rack mount. Verify voltage, current rating, outlet type and metering granularity against the datasheet of the model you buy. | 1 | $250 |

> **Not yet in this BOM (size from the datasheets before ordering):** the two 125A battery fuses with a DC voltage rating suitable for a 48V bank, 35mm² battery cable and lugs, the PV array DC breaker, earthing hardware, and a 12V DC supply (for example a 48V-to-12V DC-DC converter) for the coolant pump and radiator fans — nothing listed above provides 12V.

---

## 3. Liquid Cooling Subsystem

> Dual 360mm loop handles sustained 600W+ GPU thermal dissipation.

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Cooling Radiators** | EK-Quantum Surface P360M | 360mm Triple Fan, Copper Core, 30mm Thick | 2 | $140 |
| **Industrial Pump** | Alphacool VPP755 Eispumpe Single | D5 Variant, 3,500 RPM, 350 L/h, PWM Control | 1 | $90 |
| **High-PPC Fans** | Noctua NF-A12x25 iPPC-3000 PWM | 120mm, 3,000 RPM, 102.1 m³/h, 49.5 dB(A) | 6 | $32 |
| **GPU Water Blocks** | EK-Quantum Vector DGX Block | Full-cover nickel+acetal, compatible with DGX Spark | 2 | $170 |
| **Coolant** | EK-CryoFuel Clear Concentrate (1:9) | Non-conductive, anti-corrosive glycol, UV stable | 2 L | $30 / L |
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
| **Rack Enclosure** | Custom 9U Aluminum, 540mm W × 550mm D × 404mm H | Per `cad/rack_enclosure.scad`, 2mm 6061-T6, 400.05mm (9U) clear interior height | 1 | $450 |
| **Rack Rails** | 19" M6 Tapped Steel Rails, 9U | EIA-310-D hole pattern, mounting holes 465.1mm centre-to-centre (left to right), 2mm cold-rolled steel; 2 front + 2 rear | 4 | $30 |
| **Cable Management** | 1U Horizontal Cable Manager | D-ring brackets, brush insert, 1U | 2 | $20 |
| **M6 Cage Nut Kit** | Startech CABSCREWM62** | M6 cage nuts + screws, 50-pack | 1 | $12 |
