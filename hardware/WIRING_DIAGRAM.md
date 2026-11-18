# Electrical & Liquid Loop Schematics

## 1. High-Voltage DC & AC Electrical Wiring

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                      SOLAR ARRAY (ROOFTOP)                      │
 │    8x Monocrystalline 410W Panels — 4S2P Config (3,280W)       │
 │   String Voc = 4x panel Voc, array Isc = 2x panel Isc (*)       │
 └──────────────────────────┬──────────────────────────────────────┘
                            │ 2 strings, 4mm² solar cable each
                            ▼
          ┌─────────────────────────────────┐
          │ 2-string combiner (Type 2 SPD)  │
          │ + 2-pole PV DC isolator         │
          │ (250V DC, 35A min)              │
          └──────────────┬──────────────────┘
                         │ 6mm² Solar DC Cable (Red/Black)
                         ▼
          ┌─────────────────────────────────┐
          │ Victron SmartSolar MPPT 250/70  │
          │ (Max 250V PV, 35A PV Isc,       │
          │  70A Charge Out)                │
          └──────────────┬──────────────────┘
                         │ 35mm² Flex DC Cable, 100A MEGA fuse
                         ▼
          ┌──────────────────────────────────┐
          │   Victron Lynx Distributor (48V) │  ◄─── Main 48V DC Bus
          │ (MEGA: 3x 125A/58V, 1x 100A/80V) │
          └───┬──────────────────────┬───────┘
              │ per pack:            │
              │ battery isolator,    │
              │ then Class T 125A    │
              ▼ at the pack          ▼
  ┌─────────────────────┐    ┌──────────────────────────┐
  │ LiFePO4 Battery Bank│    │ Victron MultiPlus-II     │
  │ 2x 48V 100Ah Packs  │    │ 48/3000/35-32 Inverter   │
  │ (10.24 kWh Total)   │    │ 48V DC ➔ 230V AC Sine    │
  │ Built-in BMS / pack │    │ AC-in: not connected     │
  └─────────────────────┘    └──────────────┬───────────┘
                                            │ AC-out-1, 230V AC, through a
                                            │ 2-pole RCBO 16A / 30mA
                                            ▼
                           ┌───────────────────────────────┐
                           │  1U 230V Metered Rack PDU     │
                           │  (8x IEC C13 Outlets)         │
                           └──────────────┬────────────────┘
                                          │
                        ┌─────────────────┼─────────────────┐
                        ▼                 ▼                  ▼
              [DGX Spark Node 1]  [DGX Spark Node 2]  [Mini-ITX Host]
              (Air Cooled,        (Air Cooled,        (Liquid Cooled,
               own 240W PSU)       own 240W PSU)       own PSU)
