# Sovereign Backup & Disaster Recovery

Encrypted, deduplicated snapshots with [Restic](https://restic.net) (AES-256-CTR +
Poly1305-AES; `restic check` detects bit rot in the archive).

A file-level copy of a **running** database is not a backup: pages and write-ahead log
can come from different moments, and the copy may not start. `restic check` cannot see
that, because it verifies the archive, not the database inside it. So `backup.sh` never
hands restic a live database directory. It works in two phases:

1. **Dump** (on the host). Every database is exported with its own tool into a new
   **generation** directory, `BACKUP_DUMP_DIR/generations/<epoch>/smdc-dumps/`, each
   dump is checked, and a `MANIFEST` records the result together with the size and
   SHA-256 of every dump file. Nextcloud is put into maintenance mode while its database
   is dumped and its volume is copied to `.../generations/<epoch>/smdc-nextcloud-files/`,
   then switched back on. If any dump fails, the run stops with a non-zero exit code
   **before restic is called**: there is no snapshot, rather than a snapshot that looks
   fine and is not. A complete generation is published by switching the symlink
   `BACKUP_DUMP_DIR/current` to it (see "Layout of `BACKUP_DUMP_DIR`").
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
| `BACKUP_DUMP_DIR/current` is not a symlink to `generations/<epoch>`, or the `MANIFEST` in a generation names another creation time than the generation's directory | The dump directory was rearranged by hand |

One condition is only visible **after** the upload: the `MANIFEST` is fingerprinted
(size and SHA-256) before it is verified and again after `restic backup` returns. If it
changed or disappeared while restic was reading, the run exits non-zero and does not
prune; the snapshot it just wrote is named in the error, is tagged
`sovereign-incomplete` and is never picked by `restore.sh restore-latest` or `--verify`
(`restore.sh --verify <id>` tells what it holds). The next run or pass backs up the
current generation.

### Complete and incomplete snapshots

`restic backup` writes a snapshot even when it exits 3 because some files could not be
read. Every snapshot therefore gets the tag `sovereign-snapshot` when it is written and is
classified once the outcome is known:

| State | Tag | Set when | `restore.sh` |
|---|---|---|---|
| complete | `sovereign-complete` (together with `dumps-<epoch>-<digest>`) | `restic backup` exited 0 **and** the `MANIFEST` was still the verified one afterwards | `restore-latest` and `--verify` use the newest of these |
| incomplete | `sovereign-incomplete` | `restic backup` exited non-zero after writing a snapshot, or the `MANIFEST` changed or disappeared under it | only by id (`restore-id <id>`, `--verify <id>`), with a warning |
| unclassified | neither | the run was killed between upload and tagging, or the snapshot was written by an earlier `backup.sh` | only by id, with a warning |

Snapshots written before this classification existed: one that carries a
`dumps-<epoch>-<digest>` tag (earlier versions added it only after a clean run) counts as
complete, in `restore.sh` at once and in the repository as soon as the next clean run
has added `sovereign-complete` to it. One without that tag cannot be told from a failed
upload: it is never picked automatically and never removed automatically. Restore it by
id (`restore.sh list`, `restore.sh --verify <id>`, `restore.sh restore-id <id> <target>`)
and remove it yourself with `restic forget <id>` when it is no longer needed.

### Retention

The retention step runs only after a successful `restic backup` of a run whose coverage
is not reduced and whose `MANIFEST` was still the verified one. It is two commands,
both grouped by host only (the dump directory is a different path in every snapshot):

```
restic forget --tag sovereign-incomplete --group-by host --keep-last 3
restic forget --tag sovereign-complete   --group-by host --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --keep-yearly 1 --prune
```

The keep policy counts **complete snapshots only**. Applied to every snapshot it would
keep the newest snapshot of each day, week and month whatever its state, so an incomplete
snapshot taken after a complete one on the same day would be kept in its place. Of the
incomplete snapshots the three most recent are kept (they may hold the only copy of a
file changed since the last complete run); unclassified snapshots are left alone.

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
| Extra disk space in `BACKUP_DUMP_DIR` | the copies of two generations (the published one and the one before it): with `rsync` one copy plus the files that changed between the two, with `copy` two full copies, with `reflink` none until files change | none |

How the copy is made (`BACKUP_NEXTCLOUD_SNAPSHOT`, default `auto` = the first that works):

