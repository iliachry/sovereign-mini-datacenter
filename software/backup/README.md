# Sovereign Backup & Disaster Recovery

Encrypted, deduplicated snapshots with [Restic](https://restic.net) (AES-256-CTR +
Poly1305-AES; `restic check` detects bit rot in the archive).

A file-level copy of a **running** database is not a backup: pages and write-ahead log
can come from different moments, and the copy may not start. `restic check` cannot see
that, because it verifies the archive, not the database inside it. So `backup.sh` never
hands restic a live database directory. It works in two phases:

1. **Dump** (on the host). Every database is exported with its own tool into
   `BACKUP_DUMP_DIR/smdc-dumps/`, each dump is checked, and a `MANIFEST` records the
   result together with the size and SHA-256 of every dump file. Nextcloud is put into
   maintenance mode while its database is dumped and its volume is copied to
   `BACKUP_DUMP_DIR/smdc-nextcloud-files/`, then switched back on. If any dump fails,
   the run stops with a non-zero exit code **before restic is called**: there is no
   snapshot, rather than a snapshot that looks fine and is not.
2. **Files.** restic backs up the dump directory, the Nextcloud copy, the deployment
   configuration and the static files of the other volumes. Live database files are
   excluded.

## When a run stops without uploading anything

A backup that reports success while covering less than it should is worse than one that
fails. Each of the following stops the run with a non-zero exit code before restic is
called, so nothing is uploaded and nothing is pruned; the previous dump set stays in
place:

| Condition | Why it is not treated as "nothing to back up" |
|---|---|
| The Docker daemon does not answer (`docker version`, `docker volume ls`, `docker ps` fail: daemon stopped, no permission on the socket) | Without the daemon no volume can be found. Earlier versions read that as "no service is deployed", skipped every database and reported success |
| A volume that the daemon lists cannot be inspected, or its mountpoint cannot be read | A volume counts as "not deployed" only when the daemon answers and does not list it |
| A dump tool fails, or a dump does not pass its check | - |
| A service whose dump was `ok` in the previous complete `MANIFEST` would now be `skipped`, or a `files` volume that existed has gone (**coverage drop**) | A stopped or broken service looks exactly like a removed one. The message names the services. If the service was removed on purpose, run once with `BACKUP_ALLOW_COVERAGE_DROP=1`: that run uploads a snapshot, records `coverage|reduced` in the `MANIFEST`, and **does not prune**, so older snapshots that still hold the service are kept. Pruning resumes with the next run |
| The daemon has no volume named `<project>_*` for the resolved Compose project, or no database at all was dumped (every service `skipped`) | A wrong `COMPOSE_PROJECT_NAME` or the wrong host: project-scoped volumes are looked up as `<project>_<volume>`, and with the wrong name every one of them would look "not deployed". `BACKUP_ALLOW_COVERAGE_DROP=1` accepts it |
| A dump file named in the `MANIFEST` is missing, has another size, or (container mode) another SHA-256; the Nextcloud copy has another number of files or belongs to another run | The dump directory was damaged or only partly copied after the dump |
| Nextcloud is already in maintenance mode and this script did not switch it on | An upgrade or an administrator is at work; the script leaves it alone |
| Another dump run holds the lock `BACKUP_DUMP_DIR/.smdc-backup.lock` | A lock whose process no longer exists is taken over with a warning |

The retention step (`restic forget --prune`) runs only after a successful `restic backup`
of a run whose coverage is not reduced.

## What a snapshot contains

