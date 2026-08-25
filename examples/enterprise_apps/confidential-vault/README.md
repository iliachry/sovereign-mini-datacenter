# Confidential Data Vault

This template demonstrates an **$L_0$ Critical** zero-trust encrypted database and secrets vault on SMDC.

## Architecture
- **Power Priority**: `L0_CRITICAL` (Operates continuously down to 10% SoC).
- **Post-Quantum Cryptography**: NIST FIPS 203 (ML-KEM-1024) for session key establishment + NIST FIPS 204 (ML-DSA-87) for signature validation.
- **Space DTN Backup**: Replicates encrypted ledger snapshots to space relays when terrestrial mesh is partitioned.

## Quickstart
```bash
# Validate manifest
smdc app validate examples/enterprise_apps/confidential-vault/

# Register and start
smdc app register examples/enterprise_apps/confidential-vault/
smdc app start confidential-vault
```
