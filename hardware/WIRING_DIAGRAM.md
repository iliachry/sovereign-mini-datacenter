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
          │ 2-string combiner + 2-pole PV   │
          │ DC isolator (250V DC, 35A min)  │
          └──────────────┬──────────────────┘
                         │ 6mm² Solar DC Cable (Red/Black)
                         ▼
          ┌─────────────────────────────────┐
          │ Victron SmartSolar MPPT 250/70  │
          │ (Max 250V PV, 35A PV Isc,       │
          │  70A Charge Out)                │
          └──────────────┬──────────────────┘
                         │ 35mm² Flex DC Cable
                         ▼
          ┌──────────────────────────────────┐
          │   Victron Lynx Distributor (48V) │  ◄─── Main 48V DC Bus
          │   (Fused Busbars)                │
          └───┬──────────────────────┬───────┘
              │                      │
              ▼                      ▼
  ┌─────────────────────┐    ┌──────────────────────────┐
  │ LiFePO4 Battery Bank│    │ Victron MultiPlus-II     │
  │ 2x 48V 100Ah Packs  │    │ 48/3000/35-32 Inverter   │
  │ (10.24 kWh Total)   │    │ 48V DC ➔ 230V AC Sine    │
  │ Built-in BMS / pack │    └──────────────┬───────────┘
  └─────────────────────┘                   │ 230V AC
                                            ▼
                           ┌───────────────────────────────┐
                           │  1U 230V Metered Rack PDU     │
                           │  (8x IEC C13 Outlets)         │
                           └──────────────┬────────────────┘
                                          │
                        ┌─────────────────┼─────────────────┐
                        ▼                 ▼                  ▼
              [DGX Spark Node 1]  [DGX Spark Node 2]  [Mini-ITX Host]
              (Air Cooled,        (Air Cooled,        (Liquid Cooled)
               own 240W PSU)       own 240W PSU)       + [Switch]
```

> **(\*) PV array:** take Voc, Isc and the Voc temperature coefficient from the datasheet of the panels you install. The eight panels are wired as two strings of four in series (4S2P).
>
> - **Voltage:** the string Voc is four times the panel Voc and rises as the panels get colder. Corrected to the lowest temperature expected on site it must stay below the MPPT 250/70's 245V start-up and operating maximum (250V is the absolute maximum). Worked examples at −10°C with a −0.28%/°C coefficient: 4 × 38V × 1.098 ≈ 167V for 108 half-cell panels; 4 × 50V × 1.098 ≈ 220V for 144 half-cell panels. A panel with an STC Voc above 50V needs its own check.
> - **Current:** the array Isc is twice the panel Isc (28A for 14A panels) and must stay below the controller's 35A maximum PV short-circuit current.
> - **Power:** 3,280W is within the controller's 4,000W nominal PV power at 48V, and gives about 64A of charge current at 51.2V against the 70A rating.
> - **String fuses:** with two strings in parallel a faulted string can only be back-fed by one other string (1 × Isc), which is below the maximum series fuse rating of typical panels, so string fuses are not required; confirm the "maximum series fuse" value on the panel datasheet. Three or more parallel strings would need a fuse per string.
> - **Cable:** 4mm² solar cable (EN 50618 / H1Z2Z2-K) per string, each carrying at most one panel Isc; 6mm² from the combiner to the MPPT, which carries both strings (up to 28A). Increase the cross-section if the run length gives more than about 3% voltage drop.
> - **Isolation and hazard:** the array open-circuit voltage is 150–230V DC, well above the 48V battery side and no longer extra-low voltage. Fit a 2-pole DC isolator rated for at least 250V DC and 35A between the combiner and the MPPT, use touch-safe MC4 connectors, and never open a PV connector under load.
>
> Controller figures are from Victron's [SmartSolar MPPT 250/60 and 250/70 datasheet](https://www.victronenergy.com/upload/documents/Datasheet-SmartSolar-charge-controller-MPPT-250-60-and-250-70-EN.pdf).

### Battery connection detail

Each pack has its own positive lead and its own fuse; the two negatives join and pass through the battery monitor shunt before reaching the negative busbar. This matches Phase 2 of `ASSEMBLY_MANUAL.md`.

```
 [Pack 1 (+)] ──35mm²──► [MEGA fuse 125A] ──► [Lynx Distributor (+)]
 [Pack 2 (+)] ──35mm²──► [MEGA fuse 125A] ──► [Lynx Distributor (+)]

 [Pack 1 (−)] ──35mm²──┐
 [Pack 2 (−)] ──35mm²──┴─► [Victron SmartShunt 500A/50mV] ──► [Lynx Distributor (−)]
                            battery side │ system side