```

> **(\*) PV array:** take Voc, Isc and the Voc temperature coefficient from the datasheet of the panels you install. The eight panels are wired as two strings of four in series (4S2P).
>
> - **Voltage:** the string Voc is four times the panel Voc and rises as the panels get colder. Corrected to the lowest temperature expected on site it must stay below the MPPT 250/70's 245V start-up and operating maximum (250V is the absolute maximum). Worked examples at −10°C with a −0.28%/°C coefficient: 4 × 38V × 1.098 ≈ 167V for 108 half-cell panels; 4 × 50V × 1.098 ≈ 220V for 144 half-cell panels. A panel with an STC Voc above 50V needs its own check.
> - **Current:** the array Isc is twice the panel Isc (28A for 14A panels) and must stay below the controller's 35A maximum PV short-circuit current.
> - **Power:** 3,280W is within the controller's 4,000W nominal PV power at 48V, and gives about 64A of charge current at 51.2V against the 70A rating. If a grid or generator supply is added on the MultiPlus-II AC input (not part of the reference build), its charger (up to 35A) can run at the same time and the bank can see about 99A, about 0.5C for 200Ah; see the charge-current note in `COMPONENTS.md` for the two settings that cap it.
> - **String fuses:** with two strings in parallel a faulted string can only be back-fed by one other string (1 × Isc), which is below the maximum series fuse rating of typical panels, so string fuses are not required; confirm the "maximum series fuse" value on the panel datasheet. Three or more parallel strings would need a fuse per string.
> - **Cable:** 4mm² solar cable (EN 50618 / H1Z2Z2-K) per string, each carrying at most one panel Isc; 6mm² from the combiner to the MPPT, which carries both strings (up to 28A). Increase the cross-section if the run length gives more than about 3% voltage drop.
> - **Isolation and hazard:** the array open-circuit voltage is 150–230V DC, well above the 48V battery side and no longer extra-low voltage. Fit a 2-pole DC isolator rated for at least 250V DC and 35A between the combiner and the MPPT (it switches both PV conductors; a DC miniature circuit breaker used here must be non-polarised or wired as its polarity marking shows), use touch-safe MC4 connectors, and never open a PV connector under load. The isolator removes the array from the controller, not from the cable on the array side: the strings and the combiner stay live whenever the panels are lit.
> - **Overvoltage:** the Type 2 surge protection device in the combiner must have a maximum continuous PV voltage (Ucpv) above the cold-weather string Voc worked out above, and its earth terminal goes to the earth bar by the shortest route. It limits induced surges; it is not protection against a direct lightning strike.
> - **Connection order:** Victron's manual for the controller gives the order: connect the battery, wait for the controller to recognise the system voltage, then connect the PV ([SmartSolar MPPT manual, installation](https://www.victronenergy.com/media/pg/Manual_SmartSolar_MPPT_150-60_up_to_250-70/en/installation.html), checked on 2026-11-14). So the PV isolator is closed last and opened first: never open both battery isolators while the PV isolator is closed, because the controller would then be feeding the DC bus with no battery on it.
>
> Controller figures are from Victron's [SmartSolar MPPT 250/60 and 250/70 datasheet](https://www.victronenergy.com/upload/documents/Datasheet-SmartSolar-charge-controller-MPPT-250-60-and-250-70-EN.pdf).

### Battery connection detail

Each pack has its own positive lead with a Class T fuse at the pack followed by a load-break battery isolator, and lands on its own fused position of the Lynx Distributor; the inverter and the MPPT take the other two positions. The MPPT position carries a 100A fuse, not 125A, because Victron limits the battery fuse of the 70A controllers to 100A (table below). The two negatives join and pass through the battery monitor shunt before reaching the negative busbar. This matches Phase 2 of `ASSEMBLY_MANUAL.md`.

```
 [Pack 1 (+)] ──► [Class T fuse 125A] ──► [Battery isolator 1] ──35mm²──► [Lynx position 1, MEGA 125A/58V] ─┐
 [Pack 2 (+)] ──► [Class T fuse 125A] ──► [Battery isolator 2] ──35mm²──► [Lynx position 2, MEGA 125A/58V] ─┤ Lynx (+) busbar
 [MultiPlus-II DC (+)] ◄──────────────────────────────────────35mm²───── [Lynx position 3, MEGA 125A/58V] ─┤
 [MPPT 250/70 battery (+)] ◄──────────────────────────────────35mm²───── [Lynx position 4, MEGA 100A/80V] ─┘

 [Pack 1 (−)] ──35mm²──┐
 [Pack 2 (−)] ──35mm²──┴─► [Victron SmartShunt 500A/50mV] ──► [Lynx Distributor (−)]
                            battery side │ system side
