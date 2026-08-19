# Sovereign Mini Datacenter — Physical Assembly & Commissioning Field Manual

> **Document Version:** 1.3.0  
> **Target Audience:** Field Engineers, Datacenter Builders, Solar Technicians  
> **Safety Notice:** ⚡ High DC Current (48V / 100A+) and Pressurized Liquid Cooling Loop present. Read all warnings before energizing.

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
1. **Frame Squareness:** Lay the bottom 2.5mm 5052-H32 aluminum base plate on a level workbench.
2. **Corner Extrusions:** Bolt the four 2020/3030 vertical corner posts using M5×12mm button-head screws with blue Loctite 243.
3. **Rack Rails:** Fasten the four 9U steel rack rails (EIA-310-D compliant with 19" horizontal spacing = 465.1mm pitch).
4. **Torque Specification:** Tighten all chassis frame fasteners to **4.5 N·m**.

---

### Phase 2: DC Power Wall & Battery Busbar Wiring
```
[LiFePO4 Pack 1 (5.12kWh)] ──(35mm² Red)──► [Mega Fuse 125A] ──► [Lynx DC Busbar +] ──► [Victron MultiPlus-II]
[LiFePO4 Pack 2 (5.12kWh)] ──(35mm² Red)──► [Mega Fuse 125A] ──► [Lynx DC Busbar +] ──► [Victron MPPT 150/70]
[LiFePO4 Packs 1 & 2 (-)]  ──(35mm² Blk)───────────────────────► [Victron Shunt]     ──► [Lynx DC Busbar -]
```

1. **Battery Rack Mounting:** Slide LiFePO4 Battery Pack 2 into **U1–U2** and Pack 1 into **U3–U4**. Secure faceplates with M6 cage nuts.
2. **Lug Crimping:** Crimp 35mm² (2 AWG) fine-stranded copper cables with tin-plated copper eyelet lugs. Insulate with adhesive heat shrink.
3. **Torque Specification:**
   * Battery terminal bolts (M8): **9.0 N·m**.
   * Victron Lynx Distributor M8 studs: **9.5 N·m**.
   * Inverter DC input clamps: **4.0 N·m**.
4. **Mega Fuse Installation:** Install 125A DC-rated ceramic fuses on each positive battery lead.

---

### Phase 3: Liquid Cooling Loop & Leak Testing
1. **Radiator Mounting:** Mount the dual 360mm copper radiators to the rear exhaust brackets with vibration-dampening EPDM gaskets.
2. **Tubing Routing:** Connect EPDM 10/16mm ZMT tubing between the D5 pump reservoir, DGX Spark waterblocks, and radiators using G1/4" compression fittings.
3. **24-Hour Pressure Leak Test:**
   * Screw the pneumatic hand-pump leak tester into the reservoir fill port.
   * Pressurize loop to **0.6 bar (8.7 PSI)**.
   * *Pass Criteria:* Pressure drop must be **< 0.02 bar** over a 24-hour period before filling coolant.
4. **Priming:** Fill reservoir with EK CryoFuel Clear. Jump the 12V PDU rail to cycle the D5 pump at 100% duty cycle for 15 minutes to bleed air bubbles.

---

### Phase 4: Solar PV & Space Phased-Array Installation
1. **PV Disconnect:** Wire the 4× 410W panels in 2S2P configuration (~80V Voc) into a 2-pole 1000V DC miniature circuit breaker before entering the MPPT 150/70.
2. **Grounding:** Drive a 2.4m copper ground rod into soil and bond chassis and surge arrestors with 16mm² earth wire (< 10Ω ground resistance).
3. **Phased-Array Mast:** Mount the motorized satellite tracking terminal atop the 9U chassis, ensuring an unobstructed $360^\circ$ azimuth sky view with $>10^\circ$ elevation clearance.

---

## 📋 First-Boot Commissioning Smoke Test Checklist

- [ ] **DC Polarity Verification:** Multimeter reads $+51.2\text{V}$ to $+53.5\text{V}$ DC on inverter input terminals with zero reverse polarity.
- [ ] **BMS Communication:** RS485 communication established; all 32 LiFePO4 cells balanced within $\pm 15\text{mV}$.
- [ ] **Coolant Flow:** D5 pump tachometer reports $>300\text{ L/h}$ flow rate; coolant temperature stabilizes below $35^\circ\text{C}$ at full 550 TOPS GPU load.
- [ ] **Ground-to-Space RF Tracking:** Motorized phased-array passes self-calibration slew test across full $0^\circ\text{--}90^\circ$ elevation arc.
- [ ] **Load-Shedder Verification:** Simulated low SoC trigger gracefully throttles background AI jobs without interrupting core networking.
