# Bill of Materials (BOM) & Component Specifications

> **Total Estimated Build Cost:** $24,563 USD, the sum of quantity × estimated unit cost over every row below: $13,900 for the two DGX Spark compute nodes plus $10,663 for everything else (host, storage and switch $2,250; power, solar, protection, cabling and telemetry $6,868; host liquid loop and enclosure fans $718; network cabling $170; enclosure and mounting $657).
>
> **How to read the prices:** every figure in an "Est. Unit Cost" column is an estimate, not a quote. The DGX Spark price is NVIDIA's own listed price (source and check date under the table in section 1). All other figures are conservative mid-market estimates for the part class described in the row; rows that give a rating instead of a model number (fuses, cable, isolator, converter, PDU and so on) are generic specifications, and any part that meets the stated ratings can be used.
>
> **What the BOM excludes:** assembly labour, shipping, taxes and import duties, and the site-specific items that depend on where the array is installed: the PV panel mounting structure (roof, ground or façade), conduit, trunking and cable trays, and PV cable beyond the lengths allowed for in section 2. The optional communications peripherals described in `ARCHITECTURE.md` (LoRa radio, satellite or Starlink terminal and its mast) are deployment options chosen per site and are not part of this reference BOM.

---

## 1. Compute & System Architecture

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **GPU Compute** | NVIDIA DGX Spark (128GB / 4TB) | NVIDIA GB10 Grace Blackwell Superchip (20-core Arm), 128GB unified LPDDR5x memory, 4TB NVMe M.2, 10GbE RJ-45 + ConnectX-7 (2× QSFP), 240W external power supply | 2 | $6,950 |
| **Networking** | Mikrotik CRS309-1G-8S+IN | 8x 10GbE SFP+, L2/L3 Hardware Offload, RouterOS | 1 | $270 |
| **Storage (Primary)** | Samsung 990 PRO 4TB NVMe SSD | PCIe 4.0 (7,450 MB/s Read, 6,900 MB/s Write) | 2 | $320 |
| **System Host Board** | Mini-ITX server board, AM5 or LGA1200 | Server-class Mini-ITX board for AMD EPYC 4004 (AM5) or Intel Xeon E-2300 (LGA1200), with IPMI/BMC, ECC memory support and two M.2 or U.2 NVMe positions for the primary storage. Generic specification; board price only. | 1 | $450 |
| **System Host CPU** | 8-core AMD EPYC 4004 or Intel Xeon E-2300 class CPU | 8 cores / 16 threads, 65W TDP class (up to 125W supported by the loop); the socket must match the board | 1 | $330 |
| **RAM (System Host)** | ECC Unbuffered 32GB module, matched to the board | DDR5 ECC unbuffered for EPYC 4004 (AM5) boards, DDR4 ECC unbuffered for Xeon E-2300 (LGA1200) boards; neither platform accepts registered (RDIMM) memory. Form factor (UDIMM or SO-DIMM) and speed per the board's QVL. | 2 | $160 |
| **Boot Drive** | Samsung 980 500GB NVMe | PCIe 3.0, OS boot drive for Ubuntu Server | 1 | $60 |
| **Host Chassis & PSU** | Short-depth 2U rackmount Mini-ITX chassis with power supply | 19" rackmount, depth ≤ 400mm, room for the CPU water block and two tubing pass-throughs; 230V AC Flex-ATX or SFX power supply of at least 300W (80 PLUS Gold class) with an IEC C14 inlet for the rack PDU. Generic specification. | 1 | $180 |