```

- The SmartShunt must be the **only** connection between the battery negatives and the rest of the system: inverter, MPPT and every other load or charger connect on its system side, otherwise their current is not measured and the state of charge is wrong.
- Connect the SmartShunt's fused positive sense lead to the battery positive as described in its manual.
- Cell-level protection (over/under-voltage, over-current, temperature) is provided by the BMS built into each server-rack pack. The SmartShunt only measures.

> **Wire Sizing:** Use minimum 35mm² flexible cable on 48V DC bus runs. AC output wiring: 4mm² to PDU. All connections torqued to manufacturer spec.

> **Safety:** Install one 125A DC fuse in the positive lead of **each** battery pack (two fuses in total), as close to the pack terminal as practical. The fuses must carry a DC voltage rating suitable for a 48V LiFePO4 bank: check the fuse datasheet, since many MEGA fuses are rated for 32V only. Earth all metal chassis to ground bus.

---

## 2. Liquid Cooling Loop Routing

```
 ┌─────────────────────────────────────────────────────────────────┐
 │          LIQUID COOLING LOOP (SYSTEM HOST ONLY)                 │
 │      (EK-CryoFuel Clear concentrate, mixed 1:9 with water)      │
 └─────────────────────────────────────────────────────────────────┘

  [Alphacool D5 Pump + Reservoir Combo]
         │
         ▼  ← Cold Coolant In (~25°C)
  [System Host CPU Water Block]  (Mini-ITX server; block matched
         │                        to the board's socket, QD fittings)
         ▼  ← Warm Coolant Out (a few °C above inlet at full host load)
  [Primary 360mm Copper Radiator]  (3x Noctua iPPC-3000 @ 3000 RPM)
         │
         ▼
  [Secondary 360mm Copper Radiator] (3x Noctua iPPC-3000 @ 3000 RPM)
         │
         ▼
  [Return to Pump/Reservoir]
```

> **Scope:** the two NVIDIA DGX Spark units are sealed, air-cooled appliances. They are not opened, have no water block and are not connected to this loop; keep their intake and exhaust vents clear. The loop carries only the system host's CPU heat (on the order of 65–125W depending on the CPU fitted), so the two 360mm radiators run far below their capacity and the fans can be run slowly.

> **Bleed procedure:** Run pump at full speed for 30 min with reservoir cap loose. Tilt enclosure side-to-side. Top up coolant. Verify no air bubbles in sight glass.

> **Coolant ratio:** EK-CryoFuel Clear at 1:9 concentrate:distilled water. Replace every 2 years.

---

## 3. Network Topology

```
  [Internet / WAN]
        │
        ▼
  [Router / Firewall]  (Port 80, 443, 2222 forwarded)
        │  10GbE SFP+
        ▼
  [Mikrotik CRS309-1G-8S+IN]  (L2/L3 Core Switch)
        │
   ┌────┼────┬────────────┐
   ▼    ▼    ▼            ▼
[DGX1][DGX2][Mini-ITX]  [Management VLAN]
  (10GbE DAC cables)
```

> **VLAN suggestion:** VLAN 10 — Services (Traefik, Docker containers), VLAN 20 — Management (IPMI/BMC), VLAN 30 — Storage (NFS/iSCSI future expansion).
