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
[LiFePO4 Pack 1 (5.12kWh)] ──(35mm² Red)──► [Mega Fuse 125A] ──► [Lynx DC Busbar +] ──► [Victron MultiPlus-II]
[LiFePO4 Pack 2 (5.12kWh)] ──(35mm² Red)──► [Mega Fuse 125A] ──► [Lynx DC Busbar +] ──► [Victron MPPT 250/70]
[LiFePO4 Packs 1 & 2 (-)]  ──(35mm² Blk)───────────────────────► [Victron SmartShunt] ──► [Lynx DC Busbar -]
```

1. **Battery Rack Mounting:** Slide LiFePO4 Battery Pack 2 into **U1–U2** and Pack 1 into **U3–U4**. Secure faceplates with M6 cage nuts.
2. **Lug Crimping:** Crimp 35mm² (2 AWG) fine-stranded copper cables with tin-plated copper eyelet lugs. Insulate with adhesive heat shrink.
3. **Torque Specification:**
   * Battery terminal bolts (M8): **9.0 N·m**.
   * Victron Lynx Distributor M8 studs: **9.5 N·m**.
   * Inverter DC input clamps: **4.0 N·m**.
4. **Mega Fuse Installation:** Install 125A DC-rated ceramic fuses on each positive battery lead (one per pack). Confirm on the fuse datasheet that its DC voltage rating suits a 48V LiFePO4 bank.
5. **Battery Monitor:** Wire the Victron SmartShunt 500A/50mV into the common battery negative so that it is the only path between the pack negatives and the Lynx negative busbar, and connect its fused positive sense lead per the SmartShunt manual.

---

### Phase 3: Liquid Cooling Loop & Leak Testing
1. **Radiator & Exhaust Fan Mounting:** Mount the single 360mm copper radiator, with three of the six 120mm fans, behind one of the two rear-panel openings on the rear brackets with vibration-dampening EPDM gaskets. Mount the other three 120mm fans in the second rear opening as exhaust fans (blowing out of the enclosure) for the air-cooled DGX Spark units and the power electronics; this needs a 3 × 120mm fan bracket or fan-screw holes drilled around the opening.
2. **Tubing Routing:** Connect EPDM 10/16mm ZMT tubing in three runs (pump-reservoir outlet to the system host's CPU water block, water block to the radiator, radiator back to the reservoir) using six G1/4" compression fittings, two per component. Fit the water block to the host CPU with the mounting kit for its socket (AM5 or LGA1200) before routing the tubing. The two DGX Spark units are sealed, air-cooled appliances: do not open them and do not connect them to the loop. Mount them with their intake and exhaust vents unobstructed.
3. **24-Hour Pressure Leak Test:**
   * Screw the pneumatic hand-pump leak tester into the reservoir fill port.
   * Pressurize loop to **0.6 bar (8.7 PSI)**.
   * *Pass Criteria:* Pressure drop must be **< 0.02 bar** over a 24-hour period before filling coolant.
4. **Priming:** Fill reservoir with EK CryoFuel Clear. Power the D5 pump from a dedicated DC supply at its rated voltage (12V for the listed pump; verify on its datasheet), with the system host and compute nodes still off, and run it at 100% duty cycle for 15 minutes to bleed air bubbles. The rack PDU is 230V AC and the BOM does not yet include a 12V source: use a bench supply or a 48V-to-12V DC-DC converter sized from the pump datasheet.

---

### Phase 4: Solar PV & Space Phased-Array Installation
1. **PV Strings & Disconnect:** Wire the 8× 410W panels as two strings of four in series (4S2P).
   * Before connecting, calculate the string Voc from the panel datasheet: 4 × panel Voc, corrected to the lowest site temperature with the panel's Voc temperature coefficient. It must stay below **245V** (the MPPT 250/70's start-up and operating maximum; 250V is its absolute maximum). Twice the panel Isc must stay below the controller's **35A** PV short-circuit limit. `WIRING_DIAGRAM.md` has worked examples.
   * Run each string in 4mm² solar cable to a two-string combiner, then 6mm² through a 2-pole DC isolator (miniature circuit breaker or switch-disconnector rated for at least 250V DC and 35A; a 1000V DC PV-rated device is suitable) into the PV terminals of the MPPT 250/70-Tr.
   * Two parallel strings need no string fuses provided the panel's maximum series fuse rating is above one panel Isc; check the panel datasheet.
   * Measure each string's open-circuit voltage and polarity with the multimeter before closing the isolator. The strings are live whenever the panels are lit: cover the panels or work with the MC4 connectors open, and never unplug a connector under load.
   * In VictronConnect set the battery voltage to 48V, the LiFePO4 charge voltages from the battery pack datasheet, and a maximum charge current no higher than the two packs' combined charge limit (the array can deliver about 64A). The MultiPlus-II charger can add up to 35A from grid or generator, about 99A in total into the 200Ah bank (about 0.5C): if the pack datasheet allows less, also lower the MultiPlus-II charge current limit in VEConfigure or VictronConnect so that the two limits together stay within the bank's limit.
2. **Grounding:** Drive a 2.4m copper ground rod into soil and bond chassis and surge arrestors with 16mm² earth wire (< 10Ω ground resistance).
3. **Phased-Array Mast:** Mount the motorized satellite tracking terminal atop the 9U chassis, ensuring an unobstructed $360^\circ$ azimuth sky view with $>10^\circ$ elevation clearance.

---

## 📋 First-Boot Commissioning Smoke Test Checklist

- [ ] **DC Polarity Verification:** Multimeter reads $+51.2\text{ V}$ to $+53.5\text{ V}$ DC on inverter input terminals with zero reverse polarity.
- [ ] **Battery Monitoring:** The SmartShunt reports battery voltage, current and SoC over VE.Direct (the ESP32 bridge exposes them on `/metrics` once it has received a checksum-valid frame). On each pack's own BMS display or tool, all 32 LiFePO4 cells are balanced within $\pm 15\text{ mV}$. The ESP32 firmware has no RS485 link to the pack BMS.
- [ ] **Coolant Flow:** D5 pump tachometer reports $> 300\text{ L/h}$ flow rate; coolant temperature stabilizes below $35^\circ\text{C}$ with the system host at full load. Both DGX Spark nodes run at full load on their own air cooling with no thermal throttling reported.
- [ ] **Ground-to-Space RF Tracking:** Motorized phased-array passes self-calibration slew test across full $0^\circ \text{ to } 90^\circ$ elevation arc.
- [ ] **Load-Shedder Verification:** Simulated low SoC trigger gracefully throttles background AI jobs without interrupting core networking.
