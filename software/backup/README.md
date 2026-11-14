# Sovereign Backup & Disaster Recovery

Encrypted, deduplicated snapshots with [Restic](https://restic.net) (AES-256-CTR +
Poly1305-AES; `restic check` detects bit rot in the archive).

A file-level copy of a **running** database is not a backup: pages and write-ahead log
can come from different moments, and the copy may not start. `restic check` cannot see
that, because it verifies the archive, not the database inside it. So `backup.sh` never
hands restic a live database directory. It works in two phases:

1. **Dump** (on the host). Every database is exported with its own tool into
   `BACKUP_DUMP_DIR/smdc-dumps/`, each dump is checked, and a `MANIFEST` records the
   result. If any dump fails, the run stops with a non-zero exit code **before restic is
   called**: there is no snapshot, rather than a snapshot that looks fine and is not.
2. **Files.** restic backs up the dump directory, the deployment configuration and the
   static files of the volumes. Live database files are excluded.

## What a snapshot contains

| Data | How it is captured | Consistency |
|---|---|---|
| OpenProject database (embedded PostgreSQL) | `pg_dump -d openproject -x -O -Fc` inside the container ([OpenProject docs](https://www.openproject.org/docs/installation-and-operations/operation/backing-up/)); the `openproject_pgdata` volume itself is never read | application-consistent |
| GitLab (database, repositories, uploads, CI artifacts, ...) | `gitlab-backup create` inside the container ([GitLab docs](https://docs.gitlab.com/administration/backup_restore/backup_gitlab/)); only `gitlab_data/backups/` is read, never GitLab's live PostgreSQL/Redis/Gitaly directories | application-consistent |
| GitLab `gitlab.rb`, `gitlab-secrets.json`, SSH host keys | files of the `gitlab_config` volume. GitLab documents that these are **not** in the archive above; without `gitlab-secrets.json` encrypted columns (2FA secrets, CI variables) are unrecoverable | static files |
| Vaultwarden `db.sqlite3` | SQLite online backup, `sqlite3 ... ".backup"` ([Vaultwarden wiki](https://github.com/dani-garcia/vaultwarden/wiki/Backing-up-your-vault)) | application-consistent |
| Nextcloud database (`data/owncloud.db`; this stack runs Nextcloud on SQLite) | SQLite online backup; database type, name and data directory are read through `occ` ([Nextcloud docs](https://docs.nextcloud.com/server/latest/admin_manual/maintenance/backup.html)) | application-consistent database; see the note on files below |
| Headscale `db.sqlite`, Grafana `grafana.db`, Open WebUI `webui.db`, CrowdSec `crowdsec.db`, DTN spool `bundles.db` (WAL) | SQLite online backup ([sqlite.org](https://www.sqlite.org/backup.html)) | application-consistent |
| SMDC state directory (`BACKUP_SMDC_STATE_DIR`): ledgers and DTN spool / wallets and node keys | SQLite online backup / plain copy | application-consistent / static files |
| Attachments, uploads, user files, keys, certificates: `traefik_certs`, `openproject_data`, `nextcloud_data`, `vaultwarden_data`, `webui_data`, `grafana_data`, `headscale_data`, `space_spool_data`, `sovereign_mode`, `crowdsec_data`, `crowdsec_config` | restic file-level, database files excluded | **best effort**: files written during the walk may be captured mid-write |
| Prometheus TSDB (`prometheus_data`) | restic file-level of completed blocks; `wal/`, `chunks_head/`, `wbl/`, `lock` excluded | **best effort**: the last ~2-3 h of samples are lost; a block that was being compacted during the walk may have to be deleted before Prometheus starts. The admin API (snapshots) is not enabled in this stack |
| Deployment configuration: `software/` (compose files, overlays, `prometheus.yml`, Grafana provisioning, Headscale config, `acquis.d`, Mailcow override) **including `.env`** | restic file-level | static files |
| Qdrant vectors (`qdrant_data`) | **not backed up** | re-create: the knowledge indexer re-embeds Nextcloud files; re-index Open WebUI knowledge from its uploads |
| Ollama models (`ollama_data`) | **not backed up** | `ollama pull` again (an off-grid node needs a network path or a model archive for that) |
| Logs (`gitlab_logs`, `traefik_logs`), the restic repository itself (`backup_storage`) | **not backed up** | - |

`.env` holds every secret of the stack and is required to rebuild the node, so it **is**
in the snapshot. It is protected by the repository's encryption only: whoever has the
repository and `RESTIC_PASSWORD` has all of them. The scripts never print it. Keep
`RESTIC_PASSWORD` somewhere that survives the loss of the node; it is in `.env`, which
is inside the repository it unlocks.

Nextcloud files and its database are captured at different moments (the node stays
online instead of going into maintenance mode). After a restore run
`occ maintenance:data-fingerprint` and `occ files:scan --all`
([Nextcloud restore docs](https://docs.nextcloud.com/server/latest/admin_manual/maintenance/restore.html)).

Which volume falls in which class is declared in [`volumes.conf`](volumes.conf).
`scripts/validate_compose.sh` fails when a named volume of the merged stack is not
classified there, or when the backup container's mounts differ from it.

What this does **not** cover: Mailcow (a separate Compose project with its own
[backup script](https://docs.mailcow.email/backup_restore/b_n_r-backup/)), and anything
outside `software/` and the stack's volumes.

## Running backups

Requirements on the host: root on the Docker host (volume mountpoints are read
directly; Docker Desktop, rootless Docker and remote daemons are not supported),
`docker`, `sqlite3`, `restic`. `RESTIC_PASSWORD` must be set in `software/.env` (or the
environment); there is no default password.

The dump directory (`BACKUP_DUMP_DIR`, default `/var/backups/sovereign-dumps`, mode
0700) holds **plaintext** database dumps. Keep it on an encrypted disk. Size: one copy
of every SQLite database, one compressed OpenProject dump; GitLab's archives stay in
its own volume (`gitlab_rails['backup_keep_time']` keeps 2 days of them).

### A. Host job (recommended): one command does both phases

```bash
sudo sh software/backup/backup.sh
```
```cron
# root crontab
0 3 * * * /bin/sh /path/to/sovereign-mini-datacenter/software/backup/backup.sh >> /var/log/sovereign-backup.log 2>&1
```
The exit code is non-zero when a dump or the restic run failed; alert on it.

### B. Backup container + host dump job

`./software/setup.sh --with-backup` starts `sovereign_backup`, which runs the **files
phase only**, every `BACKUP_INTERVAL_SECONDS` (default 24 h). The dumps still come from
the host:

```cron
# root crontab - before the container's run
30 2 * * * /bin/sh /path/to/sovereign-mini-datacenter/software/backup/backup.sh --dump-only >> /var/log/sovereign-backup.log 2>&1
```

If the `MANIFEST` is missing, not marked complete, or older than
`BACKUP_DUMP_MAX_AGE_SECONDS` (default 26 h), the container's run exits non-zero,
uploads nothing, logs `[ERROR] Backup run failed: NO snapshot was taken`, and the
container becomes `unhealthy` (`docker ps`). It does not fall back to copying live
databases.

**Why the container does not dump by itself.** Dumping needs `docker exec` into the
database containers, which means mounting the Docker socket: root on the host for a
container that is attached to the service network and, with an `s3:`/`sftp:` repository,
talks to the internet. The dump phase therefore stays on the host, where root is
already required, and the container gets read-only mounts and no socket
(`scripts/validate_compose.sh` rejects a socket mount). The cost is a second scheduled
job and a host directory with plaintext dumps.

The local repository is kept in the `backup_storage` volume mounted at
`/var/backups/sovereign`; if you change `RESTIC_REPOSITORY`, use a remote backend
(`s3:`, `sftp:`, `rest:`), because any other local path inside the container is not
persistent. A repository on the same disk as the data does not survive the loss of the
node: use a remote repository or copy it off-site.

Standalone, attached to a running core stack (add `-p <name>` if it does not use the
default project name `software`):
```bash
docker compose --project-directory software \
  -f software/docker-compose.attach.yml -f software/backup/docker-compose.backup.yml up -d
```
The overlay never sets volume names of its own for core volumes: merged or attached, it
reads the very volumes the services write, whatever the Compose project name.

## Verifying that a backup can be restored

```bash
sudo bash software/backup/restore.sh --verify            # latest snapshot
sudo bash software/backup/restore.sh --verify <snapshot>
```
Restores the dumps of the snapshot into a temporary directory and checks that each one
loads: `PRAGMA integrity_check` for every SQLite dump, `pg_restore --list` for the
OpenProject dump (needs `postgresql-client`), archive listing plus `gzip -t` of the
database dump for GitLab. It does not talk to Docker and does not touch the running
services. Non-zero exit when any dump listed in the `MANIFEST` does not load, or when the
snapshot holds no dumps at all. Needs free space for one GitLab archive in `$TMPDIR`.

`restore.sh check` runs `restic check` (archive integrity only). Neither replaces a
real restore rehearsal on a spare node.

## Disaster recovery

```bash
sudo bash software/backup/restore.sh list
sudo bash software/backup/restore.sh restore-latest /mnt/restore   # must be an empty directory
sudo bash software/backup/restore.sh plan /mnt/restore             # prints the steps below with real paths
```
`restore-latest` only extracts; it never writes into a live volume. In the extracted
tree, volume files are under `.../<project>_<volume>/_data` (host job) or
`data/<volume>` (container), the configuration is the `software` / `config` directory,
and the dumps are in `smdc-dumps/`.

On a fresh node:

1. Copy the configuration directory back as `software/` (`.env` mode 0600).
2. Create the containers and volumes without starting them:
   `docker compose --env-file software/.env -f software/docker-compose.yml [-f overlays...] create`
3. Copy the static files of each volume back, for example
   `docker run --rm -v software_vaultwarden_data:/to -v /mnt/restore/.../software_vaultwarden_data/_data:/from:ro alpine cp -a /from/. /to/`
4. Load each database from its dump (the service must be stopped unless stated):

| Service | Restore |
|---|---|
| Vaultwarden | copy `sqlite/vaultwarden.sqlite` to `<vaultwarden_data>/db.sqlite3`; delete any `db.sqlite3-wal` / `-shm` first ([wiki](https://github.com/dani-garcia/vaultwarden/wiki/Backing-up-your-vault)) |
| Headscale | copy `sqlite/headscale.sqlite` to `<headscale_data>/db.sqlite`; delete `-wal` / `-shm` |
| Grafana | copy `sqlite/grafana.sqlite` to `<grafana_data>/grafana.db`, owner `472:0` |
| Open WebUI | copy `sqlite/open-webui.sqlite` to `<webui_data>/webui.db`; then re-index knowledge (Qdrant is empty) |
| CrowdSec | copy `sqlite/crowdsec.sqlite` to `<crowdsec_data>/crowdsec.db` |
| DTN spool | copy `sqlite/dtn-spool.sqlite` to `<space_spool_data>/bundles.db`; delete `-wal` / `-shm` |
| Nextcloud | copy `sqlite/nextcloud.sqlite` to `<nextcloud_data>/data/owncloud.db`, owner `33:33`; start; `occ maintenance:data-fingerprint`; `occ files:scan --all` |
| OpenProject | the [official all-in-one restore](https://www.openproject.org/docs/installation-and-operations/operation/restoring/): let the container initialise an empty `openproject_pgdata`, stop it, start a `postgres:<same major version>` container on that volume, `dropdb` / `createdb -O openproject` / `pg_restore -d openproject --no-owner --role=openproject postgres/openproject.pgdump`, stop it, `chown -R 102:102` the volume, start OpenProject. The `MANIFEST` records the `pg_dump` version. `restore.sh plan` prints the commands |
| GitLab | the [official restore](https://docs.gitlab.com/administration/backup_restore/restore_gitlab/): restore `gitlab.rb` and `gitlab-secrets.json` first, start the **same GitLab version**, put the archive in `<gitlab_data>/backups/`, stop `puma` and `sidekiq`, `gitlab-backup restore BACKUP=<id>`, restart, `gitlab-rake gitlab:check SANITIZE=true` |
| SMDC state | copy `smdc-state/` back to `SMDC_STATE_DIR` (0700; wallets and keys 0600) |

5. Start the stack with `./software/setup.sh ...`, pull the Ollama models again and
   let the knowledge indexer rebuild Qdrant.

The OpenProject and GitLab procedures above are the vendors' documented ones adapted to
this stack's volume names; this repository has no automated test that executes them
against real services. Rehearse them before you depend on them.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `RESTIC_REPOSITORY` | `/var/backups/sovereign` | restic repository |
| `RESTIC_PASSWORD` | none (required) | repository password |
| `RESTIC_S3_KEY` / `RESTIC_S3_SECRET` | empty | credentials for an `s3:` repository |
| `BACKUP_INTERVAL_SECONDS` | `86400` | container: pause between runs |
| `BACKUP_DUMP_DIR` | `/var/backups/sovereign-dumps` | host directory for dumps (mounted read-only at `/dumps` in the container) |
| `BACKUP_DUMP_MAX_AGE_SECONDS` | `93600` | container: oldest acceptable dump set |
| `BACKUP_SMDC_STATE_DIR` | `$SMDC_STATE_DIR`, else skipped | SMDC state directory to include |
| `COMPOSE_PROJECT_NAME` | read from `sovereign_traefik`, else `software` | project whose volumes are backed up |
| `GITLAB_BACKUP_ARGS` | empty | extra arguments for `gitlab-backup create` (e.g. `STRATEGY=copy`) |

`.env` is read as data (`KEY=value` lines), not executed.

A service that is not deployed (its volume does not exist) is recorded as `skipped` in
the `MANIFEST` and reported by `--verify`. A deployed service whose dump cannot be taken
(container stopped while its volume holds a database, missing database file, dump tool
error) fails the whole run.

## What has been tested

The dump phase (`backup.sh --dump-only`, run as root on the Docker host) was run on
2026-11-14 against real containers of the pinned images, on an isolated test project:

| Service | Image | Result |
| :--- | :--- | :--- |
| OpenProject | `openproject/community:13.4.1` | `pg_dump` custom-format dump; `pg_restore --list` shows 136 table-data entries |
| GitLab | `gitlab/gitlab-ce:19.5.5-ce.0` | `gitlab-backup create` archive written to the `backups` directory of `gitlab_data` |
| Nextcloud (SQLite) | `nextcloud:34.0.6-apache` | `.backup` of `data/nextcloud.db`; integrity check ok, 132 tables |
| Vaultwarden | `vaultwarden/server:1.37.5` | `.backup` of `db.sqlite3`; integrity check ok |
| Grafana | `grafana/grafana:13.3.1` | `.backup` of `grafana.db`; integrity check ok |
| Headscale | `headscale/headscale:0.23.0` | `.backup` of `db.sqlite`; integrity check ok |

A Nextcloud that has not completed its installation is skipped and reported as such.

Not tested against real services: the Open WebUI, CrowdSec and DTN-spool dumps (their
containers were not part of the test), the restic upload phase, `restore.sh --verify`
against a real repository, and loading any dump back into a live service. Rehearse a
restore before relying on these backups.
