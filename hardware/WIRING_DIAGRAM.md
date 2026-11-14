# Electrical & Liquid Loop Schematics

## 1. High-Voltage DC & AC Electrical Wiring

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                      SOLAR ARRAY (ROOFTOP)                      │
 │         4x Monocrystalline 410W Panels — 2S2P Config           │
 │   String Voc = 2x panel Voc, array Isc = 2x panel Isc (*)       │
 └──────────────────────────┬──────────────────────────────────────┘
                            │ 4mm² Solar DC Cable (Red/Black)
                            ▼
          ┌─────────────────────────────────┐
          │  Victron SmartSolar MPPT 150/70 │
          │  (Max 150V PV, 70A Charge Out)  │
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
              (Liquid Cooled)     (Liquid Cooled)      + [Switch]
```

> **(\*) PV array:** take Voc and Isc from the datasheet of the panels you install. With 2 panels in series the string Voc is twice the panel Voc and must stay below the MPPT's 150V limit at the lowest temperature expected on site (Voc rises as panels get colder). With 2 strings in parallel the array Isc is twice the panel Isc; verify it against the MPPT's PV short-circuit current rating.

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
 │                    LIQUID COOLING LOOP                          │
 │                 (EK CryoFuel Clear, ~8L Total)                  │
 └─────────────────────────────────────────────────────────────────┘

  [Alphacool D5 Pump + Reservoir Combo]
         │
         ▼  ← Cold Coolant In (~25°C)
  ┌──────┴──────────────────────────────────────────────────────┐
  │  COLD DISTRIBUTION MANIFOLD (QD Fittings, G1/4 thread)     │
  │     ├── Port A → [DGX Spark Node 1 — EK Full-Cover Block]   │
  │     └── Port B → [DGX Spark Node 2 — EK Full-Cover Block]   │
  └──────┬──────────────────────────────────────────────────────┘
         │
         ▼  ← Hot Coolant Out (~42–48°C at full GPU load)
  ┌──────┴──────────────────────────────────────────────────────┐
  │  HOT RETURN MANIFOLD                                        │
  └──────┬──────────────────────────────────────────────────────┘
         │
         ▼
  [Primary 360mm Copper Radiator]  (3x Noctua iPPC-3000 @ 3000 RPM)
         │
         ▼
  [Secondary 360mm Copper Radiator] (3x Noctua iPPC-3000 @ 3000 RPM)
         │
         ▼
  [Return to Pump/Reservoir]  ←────────────────────────────────┘
```

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
