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
sudo sh software/backup/backup.sh
```
`RESTIC_PASSWORD` must be set in `software/.env` (or the environment); the scripts
have no default password and refuse to run without one.

### Schedule Daily Automation via Cron
Add to root crontab (`sudo crontab -e`):
```cron
0 3 * * * /bin/sh /path/to/sovereign-mini-datacenter/software/backup/backup.sh >> /var/log/sovereign-backup.log 2>&1
```

### Run as a Containerized Daemon
`docker-compose.backup.yml` ships a `sovereign_backup` container that executes `backup.sh`
on a loop (default every 24 h, tunable via `BACKUP_INTERVAL_SECONDS` in `.env`) against
read-only mounts of the core-stack data volumes. The local repository is kept in
the `backup_storage` volume mounted at `/var/backups/sovereign`; if you change
`RESTIC_REPOSITORY`, use a remote backend (`s3:`, `sftp:`, `rest:`), because any other
local path inside the container is not persistent:
```bash
./software/setup.sh --with-backup
# or standalone, attached to the running core stack's volumes (add -p <name> if the
# core stack does not use the default project name "software"):
docker compose --project-directory software \
  -f software/docker-compose.attach.yml -f software/backup/docker-compose.backup.yml up -d
```
The overlay never sets volume names of its own: merged or attached, it reads the very
volumes the services write, whatever the Compose project name.

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