```

- The SmartShunt must be the **only** connection between the battery negatives and the rest of the system: inverter, MPPT and every other load or charger connect on its system side, otherwise their current is not measured and the state of charge is wrong.
- Connect the SmartShunt's fused positive sense lead to the battery positive as described in its manual.
- Cell-level protection (over/under-voltage, over-current, temperature) is provided by the BMS built into each server-rack pack. The SmartShunt only measures.

### Battery isolators (DC disconnects)

Victron's installation manual for the MultiPlus-II requires every installation to have a way to disconnect both the AC and the DC circuits; circuit breakers used for overcurrent protection count as disconnects, and where fuses are used "separate disconnect switches are required between the source and the fuses" ([MultiPlus-II manual, installation, section 4.1](https://www.victronenergy.com/media/pg/MultiPlus-II_230V/en/installation.html), checked on 2026-11-14). Fuses protect the battery feeds here, so each pack has its own switch. The switch is between the pack and the MEGA fuses in the Lynx and meets the quoted sentence for those fuses. It is downstream of the Class T fuse, which it cannot de-energise: for that fuse the disconnect is the pack's own breaker (see "The pack's own breaker" below), and a pack without one leaves the Class T fuse with no disconnect ahead of it.

| Item | Requirement |
| :--- | :--- |
| Quantity and position | Two, one per pack, in the positive lead: pack terminal, Class T fuse, isolator, Lynx position. The fuse stays at the pack so that the cable up to the isolator is fused. |
| Type | DC switch-disconnector to IEC 60947-3 that can make and break load current (utilisation category DC-21B or higher). An off-load battery switch is not sufficient. |
| Voltage | Rated operational voltage of at least 60V DC (the bus reaches about 58V on charge). |
| Current | Rated operational current of at least 125A at that voltage, the rating of the fuse it sits behind; a 160A device is the usual size. |
| Lock-out | Handle that can be padlocked in OFF, or a removable key. |
| Poles | One pole in the positive lead. Use a 2-pole device with the pack negative through the second pole (on the battery side of the SmartShunt) where the local rules require both conductors to be switched. |

- **Why one per pack:** the packs land on separate Lynx positions, so no single conductor carries the whole bank. A switch per pack isolates one pack for a fault or for service while the other keeps the system running, and the two together (with the PV isolator open) remove every source from the DC bus and from the MEGA fuses in the Lynx.
- **The pack's own breaker:** server-rack packs usually have a DC circuit breaker on the front panel; the BOM names no pack model, so the installer must check. That breaker is the disconnect between the cells and the Class T fuse and is opened before the Class T fuse is touched. It does not replace the Class T fuse (its interrupting rating is far lower) or the lockable isolator. A BMS power button is an electronic switch, not an isolator.
- **Victron's battery switch is not a load-break device.** The Battery Switch ON/OFF 275A is rated 275A continuous and 60V maximum operating voltage ([datasheet](https://www.victronenergy.com/upload/documents/Datasheet-Battery-Switch-275-A-EN.pdf)), but its manual states that the switch is not rated to make or break current ([manual](https://www.victronenergy.com/upload/documents/Battery_Switch_ON-Off_275A/103859-Battery_Switch_-_manual-pdf-en.pdf)). It does not meet the table above.
- **What stays live:** with a battery isolator open, the pack terminal, the Class T fuse holder and the cable up to the isolator are still live unless the pack's own breaker is open too. With both isolators open, the DC bus is still fed by the MPPT until the PV isolator is open, and the capacitors of the MultiPlus-II hold a charge for some time. Measure before touching. The isolation sequence is in Phase 2 of `ASSEMBLY_MANUAL.md`.

### Fuse and cable per Lynx Distributor position

| Lynx position | Device | Fuse fitted | What the device manufacturer prescribes | Branch cable |
| :---: | :--- | :--- | :--- | :--- |
| 1 | Battery pack 1 | MEGA 125A/58V in the Lynx, plus a Class T 125A and a battery isolator at the pack | The pack datasheet gives the maximum continuous current (100A is typical for a 48V 100Ah server-rack pack); the fuse protects the 35mm² lead and must not be below the pack's rated current. Confirm against the datasheet of the pack you buy. | 35mm² |
| 2 | Battery pack 2 | MEGA 125A/58V in the Lynx, plus a Class T 125A and a battery isolator at the pack | As position 1 | 35mm² |
| 3 | MultiPlus-II 48/3000/35-32 | MEGA 125A/58V | Recommended DC fuse 125A, recommended cable 35mm² for runs of 0–5m (70mm² for 5–10m), for the 48/3000/35 ([MultiPlus-II manual, installation](https://www.victronenergy.com/media/pg/MultiPlus-II_230V/en/installation.html)) | 35mm², run under 5m |
| 4 | SmartSolar MPPT 250/70-Tr | MEGA 100A/80V | Battery fuse 80A minimum, 100A maximum for the MPPT 150/70 and 250/70 ([SmartSolar MPPT manual, installation](https://www.victronenergy.com/media/pg/Manual_SmartSolar_MPPT_150-60_up_to_250-70/en/installation.html)). The manual gives no minimum battery-cable cross-section; it asks for flexible multi-stranded copper cable and 2.4 N·m on the terminals, and the controller's power terminals accept at most 35mm² ([datasheet](https://www.victronenergy.com/upload/documents/Datasheet-SmartSolar-charge-controller-MPPT-250-60-and-250-70-EN.pdf)). | 35mm² (the 100A fuse also protects 25mm²; 35mm² is kept so that one cable size is used throughout) |

- The 48V-to-12V converter is **not** on a Lynx position. It has its own inline 10A fuse, described under "12V auxiliary rail" below.
- A 125A fuse must not be fitted in position 4: it exceeds the 100A maximum that Victron prescribes for the controller, whatever the cable size.
- Victron's fuse datasheet lists a 58V MEGA range that starts at 125A and an 80V MEGA range that includes 80A and 100A ([Victron fuse datasheet](https://www.victronenergy.com/upload/documents/Datasheet-Midi,-Mega-and-ANL-fuses,-and-fuse-holders-EN-.pdf)). The Lynx Distributor manual asks for MEGA fuses rated 58V for 48V systems; the 80V fuse exceeds that voltage rating and has the same MEGA format. An 80A/80V MEGA fuse is also within Victron's range for the controller; 100A is chosen to leave margin above the 70A rated charge current.
- The four fuse ratings add up to 475A, below the Lynx Distributor's 1000A busbar rating; Victron requires the sum of the fuses not to exceed the rating of the Lynx module ([Lynx Distributor manual, system design](https://www.victronenergy.com/media/pg/Lynx_Distributor/en/system-design.html)).
- Manufacturer documents were checked on 2026-11-14. Local wiring regulations take precedence over this table.

> **Wire Sizing:** Use 35mm² flexible copper cable on the 48V DC bus runs listed above, and keep each run under 5m. AC output wiring: 3 × 2.5mm² from the MultiPlus-II AC output (AC-out-1) through the 16A RCBO to the PDU. The AC input terminals are not used in the reference build: nothing is connected to them and their cover stays fitted. Tighten every connection to the torque in the manufacturer's manual (the values are listed in Phase 2 of `ASSEMBLY_MANUAL.md`).

> **Safety:** Install one 125A Class T fuse (DC rating ≥ 125V, interrupting rating ≥ 20kA) in the positive lead of **each** battery pack (two in total), as close to the pack terminal as practical: a LiFePO4 bank can drive several kA into a short circuit, and the Class T fuse is the device rated to interrupt it. The Lynx Distributor accepts MEGA fuses only, and Victron specifies 58V-rated MEGA fuses for 48V systems ([Lynx Distributor manual](https://www.victronenergy.com/media/pg/Lynx_Distributor/en/system-design.html)): fit a 125A/58V MEGA fuse in positions 1 to 3 and a 100A/80V MEGA fuse in position 4 (the MPPT), and never a 32V one. A fuse is not a disconnect: each pack lead also carries a load-break battery isolator directly after its Class T fuse (see "Battery isolators" above).

> **Earthing:**
>
> - Bond the enclosure, every metal chassis inside it, the PV module frames and the surge protection device to the earth bar, and the earth bar to the earth rod.
> - The MultiPlus-II is a safety class I product: its chassis or AC earth terminal must have a permanent earth connection. In this build the AC input is not connected, so that connection is the chassis earth stud wired to the earth bar (the manual allows the earth conductor of the AC input to provide it in a fixed installation, and otherwise requires the casing to be grounded). With no AC input present, its internal ground relay links the output neutral to the chassis, which is what lets the 30mA RCBO on the AC output work; Victron states that a Type A residual-current device can be used on this inverter ([MultiPlus-II manual, installation](https://www.victronenergy.com/media/pg/MultiPlus-II_230V/en/installation.html)).
> - **AC protection:** the manual requires an earth leakage circuit breaker and a fuse or circuit breaker, rated for the expected load, in series with the output; the 2-pole 16A / 30mA RCBO is that device and is also the AC disconnect. If a grid or generator supply is ever connected to AC-in, the manual requires a fuse or magnetic circuit breaker of at most 32A on that input for this 3kVA model, with the cable sized to match; those parts are not in the BOM (see the AC connections note in `COMPONENTS.md`).
> - Do **not** earth the PV array's positive or negative conductor. Victron's manual for this controller states that the PV positive and negative should not be grounded, that only the module frames are grounded, and that the controller has no ground-fault detector ([SmartSolar MPPT manual, installation](https://www.victronenergy.com/media/pg/Manual_SmartSolar_MPPT_150-60_up_to_250-70/en/installation.html)).
> - The same manual allows a single earth connection on the battery side, near the battery. This design leaves the choice (battery negative earthed at one point, or floating) to the local wiring regulations; if the negative is earthed, earth it at one point only, on the system side of the SmartShunt.

### 12V auxiliary rail

The coolant pump and the six 120mm fans are 12V devices, and the MikroTik switch (12–57V DC jack) and the ESP32 telemetry bridge (through a 5V buck module) run from the same rail so that they stay up when the inverter is off. The rail is fed from the 48V bus through a DC-DC converter; the rack PDU is 230V AC and does not supply these loads.

```
 [Lynx (+) busbar] ──► [10A fuse, ≥ 80V DC] ──2.5mm²──► [48V-to-12V DC-DC converter, 20A]
 [Lynx (−) busbar] ───────────────────────────────────► [converter input (−)]
                                                              │ 12V
                                                              ▼
                                                   [4-way 12V fuse block]
                                                     ├──► [D5 coolant pump]
                                                     ├──► [3x radiator fans  (PWM splitter)]
                                                     ├──► [3x exhaust fans   (PWM splitter)]
                                                     └──► [MikroTik switch DC jack] and
                                                          [5V buck ──► ESP32 telemetry bridge]
