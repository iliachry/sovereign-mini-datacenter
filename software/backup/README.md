# Sovereign Automated Backup & Disaster Recovery

This module provides **AES-256 encrypted, deduplicated, and automated snapshots** of all containers, volumes, configuration files, and state databases using [Restic](https://restic.net).

---

## ??? Key Capabilities

- **Zero-Knowledge Encryption:** Encrypted locally before leaving disk using AES-256-CTR and HMAC-SHA256.
- **Deduplication:** Only saves delta blocks across snapshots, minimizing storage consumption.
- **Dual Target Architecture:**
  - **Local Target:** Cold NVMe / external ruggedized USB drive at `/var/backups/sovereign`.
  - **Remote Target (Optional):** Sovereign off-site MinIO / Wasabi / AWS S3 endpoint.
- **Bit-Rot Protection:** Built-in cryptographic verification (`restic check`).

---

## ?? Running Backups

### Run an Immediate Backup Snapshot
```bash
sudo bash software/backup/backup.sh
```

### Schedule Daily Automation via Cron
Add to root crontab (`sudo crontab -e`):
```cron
0 3 * * * /bin/bash /path/to/sovereign-mini-datacenter/software/backup/backup.sh >> /var/log/sovereign-backup.log 2>&1
```

---

## ?? Disaster Recovery

### List Available Snapshots
```bash
sudo bash software/backup/restore.sh list
```

### Restore to a Fresh Node
```bash
sudo bash software/backup/restore.sh restore-latest /mnt/restore_dest
```

### Verify Integrity
```bash
sudo bash software/backup/restore.sh verify
```