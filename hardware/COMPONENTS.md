# Bill of Materials (BOM) & Component Specifications

> **Total Estimated Build Cost:** $24,701 USD, the sum of quantity × estimated unit cost over every row below: $13,900 for the two DGX Spark compute nodes plus $10,801 for everything else (host, storage and switch $2,250; power, solar, protection, cabling and telemetry $7,006; host liquid loop and enclosure fans $718; network cabling $170; enclosure and mounting $657).
>
> **How to read the prices:** every figure in an "Est. Unit Cost" column is an estimate, not a quote. The DGX Spark price is NVIDIA's own listed price (source and check date under the table in section 1). All other figures are conservative mid-market estimates for the part class described in the row; rows that give a rating instead of a model number (fuses, cable, isolators, converter, PDU and so on) are generic specifications, and any part that meets the stated ratings can be used.
>
> **What the BOM excludes:** assembly labour, shipping, taxes and import duties, the upstream site router/firewall, and the site-specific items that depend on where the array is installed: the PV panel mounting structure (roof, ground or façade), conduit, trunking and cable trays, and PV cable beyond the lengths allowed for in section 2. The optional communications peripherals described in `ARCHITECTURE.md` (LoRa radio, satellite or Starlink terminal and its mast) are deployment options chosen per site and are not part of this reference BOM.

---

## 1. Compute & System Architecture

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **GPU Compute** | NVIDIA DGX Spark (128GB / 4TB) | NVIDIA GB10 Grace Blackwell Superchip (20-core Arm), 128GB unified LPDDR5x memory, 4TB NVMe M.2, 10GbE RJ-45 + ConnectX-7 (2× QSFP), 240W external power supply | 2 | $6,950 |
| **Networking** | Mikrotik CRS309-1G-8S+IN | 8x 10GbE SFP+, L2/L3 Hardware Offload, RouterOS | 1 | $270 |
| **Storage (Primary)** | Samsung 990 PRO 4TB NVMe SSD | PCIe 4.0 (7,450 MB/s Read, 6,900 MB/s Write) | 2 | $320 |
| **System Host Board** | Mini-ITX server board (+ 10GbE SFP+), AM5 or LGA1200 | Server-class Mini-ITX board for AMD EPYC 4004 (AM5) or Intel Xeon E-2300 (LGA1200), with IPMI/BMC, ECC memory support, two M.2 or U.2 NVMe positions for the primary storage, and at least one 10GbE SFP+ cage (onboard or via a low-profile PCIe x8 10GbE SFP+ NIC in the free PCIe slot, included in the $450 allowance). Generic specification. | 1 | $450 |
| **System Host CPU** | 8-core AMD EPYC 4004 or Intel Xeon E-2300 class CPU | 8 cores / 16 threads, 65W TDP class (up to 125W supported by the loop); the socket must match the board | 1 | $330 |
| **RAM (System Host)** | ECC Unbuffered 32GB module, matched to the board | DDR5 ECC unbuffered for EPYC 4004 (AM5) boards, DDR4 ECC unbuffered for Xeon E-2300 (LGA1200) boards; neither platform accepts registered (RDIMM) memory. Form factor (UDIMM or SO-DIMM) and speed per the board's QVL. | 2 | $160 |
| **Boot Drive** | Samsung 980 500GB NVMe | PCIe 3.0, OS boot drive for Ubuntu Server | 1 | $60 |
| **Host Chassis & PSU** | Short-depth 2U rackmount Mini-ITX chassis with power supply | 19" rackmount, depth ≤ 400mm, room for the CPU water block and two tubing pass-throughs; 230V AC Flex-ATX or SFX power supply of at least 300W (80 PLUS Gold class) with an IEC C14 inlet for the rack PDU. Generic specification. | 1 | $180 |