```

- The converter's supply is fused separately because Victron's 48V-input Orion-Tr models have no internal input fuse and its manual requires an external one ([Orion-Tr isolated converter manual](https://www.victronenergy.com/upload/documents/Manual-Orion-Tr-isolated-DC-DC-converters-EN-FR-NL-ES-IT-DE.pdf)); the manual states no rating. 10A is chosen from the converter's input current: about 5.7A at full output from a 48V bus and about 6.9A at 40V (240W at 87% efficiency), and 10A is within the rating of the 2.5mm² supply wire.
- Connect the tap with a ring lug on a spare stud at the end of the Lynx positive busbar, and mount the fuse holder within about 150mm of the busbar: the wire between the busbar and the fuse is protected only by the pack fuses, which are far too large for 2.5mm² wire.
- Load: pump 23W + six fans at 3.6W each (21.6W) + switch 23W + telemetry bridge 1.5W = 69.1W (about 5.7A), against a 20A converter; the sizing is in `COMPONENTS.md`.
- The converter's input negative connects on the system side of the SmartShunt (the Lynx negative busbar), so the 12V load is included in the measured battery current.
- The PWM signal for the fans and the pump comes from the system host's fan headers; only the 12V power comes from this rail. Without a PWM signal the fans and the pump run at full speed; during initial commissioning (`ASSEMBLY_MANUAL.md` Phase 2), leave the D5 pump branch fuse out of the 4-way 12V fuse block until the cooling loop has passed its leak test and been filled in Phase 3 so the pump never runs dry.

---

## 2. Liquid Cooling Loop Routing

```
 ┌─────────────────────────────────────────────────────────────────┐
 │          LIQUID COOLING LOOP (SYSTEM HOST ONLY)                 │
 │      (EK-CryoFuel Clear concentrate, mixed 1:9 with water)      │
 └─────────────────────────────────────────────────────────────────┘

  [D5 Pump (Alphacool VPP755) + Pump Top with Reservoir]
         │
         ▼  ← Cold Coolant In (~25°C)
  [System Host CPU Water Block]  (Mini-ITX server; block matched
         │                        to the board's socket, G1/4
         │                        compression fittings)
         ▼  ← Warm Coolant Out (a few °C above inlet at full host load)
  [360mm Copper Radiator]  (rear opening 1; 3x Noctua iPPC-3000, PWM)
         │
         ▼
  [Return to Pump/Reservoir]

  Not on the loop:
  [Rear opening 2]  3x Noctua iPPC-3000 exhaust fans (PWM) for the
                    air-cooled DGX Spark units, inverter and PSUs