| Data | How it is captured | Consistency |
|---|---|---|
| OpenProject database (embedded PostgreSQL) | `pg_dump -d openproject -x -O -Fc` inside the container ([OpenProject docs](https://www.openproject.org/docs/installation-and-operations/operation/backing-up/)); the `openproject_pgdata` volume itself is never read | application-consistent |
| GitLab (database, repositories, uploads, CI artifacts, ...) | `gitlab-backup create` inside the container ([GitLab docs](https://docs.gitlab.com/administration/backup_restore/backup_gitlab/)); only `gitlab_data/backups/` is read, never GitLab's live PostgreSQL/Redis/Gitaly directories | application-consistent |
| GitLab `gitlab.rb`, `gitlab-secrets.json`, SSH host keys | files of the `gitlab_config` volume. GitLab documents that these are **not** in the archive above; without `gitlab-secrets.json` encrypted columns (2FA secrets, CI variables) are unrecoverable | static files |
| Vaultwarden `db.sqlite3` | SQLite online backup, `sqlite3 ... ".backup"` ([Vaultwarden wiki](https://github.com/dani-garcia/vaultwarden/wiki/Backing-up-your-vault)) | application-consistent |
| Nextcloud database (`data/<dbname>.db`; this stack runs Nextcloud on SQLite) | SQLite online backup, taken while Nextcloud is in maintenance mode (default); database type, name and data directory are read through `occ` ([Nextcloud docs](https://docs.nextcloud.com/server/latest/admin_manual/maintenance/backup.html)) | application-consistent |
| Nextcloud volume (`nextcloud_data`: code, `config/`, `custom_apps/`, `themes/`, `data/` with every user file) | default (`BACKUP_NEXTCLOUD_MODE=maintenance`): copied to `smdc-nextcloud-files/volume/` in the same maintenance window as the database dump; restic backs up that copy and does not walk the live volume | **application-consistent with the database** in `maintenance` mode; **best effort** in `online` mode. See "Nextcloud" below |
| Headscale `db.sqlite`, Grafana `grafana.db`, Open WebUI `webui.db`, CrowdSec `crowdsec.db`, DTN spool `bundles.db` (WAL) | SQLite online backup ([sqlite.org](https://www.sqlite.org/backup.html)) | application-consistent |
| SMDC state directory (`BACKUP_SMDC_STATE_DIR`): ledgers and DTN spool / wallets and node keys | SQLite online backup / plain copy | application-consistent / static files |
| Attachments, uploads, keys, certificates: `traefik_certs`, `openproject_data`, `vaultwarden_data`, `webui_data`, `grafana_data`, `headscale_data`, `space_spool_data`, `sovereign_mode`, `crowdsec_data`, `crowdsec_config` | restic file-level, database files excluded | **best effort**: files written during the walk may be captured mid-write |
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

### Nextcloud: database and files from the same moment

Nextcloud's database records every file (name, size, checksum, shares). If the database
is dumped at one moment and the files are copied later, a file renamed or deleted in
between leaves the restored database pointing at files that are not in the snapshot;
`PRAGMA integrity_check` cannot see that. Nextcloud's
[backup procedure](https://docs.nextcloud.com/server/latest/admin_manual/maintenance/backup.html)
therefore starts with maintenance mode, which locks the sessions of logged-in users and
blocks new logins so that the data cannot change underneath the backup, and then copies
the installation directory and dumps the database. `backup.sh` does the same, selected
by `BACKUP_NEXTCLOUD_MODE`:

| | `maintenance` (default) | `online` |
|---|---|---|
| Sequence | `occ maintenance:mode --on`, SQLite `.backup` of the database, copy of the whole volume into `smdc-nextcloud-files/`, `occ maintenance:mode --off`. Runs first in the dump phase, so the window never waits behind another service's dump | SQLite `.backup` only; restic walks the live volume later (minutes later in the host job, up to a day later in the container) |
| Database | consistent | consistent |
| Files against the database | **same window**: what the database describes is what the copy holds, in the host job and in the container alike, because restic backs up the copy and never the live volume | **not guaranteed**: files changed, renamed or deleted between the dump and the walk do not match the database. After a restore, `occ files:scan --all` repairs the file index; shares and versions of files that moved are not recovered |
| Downtime | Nextcloud answers with its maintenance page (HTTP 503) for the length of the window: `BACKUP_NEXTCLOUD_SETTLE_SECONDS` (5 s) + the database backup + the copy. The measured length is written to the log and to the `MANIFEST` (`maintenance mode for N s`). It depends on the copy method below; it has not been measured on real data here | none |
| Extra disk space in `BACKUP_DUMP_DIR` | one copy of the volume, or none with `reflink` | none |

How the copy is made (`BACKUP_NEXTCLOUD_SNAPSHOT`, default `auto` = the first that works):

| Method | What it does | Window | Extra space |
|---|---|---|---|
| `reflink` | `cp -a --reflink=always`: copy-on-write clone. Needs GNU `cp`, and the volume and `BACKUP_DUMP_DIR` on the same Btrfs or reflink-enabled XFS filesystem | seconds to a few minutes (metadata only) | none until files change |
| `rsync` | `rsync -a --link-dest=<previous copy>`: unchanged files become hard links to the **previous copy**, only new and changed files are copied | first run: a full copy; afterwards the time to compare every file plus the changed data | one copy of the volume |
| `copy` | `cp -a`: a full copy on every run | proportional to the size of the volume, every run | one copy of the volume (two while a run is in progress) |

Hard links to the live files (`cp -al`) are deliberately not used: a hard link shares
the file's content, and a file that Nextcloud rewrites in place after the window would
change inside the "snapshot". Before the window opens the script checks that
`BACKUP_DUMP_DIR` has room for a full copy (methods `copy` and first `rsync` run) and
stops if it has not.

Maintenance mode is always switched off again:

- the script records that it owns the maintenance window before it sends `--on`, and an
  exit handler (normal exit, any error, `SIGINT`, `SIGTERM`, `SIGHUP`) sends `--off`,
  three attempts. A signal is handled as soon as the command that is running returns.
  If all three attempts fail the run exits non-zero and prints the command to run by hand;
- **stale lock:** `SIGKILL` or a power cut cannot be handled. The marker file
  `BACKUP_DUMP_DIR/.smdc-nextcloud-maintenance` then remains, and the next run, finding
  Nextcloud in maintenance mode *with* the marker, takes the window over and switches it
  off at its end. Until that run Nextcloud stays in maintenance mode: after a crash of
  the host during a backup, run `occ maintenance:mode --off` or start the backup;
- maintenance mode found *without* the marker was set by someone else (an upgrade, an
  administrator). The script does not touch it and stops; nothing is backed up until it
  has been switched off.

A stopped Nextcloud container is backed up without `occ`: nothing writes, so the database
and the copy are consistent.

Choose `online` when the 503 window is not acceptable or the disk cannot hold a copy of
the volume, and accept that a restore then needs `occ files:scan --all` and may show
files that do not match the database. After any Nextcloud restore run
`occ maintenance:data-fingerprint`
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
`docker`, `sqlite3`, `restic`, one of `sha256sum` / `shasum` / `openssl`, and `rsync`
(not needed when the Nextcloud copy uses `reflink` or `copy`, or in `online` mode).
`RESTIC_PASSWORD` must be set in `software/.env` (or the environment); there is no
default password.

The dump directory (`BACKUP_DUMP_DIR`, default `/var/backups/sovereign-dumps`, mode
0700) holds **plaintext** database dumps and, in `maintenance` mode, a plaintext copy of
the Nextcloud volume with every user file. Keep it on an encrypted disk. Size: one copy
of every SQLite database, one compressed OpenProject dump, and one copy of the Nextcloud
volume (none with `reflink`); GitLab's archives stay in its own volume
(`gitlab_rails['backup_keep_time']` keeps 2 days of them).

Layout of `BACKUP_DUMP_DIR`:

```
smdc-dumps/MANIFEST                 format 2; see below
smdc-dumps/sqlite/<service>.sqlite  SQLite dumps
smdc-dumps/postgres/openproject.pgdump
smdc-dumps/smdc-state/              SMDC state directory (if configured)
smdc-nextcloud-files/SNAPSHOT       creation time of the MANIFEST this copy belongs to
smdc-nextcloud-files/volume/        copy of the nextcloud_data volume, without the live database files
```

`MANIFEST` records, one per line and pipe-separated: `dump|name|kind|path|ok or
skipped|detail` for every service; `file|path|bytes|sha256` for every dump file;
`extfile|volume|path|bytes|sha256` for the GitLab archive; `tree|directory|files` for the
Nextcloud copy; `volume|key|present or absent` for every `files` volume;
`coverage|full` or `coverage|reduced|...`; and `status|complete` as its last line.
Before restic runs, every `ok` dump must exist with the recorded size; in container
mode, where the dumps were written by another process and arrive through a bind mount,
the SHA-256 of every file is recomputed as well (the host job made them seconds earlier
and does not hash them twice). A `MANIFEST` written by the previous version of this
script (format 1, no sizes or digests) is still accepted, with a warning: only the
existence and non-zero size of each dump can then be checked, until the next
`--dump-only` run rewrites it.

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

If the `MANIFEST` is missing, not marked complete, older than
`BACKUP_DUMP_MAX_AGE_SECONDS` (default 26 h), or names a dump file that is missing or
does not have the recorded size and SHA-256, the container's run exits non-zero,
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
Restores the dumps of the snapshot into a temporary directory, compares every restored
dump file (and the GitLab archive) with the size and SHA-256 in the `MANIFEST`, and
checks that each one loads: `PRAGMA integrity_check` for every SQLite dump, `pg_restore --list` for the
OpenProject dump (needs `postgresql-client`), archive listing plus `gzip -t` of the
database dump for GitLab. It does not talk to Docker and does not touch the running
services. Non-zero exit when any dump listed in the `MANIFEST` does not load, or when the
snapshot holds no dumps at all. Needs free space for one GitLab archive in `$TMPDIR`.
The Nextcloud file copy is not restored by `--verify` (it holds every user file): the
number of files the snapshot lists under `smdc-nextcloud-files/volume/` is compared with
the number the dump phase recorded; their contents are not read. A snapshot taken with
`BACKUP_ALLOW_COVERAGE_DROP=1` is reported as such.

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
the dumps are in `smdc-dumps/`, and the Nextcloud volume (in `maintenance` mode) is in
`smdc-nextcloud-files/volume/` next to it instead of among the volumes.

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
| Nextcloud | `maintenance`-mode snapshot: copy `smdc-nextcloud-files/volume/` into the empty `nextcloud_data` volume (`cp -a`), then copy `sqlite/nextcloud.sqlite` to the database path the `MANIFEST` names (`<nextcloud_data>/data/<dbname>.db`, `owncloud.db` by default), owner `33:33`; start; `occ maintenance:mode --off` (the copy was taken while maintenance mode was on, so the restored `config/config.php` still says `'maintenance' => true`); `occ maintenance:data-fingerprint`. `online`-mode snapshot: the volume files come from step 3; then the same database step, and `occ files:scan --all` as well |
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
| `BACKUP_NEXTCLOUD_MODE` | `maintenance` | `maintenance`: database dump and file copy in one maintenance window (Nextcloud answers 503 meanwhile). `online`: no downtime, files not guaranteed to match the database. See "Nextcloud" above |
| `BACKUP_NEXTCLOUD_SNAPSHOT` | `auto` | how the copy of the volume is made: `auto`, `reflink`, `rsync` or `copy` |
| `BACKUP_NEXTCLOUD_SETTLE_SECONDS` | `5` | pause after maintenance mode is switched on, for requests already in progress |
| `BACKUP_ALLOW_COVERAGE_DROP` | unset | `1` accepts, for that run, that a previously backed-up service is now skipped; the run does not prune. Read from the environment only, never from `.env`, so that it cannot stay set |

`.env` is read as data (`KEY=value` lines), not executed.

A service that is not deployed (the Docker daemon answers and does not list its volume)
is recorded as `skipped` in the `MANIFEST` and reported by `--verify`. A deployed service
whose dump cannot be taken (container stopped while its volume holds a database, missing
database file, dump tool error) fails the whole run, and so does a service that was
backed up by the previous run and would now be skipped (see "When a run stops without
uploading anything").

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

After the script gained the Docker-reachability probe, the coverage guard, manifest
checksums and the Nextcloud maintenance window, it was run again the same day against
real OpenProject, GitLab, Nextcloud, Vaultwarden and Grafana containers:

- Nextcloud: maintenance mode was on for 7 s and off again afterwards (`occ
  maintenance:mode` reported "disabled"); the copy held 27,457 files including a file
  written just before the run; copy method `rsync`.
- The manifest was format 2 with a size and SHA-256 record for every dump file.
- With the Docker daemon unreachable the run aborted (exit 1) before writing anything.
- After one service and its volume were removed, the next run aborted with a coverage
  drop naming that service (exit 1).

That run predates the Nextcloud maintenance window, the size and SHA-256 records, the
daemon check and the coverage guard. Those were tested on 2026-11-14 with recording
stand-ins for `docker`, `restic`, `sqlite3`, `cp` and `rsync` (the script itself run
under `dash`), not against real services:

| Case | Result checked |
| :--- | :--- |
| Docker daemon unreachable; a listed volume cannot be inspected; `docker ps` fails | non-zero exit, no `MANIFEST` marked complete, previous dumps untouched, restic never called |
| A service that was backed up is now skipped (volume gone; container stopped on an empty volume; state directory no longer configured) | non-zero exit naming the service, restic never called; with `BACKUP_ALLOW_COVERAGE_DROP=1` the backup runs and `forget --prune` does not; the following run prunes again |
| Container mode: a dump named in the `MANIFEST` is missing, empty, altered with the same size, or altered in size; the GitLab archive or a copied state file is altered; the Nextcloud copy lost a file or belongs to another run | non-zero exit, restic never called |
| Container mode: `MANIFEST` in the previous format | accepted with a warning; a missing or empty dump still stops the run |
| Nextcloud dump command fails; the file copy fails; the script receives `SIGTERM` during the dump | maintenance mode switched on, then off again; non-zero exit; restic never called |
| `occ maintenance:mode --off` keeps failing; next run | non-zero exit with the manual command; the next run takes the stale window over and switches it off |
| Nextcloud in maintenance mode without this script's marker | non-zero exit, maintenance mode left as it was |
| `restic backup` exits 3 | non-zero exit, `forget --prune` never called |
| `BACKUP_NEXTCLOUD_MODE=online`; copy methods `reflink`, `rsync` (with `--link-dest` on the second run) and `copy` | expected commands issued |

Not tested against real services: the Nextcloud maintenance window (`occ` against a real
Nextcloud, the length of the window on real data, `cp --reflink` on Btrfs or XFS), the
Open WebUI, CrowdSec and DTN-spool dumps (their containers were not part of the test),
the restic upload phase, `restore.sh --verify` against a real repository, and loading
any dump back into a live service. Rehearse a restore before relying on these backups.
