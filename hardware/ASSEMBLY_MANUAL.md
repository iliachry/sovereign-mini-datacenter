# Sovereign Mini Datacenter — Physical Assembly & Commissioning Field Manual

> **Document Version:** 1.4.0  
> **Target Audience:** Field Engineers, Datacenter Builders, Solar Technicians  
> **Safety Notice:** ⚡ High DC Current (48V / 100A+), a PV array of up to about 230V DC open-circuit, and a Pressurized Liquid Cooling Loop are present. Read all warnings before energizing.

---

## 🛠️ Required Tools & Equipment

* **Electrical:**
  * Heavy-duty hydraulic lug crimper (16mm² to 50mm² dies).
  * Digital calibrated torque wrench (2 Nm to 25 Nm).
  * True-RMS Digital Multimeter with DC clamp (100A+ rated).
  * Heat gun with dual-wall adhesive-lined heat shrink.
* **Mechanical & Thermal:**
  * Metric hex key set (2.5mm, 3mm, 4mm, 5mm, 6mm).
  * Radiator air-pressure leak tester (EK-Loop Leak Tester or equivalent).
  * Distilled water + EK CryoFuel non-conductive clear concentrate.

---

## ⚙️ Step-by-Step Assembly Runbook

### Phase 1: 9U Aluminum Chassis Assembly
1. **Frame Squareness:** Lay the bottom 2.0mm 6061-T6 aluminum base plate on a level workbench.
2. **Corner Extrusions:** Bolt the four 2020/3030 vertical corner posts using M5×12mm button-head screws with blue Loctite 243.
3. **Rack Rails:** Fasten the four 9U steel rack rails (two front, two rear) per EIA-310-D: mounting holes 465.1mm centre-to-centre between the left and right rail, clear opening between the rails at least 450mm, for 482.6mm (19") wide front panels.
4. **Torque Specification:** Tighten all chassis frame fasteners to **4.5 N·m**.

---

### Phase 2: DC Power Wall & Battery Busbar Wiring
```
[LiFePO4 Pack 1 (5.12kWh)] ──► [Class T Fuse 125A] ──► [Battery Isolator 1] ──(35mm² Red)──► [Lynx position 1, MEGA 125A/58V] ─┐
[LiFePO4 Pack 2 (5.12kWh)] ──► [Class T Fuse 125A] ──► [Battery Isolator 2] ──(35mm² Red)──► [Lynx position 2, MEGA 125A/58V] ─┤ Lynx DC Busbar +
[Victron MultiPlus-II DC +] ◄──────────────────────────────────────────────(35mm² Red)──── [Lynx position 3, MEGA 125A/58V] ─┤
[Victron MPPT 250/70 Batt +] ◄─────────────────────────────────────────────(35mm² Red)──── [Lynx position 4, MEGA 100A/80V] ─┘
[LiFePO4 Packs 1 & 2 (-)]  ──(35mm² Blk)──► [Victron SmartShunt] ──► [Lynx DC Busbar -]
```

> **Work on the DC side is done dead.** Throughout this phase the battery isolators are open and locked off, each pack's own breaker is off, the PV isolator is open and the AC output RCBO is off. Nothing is energised until the commissioning sequence at the end of this phase.

1. **Battery Rack Mounting:** Slide LiFePO4 Battery Pack 2 into **U1–U2** and Pack 1 into **U3–U4**. Secure faceplates with M6 cage nuts.
2. **Lug Crimping:** Crimp 35mm² (2 AWG) fine-stranded copper cables with tin-plated copper eyelet lugs. Insulate with adhesive heat shrink.
3. **Torque Specification** (manufacturer figures, checked on 2026-11-14; the manual supplied with each device takes precedence):
   * Battery pack terminals: the value in the pack's datasheet (it differs between pack makes and stud sizes).
   * Victron Lynx Distributor DC connections: **14 N·m** on M8 nuts (the M10 model of the Lynx uses 33 N·m on its M10 nuts); before fitting the fuses, check that the bottom nuts of the middle row are at 10 N·m ([Lynx Distributor manual, installation](https://www.victronenergy.com/media/pg/Lynx_Distributor/en/installation.html)).
   * MultiPlus-II battery terminals (M8 nuts): **12 N·m** ([MultiPlus-II manual, installation](https://www.victronenergy.com/media/pg/MultiPlus-II_230V/en/installation.html)).
   * SmartSolar MPPT 250/70-Tr battery and PV screw terminals: **2.4 N·m** ([SmartSolar MPPT manual, installation](https://www.victronenergy.com/media/pg/Manual_SmartSolar_MPPT_150-60_up_to_250-70/en/installation.html)).
   * SmartShunt 500A bolts (M10): at most **21 N·m** ([SmartShunt manual, installation](https://www.victronenergy.com/media/pg/SmartShunt/en/installation.html)).
4. **Fuse and Isolator Installation:** Mount a Class T fuse holder with a 125A Class T fuse (DC rating ≥ 125V, interrupting rating ≥ 20kA) on each positive battery lead, at the pack. Directly after each fuse holder mount that pack's battery isolator: a load-break DC switch-disconnector rated at least 60V DC and 125A (IEC 60947-3, DC-21B or higher; a 160A device is usual) that can be padlocked in OFF. The order on each positive lead is pack terminal, Class T fuse, isolator, Lynx position. Label the two isolators "Battery 1" and "Battery 2", leave them OFF, and tighten their terminals to the torque in the isolator's datasheet. Victron's MultiPlus-II manual asks for disconnects where fuses are used: "separate disconnect switches are required between the source and the fuses" ([MultiPlus-II manual, installation, section 4.1](https://www.victronenergy.com/media/pg/MultiPlus-II_230V/en/installation.html)). The isolator sits between the pack and the MEGA fuses in the Lynx, so it meets that sentence for the MEGA fuses only. It is downstream of the Class T fuse and cannot de-energise it: the disconnect ahead of the Class T fuse is the pack's own front-panel breaker. Check that the packs you bought have one; if they do not, the Class T fuse has no disconnect ahead of it and may only be touched after the pack's negative lead has been removed at the pack (see the battery isolation note in `COMPONENTS.md`). An off-load battery switch (one whose manual says it is not rated for making or breaking current) must not be used here. Fit a 125A MEGA fuse rated 58V DC in Lynx Distributor positions 1 to 3 (pack 1, pack 2, MultiPlus-II) and keep the fourth 125A fuse as a spare. Fit the **100A** MEGA fuse rated 80V DC in position 4 (MPPT): Victron's manual for the MPPT 250/70 prescribes a battery fuse of 80A minimum and 100A maximum, so a 125A fuse must not be used there ([SmartSolar MPPT manual, installation](https://www.victronenergy.com/media/pg/Manual_SmartSolar_MPPT_150-60_up_to_250-70/en/installation.html)). The Lynx Distributor takes MEGA fuses only; do not fit the 32V type. `WIRING_DIAGRAM.md` has the fuse table for all four positions.
5. **12V Auxiliary Rail:** Tap the Lynx positive busbar through the inline 10A fuse (≥ 80V DC), mounted within about 150mm of the busbar, to the 48V-to-12V DC-DC converter with 2.5mm² wire, connect its input negative to the Lynx negative busbar, and wire its 12V output to the 12V fuse block that feeds the pump, the two fan splitters, the switch's DC jack and the 5V buck module of the ESP32 telemetry bridge. **Leave the D5 coolant pump's branch fuse out of the 4-way 12V fuse block (or leave the pump's 12V power lead unplugged) until the cooling loop has passed its 24-hour pressure test and the reservoir has been filled in Phase 3:** with no PWM signal connected the D5 pump runs at full speed as soon as its 12V branch is energised, and running it dry damages its ceramic bearing.
6. **AC Output:** Wire the MultiPlus-II AC output (AC-out-1) through the 2-pole 16A / 30mA Type A RCBO to the rack PDU inlet with 3 × 2.5mm² cable. Leave the RCBO off. The AC input terminals are not used in the reference build: connect nothing to them and refit their terminal cover. If a grid or generator supply is added later, Victron requires a fuse or magnetic circuit breaker of at most 32A on that input for this model; see the AC connections note in `COMPONENTS.md`.
7. **Battery Monitor:** Wire the Victron SmartShunt 500A/50mV into the common battery negative so that it is the only path between the pack negatives and the Lynx negative busbar, and connect its fused positive sense lead per the SmartShunt manual.

#### Isolation and lock-out before any work on the DC bus

Use this sequence every time a DC connection is opened, a fuse is changed or a device is removed, including after commissioning:

1. Shut down the loads: power off the compute nodes and the host, then switch the MultiPlus-II off with its front switch and open the AC output RCBO.
2. Open the **PV isolator first**. The MPPT is a source: with the battery isolators open and the PV isolator closed it would feed the DC bus with no battery on it. Victron's connection order for the controller is battery first, then PV ([SmartSolar MPPT manual, installation](https://www.victronenergy.com/media/pg/Manual_SmartSolar_MPPT_150-60_up_to_250-70/en/installation.html)); disconnection is the reverse.
3. Open **both battery isolators** and padlock them in OFF (or remove the keys). Keep the keys with the person doing the work.
4. To work on a pack, its Class T fuse or its lead up to the isolator, also switch that pack's own breaker off. Those parts stay live from the pack until it is.
5. Wait for the MultiPlus-II capacitors to discharge, then **measure**: 0V between the Lynx positive and negative busbars, and 0V on the part you are about to touch, with a multimeter you have just checked on a known live source.
6. Use insulated tools, and remove rings and watches. The PV strings stay live on the array side of the PV isolator whenever the panels are lit.

To service one pack while the system keeps running, open only that pack's isolator and breaker; the other pack then carries the whole load, so keep the load below one pack's continuous rating (see its datasheet).

#### Commissioning order (first energisation)

Steps 1 to 4 bring up the 48V bus and are needed before the pump is primed in Phase 3 (keep the D5 coolant pump's branch fuse out of the 4-way 12V fuse block during Steps 1 to 4 so the dry pump is not energised before Phase 3). Steps 5 and 6 follow once the array is wired in Phase 4.

1. Check every DC connection for polarity and torque with everything still isolated. The MultiPlus-II front switch is off, the RCBO is off, the PV isolator is open, and the D5 coolant pump branch fuse in the 4-way 12V fuse block is removed.
2. Switch on the breaker of pack 1 and check 51–54V with the correct polarity at the battery side of its isolator. Do the same for pack 2. The two packs must be within about 0.5V of each other before they are paralleled; if they are not, charge the lower one first as its manual describes.
3. Close battery isolator 1, then battery isolator 2. A brief inrush as the MultiPlus-II capacitors charge is normal; a pack BMS that trips on it needs the pre-charge procedure in the pack manual.
4. Check the bus voltage at the Lynx, then that the SmartShunt and the MPPT have powered up. The MPPT must see the battery before it sees the array, so that it detects the 48V system voltage.
5. Set the charge voltages and current limits in VictronConnect (Phase 4), measure the string voltages and polarity at the PV isolator, then close the **PV isolator last**.
6. Switch the MultiPlus-II on, check 230V at the RCBO input, close the RCBO and test it with its test button before connecting the loads.

---

### Phase 3: Liquid Cooling Loop & Leak Testing
1. **Radiator & Exhaust Fan Mounting:** Mount the single 360mm copper radiator, with three of the six 120mm fans, behind one of the two rear-panel openings on the rear brackets with vibration-dampening EPDM gaskets. Mount the other three 120mm fans in the second rear opening as exhaust fans (blowing out of the enclosure) for the air-cooled DGX Spark units and the power electronics; they screw to the 3 × 120mm fan bracket listed in `COMPONENTS.md`. Fit a finger guard on the outside of every fan.
2. **Tubing Routing:** Seat the D5 pump in its pump top with the integrated reservoir (the pump-reservoir). Connect EPDM 10/16mm ZMT tubing in three runs (pump-reservoir outlet to the system host's CPU water block, water block to the radiator, radiator back to the reservoir) using six G1/4" compression fittings, two per component. Fit the water block to the host CPU with the mounting kit for its socket (AM5 or LGA1200) before routing the tubing. The two DGX Spark units are sealed, air-cooled appliances: do not open them and do not connect them to the loop. Mount them with their intake and exhaust vents unobstructed.
3. **24-Hour Pressure Leak Test:**
   * Screw the pneumatic hand-pump leak tester into the reservoir fill port.
   * Pressurize loop to **0.6 bar (8.7 PSI)**.
   * *Pass Criteria:* Pressure drop must be **< 0.02 bar** over a 24-hour period before filling coolant.
4. **Priming:** Fill reservoir with EK CryoFuel Clear, then insert the D5 pump's branch fuse into the 4-way 12V fuse block (or connect the pump's 12V power lead) to power the 12V D5 pump from the 12V auxiliary rail (the 48V-to-12V DC-DC converter wired in Phase 2), with the system host and compute nodes still off; with no PWM signal connected the pump runs at full speed. Run it for 15 minutes to bleed air bubbles. The rack PDU is 230V AC and does not power the pump.

---

### Phase 4: Solar PV Installation (and Optional Satellite Terminal)
1. **PV Strings & Disconnect:** Wire the 8× 410W panels as two strings of four in series (4S2P).
   * Before connecting, calculate the string Voc from the panel datasheet: 4 × panel Voc, corrected to the lowest site temperature with the panel's Voc temperature coefficient. It must stay below **245V** (the MPPT 250/70's start-up and operating maximum; 250V is its absolute maximum). Twice the panel Isc must stay below the controller's **35A** PV short-circuit limit. `WIRING_DIAGRAM.md` has worked examples.
   * Run each string in 4mm² solar cable to the two-string combiner box (with its Type 2 surge protection device), then 6mm² through a 2-pole DC isolator (miniature circuit breaker or switch-disconnector rated for at least 250V DC and 35A; a 1000V DC PV-rated device is suitable) into the PV terminals of the MPPT 250/70-Tr.
   * Two parallel strings need no string fuses provided the panel's maximum series fuse rating is above one panel Isc; check the panel datasheet.
   * Measure each string's open-circuit voltage and polarity with the multimeter before closing the PV isolator, and close it only when the battery is already connected to the controller (battery isolators closed): Victron's connection order is battery first, then PV. The strings are live whenever the panels are lit: cover the panels or work with the MC4 connectors open, and never unplug a connector under load.
   * In VictronConnect set the battery voltage to 48V, the LiFePO4 charge voltages from the battery pack datasheet, and a maximum charge current no higher than the two packs' combined charge limit (the array can deliver about 64A). The MultiPlus-II charger can add up to 35A from grid or generator, about 99A in total into the 200Ah bank (about 0.5C): if the pack datasheet allows less, also lower the MultiPlus-II charge current limit in VEConfigure or VictronConnect so that the two limits together stay within the bank's limit.
2. **Grounding:** Drive a 2.4m copper ground rod into soil and bond the chassis, the PV module frames and the surge arrestor to it with 16mm² earth wire (< 10Ω ground resistance). Connect the MultiPlus-II chassis earth to the same earth bar. Do not earth the PV positive or negative conductor: Victron's manual for the MPPT states that they should not be grounded (see the earthing note in `WIRING_DIAGRAM.md`).
3. **Optional satellite terminal (not in the reference BOM):** if a motorized satellite tracking terminal is fitted, mount it atop the 9U chassis with an unobstructed $360^\circ$ azimuth sky view and more than $10^\circ$ of elevation clearance.

---

## 📋 First-Boot Commissioning Smoke Test Checklist

- [ ] **Residual-Current Test (system live):** with the inverter running and the PDU energised, the AC output RCBO trips on its test button; reset it afterwards.
- [ ] **Isolation Test (after an orderly shutdown, following the isolation and lock-out sequence above):** with the loads shut down, the MultiPlus-II switched off and the AC output RCBO open, opening the PV isolator and then both battery isolators brings the Lynx busbars to 0V (after the inverter capacitors discharge), and each isolator padlocks in OFF. Never open the DC isolators under load as a test.
- [ ] **DC Polarity Verification:** Multimeter reads $+51.2\text{ V}$ to $+53.5\text{ V}$ DC on inverter input terminals with zero reverse polarity.
- [ ] **Battery Monitoring:** The SmartShunt reports battery voltage, current and SoC over VE.Direct (the ESP32 bridge exposes them on `/metrics` once it has received a checksum-valid frame). On each pack's own BMS display or tool, all 32 LiFePO4 cells are balanced within $\pm 15\text{ mV}$. The ESP32 firmware has no RS485 link to the pack BMS.
- [ ] **Coolant Flow:** The D5 pump tachometer reports a steady speed and coolant visibly circulates through the reservoir (the loop has no flow meter); with the system host at full load, coolant temperature stabilizes within $15^\circ\text{C}$ above ambient and below the $60^\circ\text{C}$ design limit (below $35^\circ\text{C}$ in a standard $\le 25^\circ\text{C}$ indoor ambient; $39.4^\circ\text{C}$ derived target at $35^\circ\text{C}$ outdoor ambient per `BENCHMARKS.md`). Both DGX Spark nodes run at full load on their own air cooling with no thermal throttling reported.
- [ ] **Ground-to-Space RF Tracking (only if the optional phased-array terminal is fitted; it is excluded from the reference BOM):** the terminal passes its own self-calibration slew test across the full elevation arc.
- [ ] **Load-Shedder Verification:** With default thresholds, injecting a simulated low-SoC reading below 20% (shedding level 3) sets the sentinel mode file to `ECO_PRESERVATION` (and an in-process `EnterpriseManager` marks running `L2_BACKGROUND`, `L3_DEFERRABLE` and `L4_IDLE` tracked apps `paused`; `L1_STANDARD` pauses below 10% at level 4, or earlier when an app's `min_battery_soc` exceeds the reading, while `L0_CRITICAL` never pauses), and running `software/telemetry/load_shedder.sh` with `LOAD_SHED_ACTION=pause` pauses a running `LOAD_SHED_CONTAINER` via `docker pause` without interrupting core networking (nothing throttles GPU clocks or hardware power rails).
