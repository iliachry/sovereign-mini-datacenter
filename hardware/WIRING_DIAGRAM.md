# Electrical & Liquid Loop Schematics

## 1. High-Voltage DC & AC Electrical Wiring

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                      SOLAR ARRAY (ROOFTOP)                      │
 │         4x Monocrystalline 410W Panels — 2S2P Config           │
 │               ~80V DC Open-Circuit, ~20A Isc                    │
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
          │   (Fused Busbars, 200A Max)      │
          └───┬──────────────────────┬───────┘
              │                      │
              ▼                      ▼
  ┌─────────────────────┐    ┌──────────────────────────┐
  │ LiFePO4 Battery Bank│    │ Victron MultiPlus-II     │
  │ 2x 48V 100Ah Packs  │    │ 48/3000/35-32 Inverter   │
  │ (10.24 kWh Total)   │    │ 48V DC ➔ 230V AC Sine    │
  │ + Victron BMS       │    └──────────────┬───────────┘
  └─────────────────────┘                   │ 230V AC
                                            ▼
                           ┌───────────────────────────────┐
                           │  APC AP7930 Metered Rack PDU  │
                           │  (8 Outlets, Per-Port Monitor)│
                           └──────────────┬────────────────┘
                                          │
                        ┌─────────────────┼─────────────────┐
                        ▼                 ▼                  ▼
              [DGX Spark Node 1]  [DGX Spark Node 2]  [Mini-ITX Host]
              (Liquid Cooled)     (Liquid Cooled)      + [Switch]
```

> **Wire Sizing:** Use minimum 35mm² flexible cable on 48V DC bus runs. AC output wiring: 4mm² to PDU. All connections torqued to manufacturer spec.

> **Safety:** Install a 200A DC fuse on battery positive terminal within 30cm of battery. Earth all metal chassis to ground bus.

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
