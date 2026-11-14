# Bill of Materials (BOM) & Component Specifications

> **Total Estimated Build Cost:** $23,257 USD for the priced lines below (sum of quantity × estimated unit cost): $13,900 for the two DGX Spark compute nodes plus $9,357 for everything else (host, storage and switch $1,940; power and solar $6,010; host liquid loop and enclosure fans $615; network cabling $170; enclosure $622). This **excludes** the items listed as not yet in the BOM in sections 2 and 3 (battery fuses, DC and PV cabling, lugs and connectors, PV isolator, panel mounting, earthing hardware, 12V supply, fan mounting for the exhaust opening), as well as shipping and taxes.

---

## 1. Compute & System Architecture

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **GPU Compute** | NVIDIA DGX Spark (128GB / 4TB) | NVIDIA GB10 Grace Blackwell Superchip (20-core Arm), 128GB unified LPDDR5x memory, 4TB NVMe M.2, 10GbE RJ-45 + ConnectX-7 (2× QSFP), 240W external power supply | 2 | $6,950 |
| **Networking** | Mikrotik CRS309-1G-8S+IN | 8x 10GbE SFP+, L2/L3 Hardware Offload, RouterOS | 1 | $270 |
| **Storage (Primary)** | Samsung 990 PRO 4TB NVMe SSD | PCIe 4.0 (7,450 MB/s Read, 6,900 MB/s Write) | 2 | $320 |
| **System Host** | Custom Mini-ITX Server Board | Intel Xeon E-2300 or AMD EPYC 4004 Series | 1 | $650 |
| **RAM (System Host)** | ECC Unbuffered 32GB module, matched to the board | DDR5 ECC unbuffered for EPYC 4004 (AM5) boards, DDR4 ECC unbuffered for Xeon E-2300 (LGA1200) boards; neither platform accepts registered (RDIMM) memory. Form factor (UDIMM or SO-DIMM) and speed per the board's QVL. | 2 | $160 |
| **Boot Drive** | Samsung 980 500GB NVMe | PCIe 3.0, OS boot drive for Ubuntu Server | 1 | $60 |

