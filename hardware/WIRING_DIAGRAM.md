# Electrical & Liquid Loop Schematics

## 1. High-Voltage DC & AC Electrical Wiring

```
 [ Solar PV Array ]  (4x 410W Panels in 2S2P: ~80V DC, 20A)
        │
        ▼
 [ Victron MPPT 150/70 ] 
        │
        ├──────────────────────────────┐
        ▼                              ▼
 [ 48V LiFePO4 Battery Bank ]   [ Victron MultiPlus-II Inverter ]
   (10.24 kWh Total Storage)           (48V DC ➔ 230V AC Pure Sine)
                                               │
                                               ▼
                                  [ 19" Power Distribution (PDU) ]
                                               │
                                 ┌─────────────┴─────────────┐
                                 ▼                           ▼
                        [ DGX Spark Node 1 ]       [ DGX Spark Node 2 ]
                        (Dual Liquid Blocks)       (Dual Liquid Blocks)
```

---

## 2. Liquid Cooling Loop Routing

```
 [ Reservior / D5 Pump Combo ] (350 L/h Flow)
        │
        ▼  (Cold Coolant In ~25°C)
 ┌──────┴──────────────────────────┐
 │ [ DGX Node 1 Water Block ]       │
 │ [ DGX Node 2 Water Block ]       │
 └──────┬──────────────────────────┘
        │
        ▼  (Hot Coolant Out ~42°C)
 [ Primary 360mm Copper Radiator ] (Noctua Industrial Fans 3000 RPM)
        │
        ▼
 [ Secondary 360mm Copper Radiator ]
        │
        └──────► (Return to Reservoir)
```