```

> **Scope:** the two NVIDIA DGX Spark units are sealed, air-cooled appliances. They are not opened, have no water block and are not connected to this loop; keep their intake and exhaust vents clear. The loop carries only the system host's CPU heat (on the order of 65–125W depending on the CPU fitted), so the single 360mm radiator runs far below its capacity and its fans can be run slowly. The loop has six G1/4 compression fittings (two each on the water block, the pump-reservoir and the radiator) and no quick-disconnect couplings. The second rear opening holds three exhaust fans that move enclosure air only.

> **Bleed procedure:** Run pump at full speed for 30 min with reservoir cap loose. Tilt enclosure side-to-side. Top up coolant. Verify no air bubbles remain in the reservoir.

> **Coolant ratio:** EK-CryoFuel Clear at 1:9 concentrate:distilled water. Replace every 2 years.

---

## 3. Network Topology

```
  [Internet / WAN]
        │
        ▼
  [Site Router / Firewall]  (Site-supplied; Port 80, 443, 2222 forwarded)
        │  10GbE SFP+ DAC (or 1GbE RJ-45 WAN port on CRS309)
        ▼
  [Mikrotik CRS309-1G-8S+IN]  (L2/L3 Core Switch)
        │
   ┌────┴─────────┬──────────────┬─────────────────┐
   ▼              ▼              ▼                 ▼
[DGX1]         [DGX2]       [Mini-ITX]    [Management VLAN]
(S+RJ10 +      (S+RJ10 +    (10GbE SFP+   (Cat6A RJ-45
 Cat6A 10GbE)   Cat6A 10GbE) DAC)          out-of-band)
```

> **VLAN suggestion:** VLAN 10 — Services (Traefik, Docker containers), VLAN 20 — Management (IPMI/BMC), VLAN 30 — Storage (NFS/iSCSI future expansion).