> DGX Spark price is the $6,950.00 that NVIDIA lists for the 128GB / 4TB DGX Spark on its own marketplace page ([NVIDIA Marketplace](https://marketplace.nvidia.com/en-us/enterprise/personal-ai-supercomputers/dgx-spark/), checked on 2026-11-14); check current pricing before ordering. Specifications are from NVIDIA's [DGX Spark hardware overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html). A lower-power alternative is the NVIDIA Jetson AGX Orin 64GB; it is not the reference build and is not priced here.

---

## 2. Power & Solar Energy Subsystem

> Total DC-coupled solar capacity: 3,280W peak (8 × 410W), about 14.5 kWh/day on annual average (5.2 peak sun hours, 85% system efficiency). The reference duty cycle (both DGX Spark nodes at their 240W supply rating for 12 h/day, 45W idle estimate otherwise, 180W base load) draws about 11.2 kWh/day, a 465W average, so the 8-panel array covers it with a surplus of about 3.3 kWh/day (yield is about 130% of the draw). This is an annual-average balance: winter yield is lower (see `BENCHMARKS.md`), and the shortfall on low-sun days comes from the battery, then from grid or generator through the MultiPlus-II. Zero-sun autonomy at that load: ~18.7 hours from the 8.70 kWh usable at 85% depth of discharge.

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Battery Storage** | Server Rack LiFePO4 48V 100Ah | 5.12 kWh per unit, 10.24 kWh total, 6,000+ cycles, integrated cell-level BMS in each pack | 2 | $1,200 |
| **Solar Charge Controller** | Victron SmartSolar MPPT 250/70-Tr | 250V absolute max PV open-circuit voltage (245V start-up and operating max), 35A max PV short-circuit current, 70A rated charge current, 4,000W nominal PV power at 48V, Bluetooth and VE.Direct ([Victron datasheet](https://www.victronenergy.com/upload/documents/Datasheet-SmartSolar-charge-controller-MPPT-250-60-and-250-70-EN.pdf)) | 1 | $600 |
| **Hybrid Inverter/Charger** | Victron MultiPlus-II 48/3000/35-32 | 3,000VA Continuous, Pure Sine, Grid-Tie Ready | 1 | $1,100 |
| **Solar PV Array** | Monocrystalline 410W PERC Panels | 4S2P config: two strings of four panels, 1,640W per string, 3,280W total. Panel limits for this design: Voc ≤ 50V and Isc ≤ 14A at STC, 1000V or 1500V DC system rating (see the PV sizing note below) | 8 | $180 |
| **Battery Monitor** | Victron SmartShunt 500A/50mV | Shunt-based battery monitor in the common battery negative: voltage, current and state of charge over VE.Direct and Bluetooth. Cell protection is done by the BMS built into each pack. | 1 | $130 |
| **DC Breaker Panel** | Victron Lynx Distributor | Fused busbar for clean 48V DC distribution | 1 | $90 |
| **PDU (Rack)** | 1U 230V metered rack PDU, 8× IEC C13 (model to be selected) | 230V AC input from the inverter, 1U rack mount. Verify voltage, current rating, outlet type and metering granularity against the datasheet of the model you buy. | 1 | $250 |

> **PV sizing (4S2P on the MPPT 250/70):** the BOM names no panel model, so the limits are stated instead. Both common 410W formats fit:
>
> | Panel format (typical figures, check your datasheet) | String Voc at STC (4 in series) | String Voc at −10°C (−0.28%/°C) | Array Isc (2 strings) |
> | :--- | :---: | :---: | :---: |
> | 108 half-cell, Voc ≈ 38V, Isc ≈ 14A | 152V | ≈ 167V | 28A |
> | 144 half-cell, Voc ≈ 50V, Isc ≈ 11A | 200V | ≈ 220V | 22A |
>
> Controller limits (Victron datasheet linked above): 250V absolute maximum PV open-circuit voltage at the coldest conditions, 245V start-up and operating maximum, 35A maximum PV short-circuit current, 4,000W nominal PV power at 48V. The installer must confirm, with the Voc, Isc and temperature coefficient of the panels actually bought, that 4 × Voc corrected to the lowest site temperature stays below 245V and that 2 × Isc stays below 35A. The 3,280W array is within the 4,000W nominal PV power, so the controller does not clip. Do not use this array with a 150V controller: four panels in series exceed 150V, and four parallel strings of two 108-cell panels (56A) exceed the 50A PV short-circuit limit of the MPPT 150/70.
>
> **Charge current:** 3,280W into a 51.2V bank is about 64A, within the controller's 70A rating. When the MultiPlus-II 48/3000/35 is also charging from grid or generator, its charger adds up to 35A, so the worst case is about 64A + 35A = 99A into the 200Ah bank (two 100Ah packs in parallel), which is about 0.5C. Check that figure against the maximum charge current in the battery pack datasheet (two packs in parallel share it, about 50A each). If the packs allow less, two settings cap it: the MPPT's "max charge current" in VictronConnect, and the MultiPlus-II's charge current limit in VEConfigure or VictronConnect. Set them so that their sum does not exceed the bank's limit.

> **Not yet in this BOM (size from the datasheets before ordering):** the two 125A battery fuses with a DC voltage rating suitable for a 48V bank, 35mm² battery cable and lugs, the PV isolator (2-pole DC breaker or switch-disconnector rated for at least 250V DC and 35A), PV string cable, MC4 connectors and the two-string combiner, panel mounting structure, earthing hardware, and a 12V DC supply (for example a 48V-to-12V DC-DC converter) for the coolant pump and the six 120mm fans (three on the radiator, three exhaust) — nothing listed above provides 12V.

---

## 3. Cooling Subsystem (Host Liquid Loop and Enclosure Exhaust)

> The liquid loop cools the system host (the custom Mini-ITX server) only. Its heat load is the host CPU, on the order of 65–125W depending on the CPU fitted (check its datasheet), so the loop is a single CPU water block, one pump-reservoir and one 360mm radiator with three fans, which still leaves a large margin and allows low fan speeds. The two DGX Spark units are sealed, air-cooled appliances: they are not opened, carry no water block and are not part of the loop; they need unobstructed intake and exhaust airflow inside the enclosure (side vent slots).
>
> **Rear panel:** the enclosure's rear panel has two 360mm × 120mm openings. One carries the radiator with its three fans. The other carries the remaining three 120mm fans as plain exhaust fans, which pull air in through the side vent slots and across the air-cooled DGX Spark units, the inverter and the power electronics. The fan total stays at six.
>
> **Loop fittings:** six G1/4 compression fittings, two per component (water block in/out, pump-reservoir in/out, radiator in/out). The assembly manual uses no quick-disconnect couplings, so none are listed.
>
> **Not yet in this BOM:** mounting for the three exhaust fans in the second rear opening (a 3 × 120mm fan bracket, or fan-screw holes drilled around the opening), and fan grilles.

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Cooling Radiator** | EK-Quantum Surface P360M | 360mm Triple Fan, Copper Core, 30mm Thick; fills one of the two rear openings | 1 | $140 |
| **CPU Water Block** | CPU water block for the chosen socket (AM5 or LGA1200) | Generic copper cold-plate block with G1/4 ports and the mounting kit for the host board's socket (AM5 for EPYC 4004, LGA1200 for Xeon E-2300). No model is selected; the price is a mid-market estimate, not a quote. | 1 | $90 |
| **Industrial Pump** | Alphacool VPP755 Eispumpe Single | D5 Variant, 3,500 RPM, 350 L/h, PWM Control | 1 | $90 |
| **High-PPC Fans** | Noctua NF-A12x25 iPPC-3000 PWM | 120mm, 3,000 RPM, 102.1 m³/h, 49.5 dB(A); 3 on the radiator, 3 as exhaust fans in the second rear opening | 6 | $32 |
| **Coolant** | EK-CryoFuel Clear Concentrate (1:9) | Non-conductive, anti-corrosive glycol, UV stable. 1 L of mixed coolant fills the single-radiator loop and leaves a top-up reserve (measure the fill volume when priming). | 1 L | $30 / L |
| **Compression Fittings** | EK-Quantum Torque STC-10/16 | G1/4 thread compression fittings for 10/16mm soft tubing; 2 each for the water block, the pump-reservoir and the radiator | 6 | $8 |
| **Tubing** | EK-Tube ZMT 16mm OD Matte Black | Soft tubing 10/16mm, UV-stable rubber, 3m length (three runs: pump to block, block to radiator, radiator to reservoir) | 1 | $25 |

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