> Section subtotal: $16,150 (DGX Spark nodes 2 × $6,950 = $13,900; switch $270; primary storage 2 × $320 = $640; host board $450; host CPU $330; RAM 2 × $160 = $320; boot drive $60; host chassis and PSU $180; the non-compute lines add up to $2,250).
>
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
| **DC Distribution** | Victron Lynx Distributor | 48V busbar with four fused positions (pack 1, pack 2, inverter, MPPT). It accepts MEGA fuses only; the fuses are not included with it. | 1 | $90 |
| **PDU (Rack)** | 1U metered rack PDU, 230V AC, 16A, 8× IEC 60320 C13 | Generic specification: 1U 19" rack mount, single-phase 230V 50Hz, 16A input (IEC C20 inlet or fixed cord), eight C13 outlets, local display of total current (per-outlet metering is not required). Fed from the inverter's AC output through the RCBO below. | 1 | $250 |
| **Battery Fuses** | Class T fuse, 125A | One in each pack's positive lead, at the pack. DC voltage rating ≥ 125V, DC interrupting rating ≥ 20kA. | 2 | $55 |
| **Battery Fuse Holders** | Class T fuse holder with insulating cover | For the 125A Class T fuses, M8 studs, takes 35mm² lugs | 2 | $45 |
| **Lynx Branch Fuses** | MEGA fuse 125A, 58V DC rated | One per Lynx Distributor position (pack 1, pack 2, MultiPlus-II, MPPT) plus one spare; they are commonly sold in packs of five. The 32V MEGA fuses made for 12V and 24V systems must not be used. | 5 | $9 |
| **Battery & DC Bus Cable** | 35mm² flexible copper cable (class 5, e.g. H07V-K), red and black | 6m in total (3m red, 3m black) for the pack leads, the shunt link and the Lynx-to-inverter and Lynx-to-MPPT runs, all inside the enclosure | 6 m | $12 / m |
| **Cable Lugs** | 35mm² tinned-copper compression lugs, M8, with adhesive-lined heat shrink | 20 terminations (pack leads through the fuse holders, shunt, Lynx, inverter) plus 4 spare | 24 | $2 |
| **PV Combiner** | 2-string PV combiner box, IP65 | Two string inputs, one output, DC rating ≥ 250V (600V or 1000V PV-rated boxes are the usual choice), Type 2 DC surge protection device, cable glands for 4mm² and 6mm² solar cable. No string fuses are needed for two strings (see `WIRING_DIAGRAM.md`). | 1 | $70 |
| **PV Isolator** | 2-pole DC switch-disconnector or DC miniature circuit breaker | Rated ≥ 250V DC and ≥ 35A; a 40A device rated 500–1000V DC for PV use is the usual choice. Fitted between the combiner and the MPPT, in or next to the combiner box. | 1 | $40 |
| **PV String Cable** | 4mm² solar cable, EN 50618 (H1Z2Z2-K), red and black | Allowance of 50m (25m per polarity) for the two string runs from the array to the combiner | 50 m | $1 / m |
| **PV Main Cable** | 6mm² solar cable, EN 50618 (H1Z2Z2-K), red and black | Allowance of 10m (5m per polarity) from the combiner through the isolator to the MPPT | 10 m | $1.50 / m |
| **PV Connectors** | MC4-type connector pairs, 1000V DC or higher, for 4mm² cable | 4 pairs fitted (both ends of the two string runs) plus 2 spare pairs. Use the same connector make as the panel leads. | 6 | $3 |
| **Earthing Kit** | Earth rod, earth cable and bonding hardware | 2.4m copper-bonded earth rod with clamp, 10m of 16mm² green/yellow earth cable, an earth bar and 16mm² lugs for bonding the enclosure, the PV frames and the surge protection device | 1 | $80 |
| **12V Supply** | 48V-to-12V DC-DC converter, 20A (240W) | Input range covering 40–60V (a 32–70V input is typical), 12V output, at least 20A (240W) continuous at 40°C, isolated. Feeds the coolant pump, the six fans, the switch and the telemetry bridge (sizing below). | 1 | $95 |
| **12V Distribution** | Converter input fuse, 12V fuse block, fan splitters and switch lead | Inline fuse holder with a 10A fuse rated ≥ 80V DC (10 × 38mm DC type) on the converter's 48V input, tapped from the Lynx busbar with 2.5mm² wire; a 4-way 12V blade-fuse block; two 4-pin PWM fan splitters that take fan power from the 12V rail and the PWM signal from a host fan header; a 5.5 × 2.0mm barrel-plug lead for the switch's DC jack | 1 | $30 |
| **Telemetry Bridge** | ESP32 development board with VE.Direct interface and temperature probes | Hardware for the firmware in `firmware/`: one ESP32 development board, an isolated VE.Direct-to-UART interface for the SmartShunt, two waterproof DS18B20 probes with a 4.7kΩ pull-up resistor, and a 12V-to-5V buck module to power the board from the 12V rail | 1 | $45 |
| **AC Output Protection** | 2-pole RCBO, 16A, 30mA, Type A, in a small DIN enclosure | Residual-current and overcurrent protection on the MultiPlus-II AC output ahead of the rack PDU, with 1.5m of 3 × 2.5mm² flexible cable | 1 | $50 |

> Section subtotal: $6,868 (batteries 2 × $1,200 = $2,400; MPPT $600; MultiPlus-II $1,100; panels 8 × $180 = $1,440; SmartShunt $130; Lynx Distributor $90; PDU $250; Class T fuses 2 × $55 = $110; fuse holders 2 × $45 = $90; MEGA fuses 5 × $9 = $45; 35mm² cable 6 × $12 = $72; lugs 24 × $2 = $48; combiner $70; PV isolator $40; 4mm² cable 50 × $1 = $50; 6mm² cable 10 × $1.50 = $15; MC4 pairs 6 × $3 = $18; earthing kit $80; 12V converter $95; 12V distribution $30; telemetry bridge $45; RCBO $50).

