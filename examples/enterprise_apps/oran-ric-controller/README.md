# O-RAN Near-RT RIC 5G Slicing Controller (xApp)

An autonomous Near-Real-Time Radio Access Network Intelligent Controller (RIC) xApp for the **Sovereign Mini Datacenter (SMDC)** platform, implementing the 5G Network Slicing and DePIN SLA validation architecture from the *IEEE Internet of Things Magazine* paper (*Metaverse Framework for Wireless Systems Management*).

## Key Features
- **Dynamic 5G Slice Management**:
  - **URLLC**: Sub-1ms flight and emergency control (10 Mbps, 99.999% reliability).
  - **eMBB**: 100-200 Mbps XR 3D digital shadow stream (127 Mbps allocation).
  - **mMTC**: 10,000+ IoT sensor telemetry stream (5 Mbps, NOMA scheduling).
- **Mathematical Bandwidth Isolation**: Enforces $T_{\mathrm{tx}} = (D \cdot 8)/B_{\mathrm{slice}} \cdot 1000\text{ ms}$.
- **DePIN SLA Integration**: Real-time multi-signature consensus & sensor attestation verification.
- **Power Tier**: `L0_CRITICAL` (Operates down to 15% Battery SoC with zero-trust survivability).

## Quickstart
```bash
# Register with SMDC
smdc app register ./examples/enterprise_apps/oran-ric-controller

# Start the xApp daemon
smdc app start oran-ric-controller

# Check live telemetry
smdc app status oran-ric-controller
```
