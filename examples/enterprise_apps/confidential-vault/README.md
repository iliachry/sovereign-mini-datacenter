# Confidential Data Vault

This template shows how an **$L_0$ Critical** secrets-vault workload is declared and wired to the SMDC SDK.

> **Simulated reference skeleton.** `app.py` stores and encrypts nothing: it holds no records and no keys, performs no ML-KEM or ML-DSA operation, and replicates nothing. It loops, increments a counter and publishes that counter as placeholder telemetry (`"simulated": true`). The manifest's resource figures, ports and health check describe what a real implementation would declare: `app.py` opens no port and serves no `/health` endpoint, and `smdc app start` only records the app as running (the manager launches no process).


## What the example contains
- **Power Priority**: `L0_CRITICAL` in the manifest (`min_battery_soc: 10`): the `EnterpriseManager` never marks an L0 app paused during load shedding. The threshold itself is declarative metadata.
- **Lifecycle wiring**: `AppLifecycleHandler` and `SMDCClient.emit_telemetry` calls, as a starting point for a real service.
- **Not implemented here**: encryption at rest, key management, access control, ML-KEM session keys and DTN replication. For post-quantum primitives a real vault could build on `sovereign_dc.security.pqc` (ML-KEM-1024 encapsulation, ML-DSA-87 signatures; pure-Python, not FIPS-validated). The only PQC step available for this example today is package signing: `smdc app package` signs the archive with ML-DSA-87.

## Quickstart
```bash
# Validate manifest
smdc app validate examples/enterprise_apps/confidential-vault/

# Register and start
smdc app register examples/enterprise_apps/confidential-vault/
smdc app start confidential-vault
```