| Method | What it does | Window | Extra space |
|---|---|---|---|
| `reflink` | `cp -a --reflink=always`: copy-on-write clone. Needs GNU `cp`, and the volume and `BACKUP_DUMP_DIR` on the same Btrfs or reflink-enabled XFS filesystem | seconds to a few minutes (metadata only) | none until files change |
| `rsync` | `rsync -a --link-dest=<previous copy>`: unchanged files become hard links to the **previous copy** (the published generation's), only new and changed files are copied | first run: a full copy; afterwards the time to compare every file plus the changed data | one copy of the volume, plus the files that differ between the two kept generations |
| `copy` | `cp -a`: a full copy on every run | proportional to the size of the volume, every run | two copies of the volume (the two kept generations) |

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
the Nextcloud volume with every user file. Keep it on an encrypted disk. Two generations
are kept (the published one and the one it replaced), so the size is: two copies of
every SQLite database, two compressed OpenProject dumps, and the Nextcloud copies as in
the table above; GitLab's archives stay in its own volume
(`gitlab_rails['backup_keep_time']` keeps 2 days of them).

#### Layout of `BACKUP_DUMP_DIR`

```
current -> generations/<epoch>      relative symlink to the published generation
generations/<epoch>/                one dump run; <epoch> is the MANIFEST's created_epoch
    smdc-dumps/MANIFEST                 format 2; see below
    smdc-dumps/sqlite/<service>.sqlite  SQLite dumps
    smdc-dumps/postgres/openproject.pgdump
    smdc-dumps/smdc-state/              SMDC state directory (if configured)
    smdc-nextcloud-files/SNAPSHOT       creation time of the MANIFEST this copy belongs to
    smdc-nextcloud-files/volume/        copy of the nextcloud_data volume, without the live database files
generations/<epoch>.partial/        a dump run in progress (removed when it fails)
```

How a dump run hands its result to a reader (the backup container may be reading at any
time, and it takes no lock: its mount is read-only):

1. The run writes into `generations/<epoch>.partial/`. Nothing reads that directory.
2. When every dump has succeeded, the directory is renamed to `generations/<epoch>` and
   a new symlink is renamed over `current`. Both are single `rename(2)` calls: a reader
   finds the old generation or the new one, never a mixture, and the database dumps and
   the Nextcloud copy are switched together.
3. A generation is never modified after it has been published.
4. The generation that `current` pointed at before stays on disk until the **next** dump
   run starts. That run deletes it (and anything a killed run left behind) before it
   writes. A reader that resolved `current` just before a switch therefore has a whole
   dump interval to finish on a complete set.
5. A reader resolves `current` **once**, to `generations/<epoch>`, and reads only that
   path. A reader that is still at work when its generation is deleted (it ran longer
   than a whole dump interval) fails: restic reports unreadable files, or the
   fingerprint of the `MANIFEST` taken after the upload does not match the one taken
   before. It exits non-zero and does not prune.

**Upgrading from the fixed-name layout.** Earlier versions kept
`BACKUP_DUMP_DIR/smdc-dumps` and `BACKUP_DUMP_DIR/smdc-nextcloud-files` and swapped them
in place. While `current` does not exist those directories are still read (with a
warning), by the container and as the "previous manifest" of the coverage guard and the
`--link-dest` source of the first new dump run. The first dump run with this version
creates `generations/` and `current` and leaves them alone; the second one removes them.
Nothing has to be migrated by hand.

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
phase only**. The dumps still come from the host, and the host job is the schedule:

```cron
# root crontab: this job decides when a backup is taken
30 2 * * * /bin/sh /path/to/sovereign-mini-datacenter/software/backup/backup.sh --dump-only >> /var/log/sovereign-backup.log 2>&1
```

There is no second clock to align with it. Every `BACKUP_POLL_SECONDS` (default 300) the
container runs one pass of `backup.sh`:

1. It resolves `BACKUP_DUMP_DIR/current` once and fingerprints that generation's
   `MANIFEST`.
2. If the repository already holds a snapshot of this generation, the pass ends with
   exit 0 and uploads nothing: **each generation is uploaded exactly once**. The record
   is the repository itself: a snapshot whose `restic backup` exited 0 and whose
   `MANIFEST` was still the verified one afterwards gets the tag
   `dumps-<epoch>-<first 16 hex digits of the MANIFEST's SHA-256>` and the tag
   `sovereign-complete` (one `restic tag --add ... --add ...`).
   A snapshot of a failed pass (restic writes one even when it exits 3 because files
   were unreadable) never gets them (it is tagged `sovereign-incomplete`), so that
   generation is uploaded again. A small cache file
   inside the container (`/tmp/smdc-backup.uploaded`, never in the dump directory) saves
   asking the repository on every poll; a re-created container has no cache and asks
   once. If the repository cannot answer that question, the generation is uploaded.
3. Otherwise it verifies the generation (below), runs `restic backup` on the resolved
   `generations/<epoch>` directories, fingerprints the `MANIFEST` again, tags the
   snapshot, and only then applies the retention policy.

A dump job that finishes while restic is reading switches `current` to a new generation;
the pass in progress finishes on the generation it started with, and the next pass
uploads the new one. Snapshots are therefore taken a few minutes after each host dump,
whenever that runs.

A pass exits non-zero (nothing is pruned) when the `MANIFEST` is missing, not marked
complete, older than `BACKUP_DUMP_MAX_AGE_SECONDS` (default 26 h; this also holds for a
generation that is already uploaded, so a host job that has stopped is noticed), names a
dump file that is missing or does not have the recorded size and SHA-256, or is no
longer the verified one after the upload. The container logs
`[ERROR] Backup pass failed`, tries again after `BACKUP_POLL_SECONDS`, and becomes
`unhealthy` (`docker ps`) until a pass succeeds. It does not fall back to copying live
databases.

`BACKUP_INTERVAL_SECONDS` of earlier versions is no longer read: remove it from `.env`.
The interval between backups is the schedule of the host job.

**Why the container does not dump by itself.** Dumping needs `docker exec` into the
database containers, which means mounting the Docker socket: root on the host for a
container that is attached to the service network and, with an `s3:`/`sftp:` repository,
talks to the internet. The dump phase therefore stays on the host, where root is
already required, and the container gets read-only mounts and no socket
(`scripts/validate_compose.sh` rejects a socket mount). The cost is a host cron job and
a host directory with plaintext dumps.

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
sudo bash software/backup/restore.sh --verify            # newest COMPLETE snapshot
sudo bash software/backup/restore.sh --verify <snapshot>
```
Without an id, `--verify` (and `restore-latest`) takes the newest snapshot that is marked
complete (see "Complete and incomplete snapshots"), prints its id, and lists every newer
snapshot that is not complete as a warning: those are failed or interrupted runs, and the
backup needs attention. When no snapshot is marked complete it stops instead of falling
back to an unmarked one. With an id it uses that snapshot and warns when it is not
marked complete.

### Which repository the command reads

`RESTIC_REPOSITORY=/var/backups/sovereign` (the default, and the value in
`software/env.example`) names two different places:

| Who runs restic | `/var/backups/sovereign` is |
|---|---|
| the host job `sh backup.sh` (setup A) | a directory on the host |
| the `sovereign_backup` container (setup B) | the named volume `<project>_backup_storage`, mounted there **inside the container**; on the host its files are at the volume's mountpoint (`docker volume inspect <project>_backup_storage`), typically `/var/lib/docker/volumes/<project>_backup_storage/_data` |

`restore.sh` runs on the host. With that default path it asks Docker (read-only:
`docker inspect sovereign_backup`, `docker volume inspect`) where the backup volume is
and then:

- uses the **volume's mountpoint** when only the volume holds a restic repository, and
  says so;
- uses the **host directory** when only that holds one;
- **stops** when both hold one (setups A and B were both used) and asks you to choose:
  ```bash
  sudo RESTIC_REPOSITORY=/var/lib/docker/volumes/<project>_backup_storage/_data bash software/backup/restore.sh --verify   # the container's repository
  sudo RESTIC_REPOSITORY=/var/backups/sovereign bash software/backup/restore.sh --verify                                 # the host job's repository
  ```
- stops when the volume exists but its mountpoint is not a directory it can read: run it
  as root, or, where Docker runs in a VM or on another machine (Docker Desktop, Colima,
  a remote context), use the container method below.

A `RESTIC_REPOSITORY` given in the environment of the command, as above, is always used
exactly as given, and so is any value in `.env` other than the default path (a remote
`s3:`, `sftp:` or `rest:` repository, or another directory). Both ways need the `restic`
binary on the host (`apt-get install restic`), plus `sqlite3` and, for the OpenProject
dump, `postgresql-client`.

**Without restic on the host, or when the volume is not on the host's filesystem:** run
the script in a throwaway container that mounts what `sovereign_backup` mounts. The
restic image has neither `bash` nor `sqlite3`, so they are installed first (`apk add`
needs network access; `coreutils`, `tar` and `gzip` replace the BusyBox applets
`restore.sh` would otherwise use; add `postgresql-client` when the OpenProject overlay
is deployed, for `pg_restore`):

```bash
docker run --rm --entrypoint sh --volumes-from sovereign_backup \
  -e RESTIC_REPOSITORY=/var/backups/sovereign \
  restic/restic:0.19.1 -c 'apk add -q bash sqlite coreutils tar gzip && cd /config/backup && bash ./restore.sh --verify'
```

`--volumes-from sovereign_backup` mounts the repository volume at
`/var/backups/sovereign` and the deployment directory at `/config`, so `restore.sh`
reads `RESTIC_PASSWORD` from `/config/.env` itself: the password is not typed, not
passed with `-e` and not echoed. (If your `.env` is not in that directory, export
`RESTIC_PASSWORD` in your shell without echoing it, e.g. `read -rs RESTIC_PASSWORD &&
export RESTIC_PASSWORD`, and add `-e RESTIC_PASSWORD` - the name only - to the
command.) The container must exist (it may be stopped). To extract instead of verify,
add a writable bind mount for the target, e.g. `-v /mnt/restore:/restore`, and run
`bash ./restore.sh restore-latest /restore`.

`--verify` restores the dumps of the snapshot into a temporary directory, compares every restored
dump file (and the GitLab archive) with the size and SHA-256 in the `MANIFEST`, and
checks that each one loads: `PRAGMA integrity_check` for every SQLite dump, `pg_restore --list` for the
OpenProject dump (needs `postgresql-client`), archive listing plus `gzip -t` of the
database dump for GitLab. It changes nothing through Docker (it only asks where the
backup volume is, see above) and does not touch the running services. Non-zero exit when any dump listed in the `MANIFEST` does not load, or when the
snapshot holds no dumps at all. Needs free space for one GitLab archive in `$TMPDIR`.
The Nextcloud file copy is not restored by `--verify` (it holds every user file): the
number of files the snapshot lists under `smdc-nextcloud-files/volume/` is compared with
the number the dump phase recorded; their contents are not read. A snapshot taken with
`BACKUP_ALLOW_COVERAGE_DROP=1` is reported as such. `--verify` also reports the dump
generation the snapshot holds and fails when the generation directory's name is not the
`created_epoch` of the `MANIFEST` inside it; a snapshot from before dump generations is
reported as "fixed-name directory" and verified as before.

`restore.sh check` runs `restic check` (archive integrity only). Neither replaces a
real restore rehearsal on a spare node.

## Disaster recovery

```bash
sudo bash software/backup/restore.sh list
sudo bash software/backup/restore.sh restore-latest /mnt/restore   # must be an empty directory
sudo bash software/backup/restore.sh plan /mnt/restore             # prints the steps below with real paths
```
`list` shows every backup snapshot with its state (complete, incomplete, unclassified).
`restore-latest` extracts the newest **complete** snapshot and names it; to extract
another one, incomplete ones included, use `restore.sh restore-id <id> /mnt/restore`.
The repository is found as described under "Which repository the command reads".
`restore-latest` only extracts; it never writes into a live volume. In the extracted
tree, volume files are under `.../<project>_<volume>/_data` (host job) or
`data/<volume>` (container), the configuration is the `software` / `config` directory,
the dumps are in `.../generations/<epoch>/smdc-dumps/` (directly in `smdc-dumps/` in a
snapshot from before dump generations), and the Nextcloud volume (in `maintenance` mode)
is in `smdc-nextcloud-files/volume/` next to it instead of among the volumes. `plan`
finds both by name and prints the real paths and the generation.

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
| `BACKUP_POLL_SECONDS` | `300` | container: seconds between looks at `BACKUP_DUMP_DIR/current` for a generation that is not in the repository yet. Not the backup interval: that is the schedule of the host's `--dump-only` job |
| `BACKUP_DUMP_DIR` | `/var/backups/sovereign-dumps` | host directory for the dump generations (mounted read-only at `/dumps` in the container) |
| `BACKUP_DUMP_MAX_AGE_SECONDS` | `93600` | container: oldest acceptable dump set, uploaded or not; the health check allows twice this plus one hour without a finished pass |
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
| `restic backup` exits 3 | non-zero exit, `forget --prune` never called; the snapshot restic wrote is tagged `sovereign-incomplete` |
| `BACKUP_NEXTCLOUD_MODE=online`; copy methods `reflink`, `rsync` (with `--link-dest` on the second run) and `copy` | expected commands issued |
| Dump generations: three dump runs in a row; a failing run; leftovers of a killed run | `current` is a relative symlink to `generations/<epoch>`; the previous generation is kept and the one before it removed; a failed run leaves `current` and the published generation untouched and no `.partial` directory |
| Container: two passes on one generation; cache file removed; repository query failing | one `restic backup` only; with the cache gone the repository is asked and the generation is not uploaded again; when the query fails the generation is uploaded |
| A host dump run executes while the container's `restic backup` is reading (the stand-in reads the `MANIFEST`, runs the dump job, then reads the dump files) | exit 0; the snapshot holds the `MANIFEST` and dump files of the generation the pass started with; the next pass uploads the new generation |
| Two host dump runs during one container pass (its generation is deleted mid-read); the generation vanishes right after `restic backup` returned 0 | non-zero exit, `forget --prune` not called, generation not recorded as uploaded; the following pass succeeds |
| Fixed-name layout of earlier versions: container pass; the directory swapped in place during the pass; host run with a coverage drop; two dump runs after the upgrade | accepted with a warning and uploaded once; the swap is detected after the upload (non-zero exit, no prune); the drop is detected against the old `MANIFEST`; the first run keeps the old directories, the second removes them |
| `current` is a directory, points outside `generations/`, dangles, or its generation's `MANIFEST` names another creation time | non-zero exit, nothing uploaded |
| Container: `restic backup` exits 3 (a snapshot is written all the same); `restic tag` fails after a good upload | non-zero exit, no prune, the snapshot does not carry the generation's tag; the next pass uploads the generation again and tags it, the pass after that uploads nothing |
| Snapshot classification (2026-11-15): a clean run; a run whose `restic backup` exits 3; the `MANIFEST` vanishing after the upload; snapshots of an earlier `backup.sh` with and without a `dumps-` tag; four failing runs followed by a clean one | the clean run's snapshot is tagged `sovereign-complete` and `dumps-…` in one `restic tag` call after the upload; the others are tagged `sovereign-incomplete`; a legacy snapshot with a `dumps-` tag gets `sovereign-complete` once, one without stays untouched; `forget` is issued as the two commands above and never with `--tag sovereign-snapshot`; three incomplete snapshots remain |
| `restore.sh` (2026-11-15): a complete snapshot followed by an incomplete one; a legacy repository; only untagged snapshots; an empty repository; a snapshot named by id | `--verify` and `restore-latest` restore the complete snapshot, name it and warn about the newer one; a `dumps-`-only snapshot counts as complete; with no complete snapshot both stop without restoring; `restore-id` / `--verify <id>` of an incomplete or untagged snapshot proceed with a warning; `list` shows the states |
| `restore.sh` repository detection (2026-11-15), with a stand-in for `docker` and the default path pointed into a sandbox: repository only in the volume; only on the host; in both; volume not visible; volume unreadable; neither; Docker down; `RESTIC_REPOSITORY` in the environment; a remote repository in `.env` | the volume's mountpoint is used and announced; the host directory is used; the script stops and names both; it stops and points at the container method; it stops and asks for root; it stops; the path is used as it is; the given value is used and Docker is not asked (last two) |

The generation hand-off was then run with a real restic 0.19.1 repository (Vaultwarden
and Grafana as the services): the host job published `generations/<epoch>` through
`current`; the backup container uploaded that generation once across three polls and
tagged the snapshot `dumps-<epoch>-<manifest digest>`; a second host run produced a
second generation and exactly one more snapshot; and `restore.sh --verify` restored
the dumps of the latest snapshot from the repository and loaded both databases.

The classification was also run on 2026-11-15 against a real restic repository (restic
0.18.1 built from source; the commands were issued by hand, not by `backup.sh`): a
complete snapshot, then two whose `restic backup` exited 3 because a file was unreadable
and whose dump had been damaged. `restic tag --add … --add …`, `restic tag --add … <id>`
and the JSON reader in both scripts behaved as the stand-in tests assume;
`restore.sh --verify` and `restore-latest` chose the complete snapshot (the previous
`restore.sh` verified the damaged one and failed, and extracted a snapshot without the
unreadable file); and with `--keep-daily 2` the old `forget --tag sovereign-snapshot`
would have removed the complete snapshot in favour of the incomplete one of the same
day, while the new command keeps it.

Not verified: the throwaway-container command above as written (it was run by the
maintainer on a real host with `-e RESTIC_PASSWORD` passed explicitly; the variant that
lets `restore.sh` read `/config/.env` and the `postgresql-client` addition were not
re-run), and the repository detection against a real Docker daemon.

Not tested against real services: the Open WebUI, CrowdSec and DTN-spool dumps (their
containers were not part of the tests), `cp --reflink` on a copy-on-write filesystem,
retention (`forget --prune`) over many days, a remote (S3/SFTP) repository, and loading
any dump back into a live service. Rehearse a restore before relying on these backups.