# O-RAN Near-RT RIC 5G Slicing Controller (xApp)

A reference skeleton shaped like a Near-Real-Time RAN Intelligent Controller (RIC) xApp for the **Sovereign Mini Datacenter (SMDC)** platform. It drives the in-process 5G slicing simulator (`sovereign_dc.metaverse.slicing`) that models the slicing architecture of the *IEEE Internet of Things Magazine* paper (*Metaverse Framework for Wireless Systems Management*).

> **Simulated reference skeleton.** `app.py` controls no radio: it implements no O-RAN interface (no E2, A1 or O1), connects to no RIC platform or gNB, and its latencies and connection counts come from the simulator's formulas. It creates a `DePINSLAValidator` but only reports its validator count; no SLA is evaluated in the loop. The manifest's resource figures, ports and health check describe what a real implementation would declare: `app.py` opens no port and serves no `/health` endpoint, and `smdc app start` only records the app as running (the manager launches no process).


## What the simulator models
- **5G slice model (simulated)**:
  - **URLLC**: Sub-1ms flight and emergency control (10 Mbps, 99.999% reliability).
  - **eMBB**: 100-200 Mbps XR 3D digital shadow stream (127 Mbps allocation).
  - **mMTC**: 10,000+ IoT sensor telemetry stream (5 Mbps, NOMA scheduling).
- **Bandwidth isolation formula**: the simulator computes $T_{\mathrm{tx}} = (D \cdot 8)/B_{\mathrm{slice}} \cdot 1000\text{ ms}$ per slice; nothing is enforced on a network.
- **DePIN SLA validator**: instantiated from `sovereign_dc.metaverse.depin_sla` (a simulated multi-signature model); this app only publishes how many validators it has.
- **Power Tier**: `L0_CRITICAL` in the manifest (`min_battery_soc: 15`, declarative metadata): the `EnterpriseManager` never marks an L0 app paused during load shedding.

## Quickstart
```bash
# Register with SMDC
smdc app register ./examples/enterprise_apps/oran-ric-controller

# Mark the app running (state tracking; no process is launched)
smdc app start oran-ric-controller

# Show tracked state
smdc app status oran-ric-controller
```