> Section subtotal: $16,150 (DGX Spark nodes 2 × $6,950 = $13,900; switch $270; primary storage 2 × $320 = $640; host board $450; host CPU $330; RAM 2 × $160 = $320; boot drive $60; host chassis and PSU $180; the non-compute lines add up to $2,250).
>
> DGX Spark price is the $6,950.00 that NVIDIA lists for the 128GB / 4TB DGX Spark on its own marketplace page ([NVIDIA Marketplace](https://marketplace.nvidia.com/en-us/enterprise/personal-ai-supercomputers/dgx-spark/), checked on 2026-11-14); check current pricing before ordering. Specifications are from NVIDIA's [DGX Spark hardware overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html). A lower-power alternative is the NVIDIA Jetson AGX Orin 64GB; it is not the reference build and is not priced here.

---

## 2. Power & Solar Energy Subsystem

> Total DC-coupled solar capacity: 3,280W peak (8 × 410W), about 14.5 kWh/day on annual average (5.2 peak sun hours, 85% system efficiency). The reference duty cycle (both DGX Spark nodes at their 240W supply rating for 12 h/day, 45W idle estimate otherwise, 180W base load) draws about 11.2 kWh/day, a 465W average, so the 8-panel array covers it with a surplus of about 3.3 kWh/day (yield is about 130% of the draw). This is an annual-average balance: winter yield is lower (see `BENCHMARKS.md`), and the shortfall on low-sun days comes from the battery; a grid or generator supply on the MultiPlus-II AC input can be added for sites that need it (it is not part of the reference build, see the AC connections note below). Zero-sun autonomy at that load: ~18.7 hours from the 8.70 kWh usable at 85% depth of discharge.

| Subsystem | Component Model | Spec / Description | Qty | Est. Unit Cost |
| :--- | :--- | :--- | :---: | :---: |
| **Battery Storage** | Server Rack LiFePO4 48V 100Ah | 5.12 kWh per unit, 10.24 kWh total, 6,000+ cycles, integrated cell-level BMS in each pack. Buy packs that have their own DC circuit breaker on the front panel (see the battery isolation note below); no pack model is named here, so the installer must check this on the datasheet. | 2 | $1,200 |
| **Solar Charge Controller** | Victron SmartSolar MPPT 250/70-Tr | 250V absolute max PV open-circuit voltage (245V start-up and operating max), 35A max PV short-circuit current, 70A rated charge current, 4,000W nominal PV power at 48V, Bluetooth and VE.Direct ([Victron datasheet](https://www.victronenergy.com/upload/documents/Datasheet-SmartSolar-charge-controller-MPPT-250-60-and-250-70-EN.pdf)) | 1 | $600 |
| **Hybrid Inverter/Charger** | Victron MultiPlus-II 48/3000/35-32 | 3,000VA Continuous, Pure Sine, Grid-Tie Ready | 1 | $1,100 |
| **Solar PV Array** | Monocrystalline 410W PERC Panels | 4S2P config: two strings of four panels, 1,640W per string, 3,280W total. Panel limits for this design: Voc ≤ 50V and Isc ≤ 14A at STC, 1000V or 1500V DC system rating (see the PV sizing note below) | 8 | $180 |
| **Battery Monitor** | Victron SmartShunt 500A/50mV | Shunt-based battery monitor in the common battery negative: voltage, current and state of charge over VE.Direct and Bluetooth. Cell protection is done by the BMS built into each pack. | 1 | $130 |
| **DC Distribution** | Victron Lynx Distributor (M8 model) | 48V busbar, 1000A, with four fused positions (pack 1, pack 2, inverter, MPPT). It accepts MEGA fuses only; the fuses are not included with it. | 1 | $90 |
| **PDU (Rack)** | 1U metered rack PDU, 230V AC, 16A, 8× IEC 60320 C13 | Generic specification: 1U 19" rack mount, single-phase 230V 50Hz, 16A input (IEC C20 inlet or fixed cord), eight C13 outlets, local display of total current (per-outlet metering is not required). Fed from the inverter's AC output through the RCBO below. | 1 | $250 |
| **Battery Fuses** | Class T fuse, 125A | One in each pack's positive lead, at the pack. DC voltage rating ≥ 125V, DC interrupting rating ≥ 20kA. | 2 | $55 |
| **Battery Fuse Holders** | Class T fuse holder with insulating cover | For the 125A Class T fuses, M8 studs, takes 35mm² lugs | 2 | $45 |
| **Battery Isolators** | DC switch-disconnector (load-break), IEC 60947-3, 160A frame, lockable in OFF | One in each pack's positive lead, between the Class T fuse and the Lynx Distributor (the fuse stays next to the pack terminal). Generic specification: rated operational voltage ≥ 60V DC, rated operational current ≥ 125A at utilisation category DC-21B or higher at that voltage (a 160A device is the usual size), able to make and break load current, handle that can be padlocked in the OFF position (or a removable key), terminals for 35mm² lugs, touch-safe terminal covers. Estimated price; no model is selected. See the battery isolation note below. | 2 | $65 |
| **Lynx Branch Fuses (packs, inverter)** | MEGA fuse 125A, 58V DC rated | One each for Lynx Distributor positions 1 to 3 (pack 1, pack 2, MultiPlus-II) plus one spare. The 32V MEGA fuses made for 12V and 24V systems must not be used. | 4 | $9 |
| **Lynx Branch Fuse (MPPT)** | MEGA fuse 100A, 80V DC rated | For Lynx Distributor position 4 (MPPT 250/70). Victron prescribes a battery fuse of 80A minimum and 100A maximum for this controller, so the 125A fuse must not be used here (see the fuse selection note below). No spare is priced; add one if the site is remote. | 1 | $9 |
| **Battery & DC Bus Cable** | 35mm² flexible copper cable (class 5, e.g. H07V-K), red and black | 6m in total (3m red, 3m black) for the pack leads (each positive lead is cut for the fuse holder and the isolator), the shunt link and the Lynx-to-inverter and Lynx-to-MPPT runs, all inside the enclosure | 6 m | $12 / m |
| **Cable Lugs** | 35mm² tinned-copper compression lugs with adhesive-lined heat shrink | 24 terminations (pack leads through the fuse holders and the battery isolators, shunt, Lynx, inverter; the MPPT end has screw terminals and needs no lug) plus 4 spare. M8 holes for the Lynx Distributor (M8 model), the Class T fuse holders and the MultiPlus-II; M10 holes for the three terminations on the SmartShunt 500A, whose bolts are M10; for the pack terminals and the four isolator terminals use the stud size in the pack and isolator datasheets. | 28 | $2 |
| **PV Combiner** | 2-string PV combiner box, IP65 | Two string inputs, one output, DC rating ≥ 250V (600V or 1000V PV-rated boxes are the usual choice), Type 2 DC surge protection device, cable glands for 4mm² and 6mm² solar cable. No string fuses are needed for two strings (see `WIRING_DIAGRAM.md`). | 1 | $70 |
| **PV Isolator** | 2-pole DC switch-disconnector or DC miniature circuit breaker | Rated ≥ 250V DC and ≥ 35A, switching both PV conductors; a 40A device rated 500–1000V DC for PV use is the usual choice. If a DC miniature circuit breaker is used it must be a non-polarised type, or be wired exactly as its polarity marking shows. Fitted between the combiner and the MPPT, in or next to the combiner box. | 1 | $40 |
| **PV String Cable** | 4mm² solar cable, EN 50618 (H1Z2Z2-K), red and black | Allowance of 50m (25m per polarity) for the two string runs from the array to the combiner | 50 m | $1 / m |
| **PV Main Cable** | 6mm² solar cable, EN 50618 (H1Z2Z2-K), red and black | Allowance of 10m (5m per polarity) from the combiner through the isolator to the MPPT | 10 m | $1.50 / m |
| **PV Connectors** | MC4-type connector pairs, 1000V DC or higher, for 4mm² cable | 4 pairs fitted (both ends of the two string runs) plus 2 spare pairs. Use the same connector make as the panel leads. | 6 | $3 |
| **Earthing Kit** | Earth rod, earth cable and bonding hardware | 2.4m copper-bonded earth rod with clamp, 10m of 16mm² green/yellow earth cable, an earth bar and 16mm² lugs for bonding the enclosure, the PV frames and the surge protection device | 1 | $80 |
| **12V Supply** | 48V-to-12V DC-DC converter, 20A (240W) | Input range covering 40–60V (a 32–70V input is typical), 12V output, at least 20A (240W) continuous at 40°C, isolated. Feeds the coolant pump, the six fans, the switch and the telemetry bridge (sizing below). | 1 | $95 |
| **12V Distribution** | Converter input fuse, 12V fuse block, fan splitters and switch lead | Inline fuse holder with a 10A fuse rated ≥ 80V DC (10 × 38mm DC type) on the converter's 48V input, tapped from the Lynx busbar with 2.5mm² wire; a 4-way 12V blade-fuse block; two 4-pin PWM fan splitters that take fan power from the 12V rail and the PWM signal from a host fan header; a 5.5 × 2.0mm barrel-plug lead for the switch's DC jack | 1 | $30 |
| **Telemetry Bridge** | ESP32 development board with VE.Direct interface and temperature probes | Hardware for the firmware in `firmware/`: one ESP32 development board, an isolated VE.Direct-to-UART interface for the SmartShunt, two waterproof DS18B20 probes with a 4.7kΩ pull-up resistor, and a 12V-to-5V buck module to power the board from the 12V rail | 1 | $45 |
| **AC Output Protection** | 2-pole RCBO, 16A, 30mA, Type A, in a small DIN enclosure | Residual-current and overcurrent protection on the MultiPlus-II AC output (AC-out-1) ahead of the rack PDU, with 1.5m of 3 × 2.5mm² flexible cable. It is also the AC disconnect of the system. The AC input of the MultiPlus-II is not used in this reference build and nothing is priced for it (see the AC connections note below). | 1 | $50 |

> Section subtotal: $7,006 (batteries 2 × $1,200 = $2,400; MPPT $600; MultiPlus-II $1,100; panels 8 × $180 = $1,440; SmartShunt $130; Lynx Distributor $90; PDU $250; Class T fuses 2 × $55 = $110; fuse holders 2 × $45 = $90; battery isolators 2 × $65 = $130; MEGA fuses 125A 4 × $9 = $36 and 100A 1 × $9 = $9; 35mm² cable 6 × $12 = $72; lugs 28 × $2 = $56; combiner $70; PV isolator $40; 4mm² cable 50 × $1 = $50; 6mm² cable 10 × $1.50 = $15; MC4 pairs 6 × $3 = $18; earthing kit $80; 12V converter $95; 12V distribution $30; telemetry bridge $45; RCBO $50).

> **Battery isolation (DC disconnects):** Victron's installation manual for the MultiPlus-II requires every installation to have a way to disconnect both the AC and the DC circuits. It accepts circuit breakers used for overcurrent protection as disconnects, and where fuses are used it says that "separate disconnect switches are required between the source and the fuses" ([MultiPlus-II manual, installation, section 4.1](https://www.victronenergy.com/media/pg/MultiPlus-II_230V/en/installation.html), checked on 2026-11-14). The battery feeds of this design are protected by fuses (Class T at each pack, MEGA in the Lynx Distributor), which are not disconnects, so the BOM adds one load-break switch-disconnector per pack. That switch is between the packs and the MEGA fuses, so it satisfies the quoted sentence for the MEGA fuses. It does not satisfy it for the Class T fuse, which is upstream of the switch and stays live when the switch is open; for that fuse the disconnect is the pack's own breaker, which the installer must confirm (last point below).
>
> - **One per pack, not one main switch.** Each pack lands on its own fused position of the Lynx Distributor, so there is no single conductor that carries the whole bank and could take one main switch. A switch per pack also lets one pack be isolated for a fault or for service while the other keeps the bus alive. Opening both switches (with the PV isolator open as well) removes every source from the DC bus and from the MEGA fuses in the Lynx.
> - **Position.** Pack positive terminal, then the Class T fuse, then the isolator, then the Lynx position. The fuse stays next to the pack so that the unfused length of cable is as short as possible; the isolator is the first device after it. Putting the isolator ahead of the Class T fuse instead would leave the isolator and the cable to it unprotected against a short circuit, so this design keeps the fuse first and relies on the pack's own breaker to de-energise it.
> - **Rating.** Continuous current not below the 125A fuse it sits behind, at a DC voltage of at least 60V (the bus reaches about 58V on charge), and rated to make and break that current (IEC 60947-3 utilisation category DC-21B or higher). A switch that is only an off-load isolator does not meet this row. Victron's own Battery Switch ON/OFF 275A is an example of that second kind: its datasheet gives 275A continuous and a 60V maximum operating voltage ([datasheet](https://www.victronenergy.com/upload/documents/Datasheet-Battery-Switch-275-A-EN.pdf)), but its manual states that the switch is not rated to make or break current and asks for all loads and charge sources to be switched off before it is operated ([manual, Rev 04](https://www.victronenergy.com/upload/documents/Battery_Switch_ON-Off_275A/103859-Battery_Switch_-_manual-pdf-en.pdf), checked on 2026-11-14). The $65 unit price is an estimate for a generic 160A DC switch-disconnector, not a quote.
> - **Single pole or two poles.** The isolator switches the positive lead. Where the local wiring rules require both battery conductors to be switched (often the case when the battery is left unearthed), use a 2-pole device and take that pack's negative lead through the second pole, on the battery side of the SmartShunt.
> - **The pack's own breaker (installer check).** 48V server-rack LiFePO4 packs usually carry a DC circuit breaker on the front panel, often together with a BMS power button. The BOM names no pack model, so check the datasheet. A front-panel breaker sits between the cells and the Class T fuse, which is the position the Victron sentence above asks for with respect to that fuse, and it is what makes the Class T fuse safe to change. With a pack that has no such breaker, this design does not meet the quoted sentence for the Class T fuse. It does not replace the Class T fuse (pack breakers have a much lower interrupting rating) or the external isolator (it cannot be locked off, and it is inside the pack being serviced). A BMS power button alone is an electronic switch, not an isolator. If a pack has no breaker, its Class T fuse may only be changed after the pack's negative lead has been removed at the pack, with insulated tools.
>
> **AC connections of the MultiPlus-II:**
>
> - **AC output (used).** The manual requires an earth leakage circuit breaker and a fuse or circuit breaker, rated for the expected load, in series with the output, and states that Type A residual-current devices can be used (section 4.4 of the manual linked above). The 2-pole 16A / 30mA Type A RCBO in the BOM is that device, and, being a circuit breaker, it is also the AC disconnect. 16A is above the inverter's 13A continuous output (3,000VA at 230V) and is within the rating of the 2.5mm² cable and of the 16A PDU.
> - **AC input (not used in the reference build).** The reference build is off-grid: nothing is connected to the AC-in terminals, their terminal cover stays fitted, and the BOM has no AC-input parts. The manual does not describe an unused AC input; leaving it unconnected and covered is this design's choice. The chassis earth connection is still required, because the manual requires the casing to be grounded when the earth conductor of an AC input does not provide the permanent earth connection.
> - **If a grid or generator supply is added later** (the battery charger and the transfer switch then come into use), the manual requires the AC input of the 3 kVA model to be protected by a fuse or magnetic circuit breaker rated at 32A or less, with the cable sized accordingly, and a smaller breaker if the supply is rated lower. That means, on the supply side of AC-in: a 2-pole breaker of at most 32A (which is also the AC-in disconnect), residual-current protection as the local rules require for the supply circuit, cable matched to the breaker (6mm² for 32A is usual; 2.5mm² for a 16A supply), and the AC input current limit of the MultiPlus-II set to what the source can deliver. These parts depend on the supply and are not in this BOM. The 16A RCBO on the output stays as it is: it limits the output circuit to 16A even though the manual notes that the output can supply up to 45A when AC-in is present.

> **Fuse selection:** Victron's Lynx Distributor documentation states that the unit is designed to fit MEGA fuses, and that 48V systems must use 58V-rated MEGA fuses (36V-rated ones are for 12V and 24V systems) ([Lynx Distributor manual, system design](https://www.victronenergy.com/media/pg/Lynx_Distributor/en/system-design.html), checked on 2026-11-14). Positions 1 to 3 (pack 1, pack 2, MultiPlus-II) carry 125A/58V MEGA fuses, which protect the 35mm² branch conductors and match the 125A DC fuse and 35mm² cable (runs of 0–5m) that Victron recommends for the MultiPlus-II 48/3000/35 ([MultiPlus-II manual, installation](https://www.victronenergy.com/media/pg/MultiPlus-II_230V/en/installation.html)). Position 4 (MPPT 250/70) carries a 100A MEGA fuse: Victron's installation manual for the controller prescribes a battery fuse of 80A minimum and 100A maximum for the 150/70 and 250/70 models ([SmartSolar MPPT manual, installation](https://www.victronenergy.com/media/pg/Manual_SmartSolar_MPPT_150-60_up_to_250-70/en/installation.html), checked on 2026-11-14), and that limit applies whatever the cable size. Victron's 58V MEGA range starts at 125A, so the 100A fuse is taken from its 80V MEGA range, which has the same format and a higher voltage rating than the 58V the Lynx manual asks for. The four ratings add up to 475A, within the 1000A rating of the Lynx Distributor. `WIRING_DIAGRAM.md` has the table for all four positions. A Class T fuse is added at each pack because a LiFePO4 bank can drive several kA into a short circuit, which Class T fuses are rated to interrupt (20kA DC), while Victron publishes no interrupting rating for its 58V MEGA fuse ([Victron fuse datasheet](https://www.victronenergy.com/upload/documents/Datasheet-Midi,-Mega-and-ANL-fuses,-and-fuse-holders-EN-.pdf)). Keep the charge voltage at or below 58V (typical 16-cell LiFePO4 packs absorb at 56.0–57.6V).
>
> **12V load and converter sizing:** the 12V rail feeds the coolant pump, the six 120mm fans, the MikroTik switch and the ESP32 telemetry bridge, so that the switch and the bridge stay powered when the inverter is off. No code in this repository switches the inverter off: suspending the compute nodes while the 12V rail stays up is one of the hardware actions listed as "Design targets, not implemented" under the load-shedding levels in `ARCHITECTURE.md`, and the rail is laid out so that such a state remains possible.
>
> | 12V load | Power | Basis |
> | :--- | ---: | :--- |
> | Coolant pump | 23W | The higher of the figures published for the Alphacool VPP755 (the launch specification gives 14W, some retailers list 23W) |
> | Six fans, 6 × 3.6W | 21.6W | Noctua lists 12V, 0.3A, 3.6W maximum for the NF-F12 industrialPPC-3000 PWM |
> | MikroTik CRS309-1G-8S+IN | 23W | Maximum power consumption on [MikroTik's product page](https://mikrotik.com/product/crs309_1g_8s_in), which also gives the DC jack input range as 12–57V |
> | ESP32 telemetry bridge | 1.5W | Estimate for an ESP32 board with Wi-Fi active, through the 5V buck module |
> | **Total** | **69.1W** | About 5.7A at 12.2V |
>
> The converter class specified is that of Victron's Orion-Tr 48/12-20 ([datasheet](https://www.victronenergy.com/upload/documents/Datasheet-Orion-Tr-DC-DC-converters-isolated-100-250-400W-EN.pdf), checked on 2026-11-14: 32–70V input, 12.2V nominal output, 20A and 240W continuous at 40°C, 25A for 10 seconds). At 69.1W it runs at 29% of its 40°C rating, a margin of 3.5×, which also covers motor start-up current. The smaller 9A model of the same series (110W at 25°C, 85W at 40°C) would run at 81% of its 40°C rating and is not used. At full output the converter draws about 5.7A from the 48V bus (240W at 87% efficiency), so a 10A input fuse on 2.5mm² wire protects its supply. Victron's manual for the isolated Orion-Tr states that the 48V-input models have no internal input fuse and require an external one, without giving a rating ([manual](https://www.victronenergy.com/upload/documents/Manual-Orion-Tr-isolated-DC-DC-converters-EN-FR-NL-ES-IT-DE.pdf)); the 10A value is derived here from the input current.

> **PV sizing (4S2P on the MPPT 250/70):** the BOM names no panel model, so the limits are stated instead. Both common 410W formats fit:
>
> | Panel format (typical figures, check your datasheet) | String Voc at STC (4 in series) | String Voc at −10°C (−0.28%/°C) | Array Isc (2 strings) |
> | :--- | :---: | :---: | :---: |
> | 108 half-cell, Voc ≈ 38V, Isc ≈ 14A | 152V | ≈ 167V | 28A |
> | 144 half-cell, Voc ≈ 50V, Isc ≈ 11A | 200V | ≈ 220V | 22A |
>
> Controller limits (Victron datasheet linked above): 250V absolute maximum PV open-circuit voltage at the coldest conditions, 245V start-up and operating maximum, 35A maximum PV short-circuit current, 4,000W nominal PV power at 48V. The installer must confirm, with the Voc, Isc and temperature coefficient of the panels actually bought, that 4 × Voc corrected to the lowest site temperature stays below 245V and that 2 × Isc stays below 35A. The 3,280W array is within the 4,000W nominal PV power, so the controller does not clip. Do not use this array with a 150V controller: four panels in series exceed 150V, and four parallel strings of two 108-cell panels (56A) exceed the 50A PV short-circuit limit of the MPPT 150/70.
>
> **Charge current:** 3,280W into a 51.2V bank is about 64A, within the controller's 70A rating. If a grid or generator supply is connected to the MultiPlus-II 48/3000/35 (optional, see the AC connections note above), its charger adds up to 35A, so the worst case is about 64A + 35A = 99A into the 200Ah bank (two 100Ah packs in parallel), which is about 0.5C. Check that figure against the maximum charge current in the battery pack datasheet (two packs in parallel share it, about 50A each). If the packs allow less, two settings cap it: the MPPT's "max charge current" in VictronConnect, and the MultiPlus-II's charge current limit in VEConfigure or VictronConnect. Set them so that their sum does not exceed the bank's limit.

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
| **Cooling Radiator** | EK-Quantum Surface P360M | 360mm Triple Fan, Copper Core, 44mm Thick (405 × 130 × 44mm); fills one of the two rear openings | 1 | $140 |
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
| **SFP+ DAC Cables** | MikroTik S+DA0001 | 1m SFP+ Direct Attach Cable, 10GbE (host SFP+ link, upstream SFP+ router link & spares) | 4 | $15 |
| **SFP+ to RJ45** | MikroTik S+RJ10 | 10GbE SFP+ to RJ45 Module | 2 | $45 |
| **10GbE / Mgmt Patch** | Cat6A Shielded Patch (0.3m) | DGX Spark S+RJ10 10GbE & management links | 4 | $5 |

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
| 2. Power, solar, protection, cabling and telemetry | $7,006 |
| 3. Cooling (host liquid loop and enclosure fans) | $718 |
| 4. Networking | $170 |
| 5. Enclosure & mounting hardware | $657 |
| **Total** | **$24,701** |

16,150 + 7,006 + 718 + 170 + 657 = 24,701. Of this, the two DGX Spark nodes are $13,900 and everything else is $10,801. The sizing calculator on the project page (`docs/js/calculator.js`) uses the same unit prices and returns $24,701 at its default settings.