> **Fuse selection:** Victron's Lynx Distributor documentation states that the unit is designed to fit MEGA fuses, and that 48V systems must use 58V-rated MEGA fuses (36V-rated ones are for 12V and 24V systems) ([Lynx Distributor manual, system design](https://www.victronenergy.com/media/pg/Lynx_Distributor/en/system-design.html), checked on 2026-11-14). The four Lynx positions therefore carry 125A/58V MEGA fuses, which protect the 35mm² branch conductors. A Class T fuse is added at each pack because a LiFePO4 bank can drive several kA into a short circuit, which Class T fuses are rated to interrupt (20kA DC), while Victron publishes no interrupting rating for its 58V MEGA fuse ([Victron fuse datasheet](https://www.victronenergy.com/upload/documents/Datasheet-Midi,-Mega-and-ANL-fuses,-and-fuse-holders-EN-.pdf)). Keep the charge voltage at or below 58V (typical 16-cell LiFePO4 packs absorb at 56.0–57.6V).
>
> **12V load and converter sizing:** the 12V rail feeds the coolant pump, the six 120mm fans, the MikroTik switch and the ESP32 telemetry bridge, so that the switch and the bridge stay powered when the inverter is off (load-shedding level 3 in `ARCHITECTURE.md`).
>
> | 12V load | Power | Basis |
> | :--- | ---: | :--- |
> | Coolant pump | 23W | The higher of the figures published for the Alphacool VPP755 (the launch specification gives 14W, some retailers list 23W) |
> | Six fans, 6 × 3.6W | 21.6W | Noctua lists 12V, 0.3A, 3.6W maximum for the NF-F12 industrialPPC-3000 PWM |
> | MikroTik CRS309-1G-8S+IN | 23W | Maximum power consumption on [MikroTik's product page](https://mikrotik.com/product/crs309_1g_8s_in), which also gives the DC jack input range as 12–57V |
> | ESP32 telemetry bridge | 1.5W | Estimate for an ESP32 board with Wi-Fi active, through the 5V buck module |
> | **Total** | **69.1W** | About 5.7A at 12.2V |
>
> The converter class specified is that of Victron's Orion-Tr 48/12-20 ([datasheet](https://www.victronenergy.com/upload/documents/Datasheet-Orion-Tr-DC-DC-converters-isolated-100-250-400W-EN.pdf), checked on 2026-11-14: 32–70V input, 12.2V nominal output, 20A and 240W continuous at 40°C, 25A for 10 seconds). At 69.1W it runs at 29% of its 40°C rating, a margin of 3.5×, which also covers motor start-up current. The smaller 9A model of the same series (110W at 25°C, 85W at 40°C) would run at 81% of its 40°C rating and is not used. At full output the converter draws about 5.7A from the 48V bus (240W at 87% efficiency), so a 10A input fuse on 2.5mm² wire protects its supply.

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

> **Site-specific, excluded:** the PV panel mounting structure, conduit and cable trays, and any PV cable beyond the 50m (4mm²) and 10m (6mm²) allowances above depend on the site and are not part of this BOM. Increase the cable cross-section if the run length gives more than about 3% voltage drop.

---

## 3. Cooling Subsystem (Host Liquid Loop and Enclosure Exhaust)

> The liquid loop cools the system host (the custom Mini-ITX server) only. Its heat load is the host CPU, on the order of 65–125W depending on the CPU fitted (check its datasheet), so the loop is a single CPU water block, one pump with a combined pump top and reservoir (called the pump-reservoir below) and one 360mm radiator with three fans, which still leaves a large margin and allows low fan speeds. The two DGX Spark units are sealed, air-cooled appliances: they are not opened, carry no water block and are not part of the loop; they need unobstructed intake and exhaust airflow inside the enclosure (side vent slots).
>
> **Rear panel:** the enclosure's rear panel has two 360mm × 120mm openings. One carries the radiator with its three fans. The other carries the remaining three 120mm fans as plain exhaust fans, which pull air in through the side vent slots and across the air-cooled DGX Spark units, the inverter and the power electronics. The fan total stays at six.
>
> **Loop fittings:** six G1/4 compression fittings, two per component (water block in/out, pump-reservoir in/out, radiator in/out). The assembly manual uses no quick-disconnect couplings, so none are listed.
>
> **12V power:** the pump and all six fans run from the 12V rail provided by the 48V-to-12V DC-DC converter in section 2; the rack PDU is 230V AC and powers neither.

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Cooling Radiator** | EK-Quantum Surface P360M | 360mm Triple Fan, Copper Core, 30mm Thick; fills one of the two rear openings | 1 | $140 |
| **CPU Water Block** | CPU water block for the chosen socket (AM5 or LGA1200) | Generic copper cold-plate block with G1/4 ports and the mounting kit for the host board's socket (AM5 for EPYC 4004, LGA1200 for Xeon E-2300). No model is selected; the price is a mid-market estimate, not a quote. | 1 | $90 |
| **Coolant Pump** | Alphacool Eispumpe VPP755 (pump only) | D5-format pump, 12V DC, PWM control, 350 L/h maximum flow (manufacturer's catalogue figure). Supplied without a pump top, so it needs the pump top below. | 1 | $90 |
| **Pump Top & Reservoir** | D5 pump top with integrated tube reservoir | Fits D5-format pumps, reservoir of about 150mm, G1/4 inlet and outlet ports and a G1/4 fill port (used for the leak test) | 1 | $60 |
| **High-PPC Fans** | Noctua NF-F12 industrialPPC-3000 PWM | 120mm, 12V, 4-pin PWM, 3,000 RPM, 186.7 m³/h, 43.5 dB(A), 0.3A / 3.6W maximum ([Noctua specification](https://noctua.at/en/nf-f12-industrialppc-3000-pwm/specification)); 3 on the radiator, 3 as exhaust fans in the second rear opening. This is the 12V model, not the 24V industrialPPC variant. | 6 | $32 |
| **Coolant** | EK-CryoFuel Clear Concentrate (1:9) | Non-conductive, anti-corrosive glycol, UV stable. 1 L of mixed coolant fills the single-radiator loop and leaves a top-up reserve (measure the fill volume when priming). | 1 L | $30 / L |
| **Compression Fittings** | EK-Quantum Torque STC-10/16 | G1/4 thread compression fittings for 10/16mm soft tubing; 2 each for the water block, the pump-reservoir and the radiator | 6 | $8 |
| **Tubing** | EK-Tube ZMT 16mm OD Matte Black | Soft tubing 10/16mm, UV-stable rubber, 3m length (three runs: pump to block, block to radiator, radiator to reservoir) | 1 | $25 |
| **Exhaust Fan Bracket** | 3 × 120mm fan mounting plate, 2mm aluminium | Carries the three exhaust fans in the second 360mm × 120mm rear opening (120mm fan hole pattern, 105mm hole spacing); can be cut with the enclosure panels | 1 | $25 |
| **Fan Grilles** | 120mm wire finger guards | One per fan, on the outside face of both rear openings | 6 | $3 |

> Section subtotal: $718 (radiator $140; water block $90; pump $90; pump top and reservoir $60; fans 6 × $32 = $192; coolant $30; fittings 6 × $8 = $48; tubing $25; fan bracket $25; grilles 6 × $3 = $18).

---

## 4. Networking

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **SFP+ DAC Cables** | MikroTik S+DA0001 | 1m SFP+ Direct Attach Cable, 10GbE | 4 | $15 |
| **SFP+ to RJ45** | MikroTik S+RJ10 | 10GbE SFP+ to RJ45 Module | 2 | $45 |
| **Management Cable** | Cat6A Shielded Patch (0.3m) | Short run management links | 4 | $5 |

> Section subtotal: $170 (DAC cables 4 × $15 = $60; SFP+ to RJ45 modules 2 × $45 = $90; patch cables 4 × $5 = $20).

---

## 5. Enclosure & Mounting Hardware

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Rack Enclosure** | Custom 9U Aluminum, 540mm W × 550mm D × 404mm H | Per `cad/rack_enclosure.scad`, 2mm 6061-T6, 400.05mm (9U) clear interior height | 1 | $450 |
| **Rack Rails** | 19" M6 Tapped Steel Rails, 9U | EIA-310-D hole pattern, mounting holes 465.1mm centre-to-centre (left to right), 2mm cold-rolled steel; 2 front + 2 rear | 4 | $30 |
| **Cable Management** | 1U Horizontal Cable Manager | D-ring brackets, brush insert, 1U | 2 | $20 |
| **M6 Cage Nut Kit** | Startech CABSCREWM62 | M6 cage nuts + screws, 50-pack | 1 | $12 |
| **Rack Shelf** | 1U vented rack shelf, 19" | Carries the two DGX Spark units side by side with their vents unobstructed; load rating ≥ 10kg | 1 | $35 |

> Section subtotal: $657 (enclosure $450; rails 4 × $30 = $120; cable managers 2 × $20 = $40; cage nut kit $12; rack shelf $35).

---

## 6. BOM Total

| Section | Subtotal |
| :--- | ---: |
| 1. Compute & system architecture | $16,150 |
| 2. Power, solar, protection, cabling and telemetry | $6,868 |
| 3. Cooling (host liquid loop and enclosure fans) | $718 |
| 4. Networking | $170 |
| 5. Enclosure & mounting hardware | $657 |
| **Total** | **$24,563** |

16,150 + 6,868 + 718 + 170 + 657 = 24,563. Of this, the two DGX Spark nodes are $13,900 and everything else is $10,663. The sizing calculator on the project page (`docs/js/calculator.js`) uses the same unit prices and returns $24,563 at its default settings.
