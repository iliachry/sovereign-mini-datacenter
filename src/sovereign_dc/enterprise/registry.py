"""Sovereign Mini Datacenter — Enterprise Application Registry & Scaffolding.

Provides registry management, directory discovery, schema validation, and turnkey
manifest scaffolding for enterprise application onboarding.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shlex
import shutil
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:  # POSIX advisory locks; absent on Windows (see ``EnterpriseRegistry._exclusive_lock``).
    import fcntl
except ImportError:  # pragma: no cover - exercised by patching ``fcntl`` to None
    fcntl = None  # type: ignore[assignment]

from sovereign_dc.enterprise.schema import (
    AppCategory,
    AppManifest,
    HealthProbe,
    NetworkPolicy,
    PowerPolicy,
    PowerPriority,
    ResourceQuotas,
    RuntimeType,
    StoragePolicy,
)

logger = logging.getLogger("smdc.enterprise.registry")

# Default system directories searched for enterprise app manifests
DEFAULT_APP_DIRECTORIES: list[Path] = [
    Path("/etc/smdc/apps"),
    Path.home() / ".smdc" / "apps",
    Path("./apps"),
    Path("./software/enterprise/apps"),
]

# Fallback lock (platforms without ``fcntl``): how long to wait for the exclusive lock file,
# and the age after which a lock file is taken to be left over from a crashed writer.
LOCK_TIMEOUT_SECONDS = 30.0
LOCK_STALE_SECONDS = 60.0
LOCK_POLL_SECONDS = 0.05
_FCNTL_UNSET = object()


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                get_last_error = getattr(ctypes, "GetLastError", lambda: 0)
                return bool(get_last_error() == 5)
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return bool(exit_code.value == 259)
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    if os.name == "posix" and hasattr(os, "kill"):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    return False


def _is_token_owner_alive(raw: bytes) -> bool:
    """True when a lock/reap marker's ``pid[:tid:uuid]`` owner is still alive."""
    text = raw.decode("utf-8", errors="ignore").strip().splitlines()
    if not text:
        return False
    parts = text[0].split(":")
    try:
        pid = int(parts[0])
    except (ValueError, IndexError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        if len(parts) < 2:
            return False
        try:
            tid = int(parts[1])
        except ValueError:
            return False
        return any(t.ident == tid and t.is_alive() for t in threading.enumerate())
    return _is_pid_alive(pid)


def _claim_reap_file(
    base_reap: Path,
    timeout_s: float,
    stale_seconds: float,
    poll_seconds: float,
    owner_token: bytes = b"",
) -> Path | None:
    """Claims an exclusive ``.reap`` marker via ``O_EXCL``, advancing only past dead/expired ones."""
    candidate = base_reap
    hop = 0
    claim_deadline = time.monotonic() + max(timeout_s, 0.0)
    while True:
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                reap_st = candidate.stat()
                reap_bytes = candidate.read_bytes()
            except OSError:
                if time.monotonic() >= claim_deadline:
                    return None
                time.sleep(min(poll_seconds, 0.005))
                continue
            age = time.time() - reap_st.st_mtime
            if age >= max(stale_seconds, 5.0) and hop < 3 and not _is_token_owner_alive(reap_bytes):
                hop += 1
                candidate = base_reap.with_name(f"{base_reap.name}.r{hop}")
                continue
            if time.monotonic() >= claim_deadline:
                return None
            time.sleep(min(poll_seconds, 0.005))
            continue
        try:
            if owner_token:
                with os.fdopen(fd, "wb") as rf:
                    rf.write(owner_token)
                    rf.flush()
            else:
                os.close(fd)
        except Exception:
            with contextlib.suppress(OSError):
                candidate.unlink()
            raise
        with contextlib.suppress(OSError):
            os.utime(candidate, None)
        return candidate


def _cleanup_reap_family(base_reap: Path) -> None:
    with contextlib.suppress(OSError):
        for marker in base_reap.parent.glob(f"{base_reap.name}*"):
            with contextlib.suppress(OSError):
                marker.unlink()


@contextlib.contextmanager
def exclusive_file_lock(
    lock_path: Path,
    *,
    use_fcntl: Any = _FCNTL_UNSET,
    stale_seconds: float | None = None,
    timeout_seconds: float | None = None,
    poll_seconds: float | None = None,
) -> Iterator[None]:
    """Cross-process exclusive lock at ``lock_path`` (``flock`` on POSIX, token ``O_EXCL`` otherwise)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    active_fcntl = fcntl if use_fcntl is _FCNTL_UNSET else use_fcntl
    if active_fcntl is not None and ("fcntl" not in sys.modules or sys.modules["fcntl"] is not None):
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            active_fcntl.flock(fd, active_fcntl.LOCK_EX)
            try:
                yield
            finally:
                active_fcntl.flock(fd, active_fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return

    stale_s = LOCK_STALE_SECONDS if stale_seconds is None else stale_seconds
    timeout_s = LOCK_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    poll_s = LOCK_POLL_SECONDS if poll_seconds is None else poll_seconds

    excl_path = lock_path.with_name(f"{lock_path.name}.excl")
    token_bytes = f"{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex}\n".encode()
    my_tag = hashlib.sha256(token_bytes).hexdigest()[:16]
    staging_path = lock_path.with_name(f"{lock_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    st_fd = os.open(staging_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(st_fd, "wb") as sf:
            sf.write(token_bytes)
            sf.flush()
    except Exception:
        with contextlib.suppress(OSError):
            staging_path.unlink()
        raise

    acquired = False
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                try:
                    os.link(staging_path, excl_path)
                except FileExistsError:
                    raise
                except OSError:
                    fd = os.open(excl_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(token_bytes)
                        f.flush()
                else:
                    with contextlib.suppress(OSError):
                        staging_path.unlink()
                with contextlib.suppress(OSError):
                    os.utime(excl_path, None)
                acquired = True
                break
            except FileExistsError:
                try:
                    stale_st = excl_path.stat()
                    stale_bytes = excl_path.read_bytes()
                except FileNotFoundError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"registry lock {excl_path} is held by another process") from None
                    continue  # released between calls: retry until deadline
                except OSError as err:
                    if os.name == "nt" and getattr(err, "winerror", None) in (5, 32, 33):
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"registry lock {excl_path} is held by another process") from err
                        time.sleep(poll_s)
                        continue
                    raise
                age = time.time() - stale_st.st_mtime
                if age >= stale_s and not _is_token_owner_alive(stale_bytes):
                    stale_tag = hashlib.sha256(stale_bytes).hexdigest()[:16]
                    base_reap = lock_path.with_name(f"{lock_path.name}.reap.{stale_tag}")
                    claimed = _claim_reap_file(
                        base_reap, max(deadline - time.monotonic(), 0.0), stale_s, poll_s, token_bytes
                    )
                    if claimed is None:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"registry lock {excl_path} is held by another process") from None
                        continue
                    try:
                        try:
                            cur_st = excl_path.stat()
                            cur_bytes = excl_path.read_bytes()
                        except OSError:
                            cur_st = None
                            cur_bytes = b""
                        if (
                            cur_st is not None
                            and cur_bytes == stale_bytes
                            and (cur_st.st_ino, cur_st.st_mtime_ns) == (stale_st.st_ino, stale_st.st_mtime_ns)
                            and (time.time() - cur_st.st_mtime) >= stale_s
                            and not _is_token_owner_alive(cur_bytes)
                        ):
                            logger.warning("Removing stale registry lock %s (%.0f s old)", excl_path, age)
                            os.replace(staging_path, excl_path)
                            with contextlib.suppress(OSError):
                                os.utime(excl_path, None)
                            acquired = True
                            break
                    finally:
                        _cleanup_reap_family(base_reap)
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"registry lock {excl_path} is held by another process") from None
                time.sleep(poll_s)
        yield
    finally:
        with contextlib.suppress(OSError):
            staging_path.unlink()
        if acquired:
            my_base_reap = lock_path.with_name(f"{lock_path.name}.reap.{my_tag}")
            claimed_rel = _claim_reap_file(my_base_reap, max(stale_s, 5.0), stale_s, poll_s, token_bytes)
            try:
                if claimed_rel is not None:
                    with contextlib.suppress(OSError):
                        if excl_path.read_bytes() == token_bytes:
                            excl_path.unlink()
            finally:
                if claimed_rel is not None:
                    _cleanup_reap_family(my_base_reap)


# Files written by ``EnterpriseRegistry.create_project_scaffold`` (manifests always, the sample
# code only with ``create_sample_code``).
SCAFFOLD_MANIFEST_FILES: tuple[str, ...] = ("smdc-app.json", "smdc-app.yaml")
SCAFFOLD_SAMPLE_FILES: tuple[str, ...] = ("app.py", "Dockerfile", "README.md")


class ScaffoldConflictError(FileExistsError):
    """Raised when scaffolding would overwrite files that already exist in the target directory."""

    def __init__(self, target_dir: Path, existing: list[Path]) -> None:
        self.target_dir = target_dir
        self.existing = list(existing)
        names = ", ".join(p.name for p in self.existing)
        super().__init__(f"refusing to overwrite existing file(s) in {target_dir}: {names}")


_SET = "set"
_DELETE = "delete"


class EnterpriseRegistry:
    """Registry managing discovered and installed enterprise applications.

    The registry file is shared by every ``smdc`` process on the node. A mutation is
    therefore never written from this instance's earlier view of the file: ``save_registry``
    takes an exclusive inter-process lock, reloads the file, applies only this instance's
    own pending changes (registrations / removals made since its last save) and replaces the
    file atomically. Two overlapping ``smdc app register`` runs both end up in the file.
    """

    def __init__(self, registry_file: Path | None = None) -> None:
        self.registry_file = registry_file or (Path.home() / ".smdc" / "enterprise_registry.json")
        self._apps: dict[str, AppManifest] = {}
        self._app_paths: dict[str, Path] = {}
        # Registry entries that could not be parsed; written back verbatim on save so one
        # bad entry never causes data loss for itself or its neighbours.
        self._unparsed_entries: list[Any] = []
        # Set when the registry file is unreadable AND could not be backed up: saving is
        # then refused so the only copy of the operator's data is never overwritten.
        self._save_blocked = False
        # Changes made through this instance that are not in the file yet, in order:
        # (_SET, app_id, manifest, manifest_path | None) or (_DELETE, app_id, None, None).
        self._pending: list[tuple[str, str, AppManifest | None, Path | None]] = []
        # (mtime_ns, size, ino) and backup path of the unparseable file already copied aside,
        # so re-reading the same broken file (every save reloads) does not copy it again.
        self._corrupt_backup: tuple[tuple[int, int, int], Path] | None = None
        # Why the most recent save_registry() call failed; None after a successful one.
        self.last_save_error: str | None = None
        # (mtime_ns, size, ino) of the registry file when this instance last read or saved it;
        # lookups re-read the file when it differs, so a long-running process sees changes made
        # by another `smdc` process.
        self._loaded_signature: tuple[int, int, int] | None = None
        self.load_registry()

    def _file_signature(self) -> tuple[int, int, int] | None:
        """(mtime_ns, size, ino) of the registry file, or None when it cannot be read."""
        try:
            stat = self.registry_file.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size, stat.st_ino

    def refresh(self) -> None:
        """Re-reads the registry file if it changed since this instance last read it.

        This instance's own unsaved changes are kept on top (see ``load_registry``).
        """
        signature = self._file_signature()
        if signature != self._loaded_signature:
            if signature is None:
                self._loaded_signature = None
            else:
                self.load_registry()

    def _backup_unreadable_registry(self) -> Path | None:
        """Copies an unparseable registry file aside. Returns the backup path, or None on failure."""
        backup = self.registry_file.with_name(f"{self.registry_file.name}.corrupt-{int(time.time())}")
        counter = 0
        while backup.exists():
            counter += 1
            backup = self.registry_file.with_name(f"{self.registry_file.name}.corrupt-{int(time.time())}-{counter}")
        try:
            shutil.copy2(self.registry_file, backup)
            return backup
        except OSError as e:
            logger.error("Could not back up unreadable registry %s: %s", self.registry_file, e)
            return None

    @contextlib.contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        """Holds the registry's inter-process write lock for the duration of the block.

        POSIX: ``flock(LOCK_EX)`` on ``<registry>.lock`` (released by the kernel if the
        holder dies; the lock file itself is left in place on purpose, removing it would let
        two writers lock different inodes). Without ``fcntl``: an ownership-safe ``O_EXCL``
        lock file ``<registry>.lock.excl`` whose stale takeover and release both serialize on a
        per-token ``.reap.<tag>`` claim; waiting longer than ``LOCK_TIMEOUT_SECONDS`` raises
        ``TimeoutError``.
        """
        lock_path = self.registry_file.with_name(f"{self.registry_file.name}.lock")
        with exclusive_file_lock(
            lock_path,
            use_fcntl=fcntl,
            stale_seconds=LOCK_STALE_SECONDS,
            timeout_seconds=LOCK_TIMEOUT_SECONDS,
            poll_seconds=LOCK_POLL_SECONDS,
        ):
            yield

    def _read_registry_file(self) -> tuple[dict[str, AppManifest], dict[str, Path], list[Any]] | None:
        """Parses the registry file into (apps, manifest paths, unparsed entries).

        A missing file is an empty registry. Returns None if the file cannot be parsed at
        all; it is then copied to ``<name>.corrupt-<timestamp>`` and ``_save_blocked`` says
        whether that backup failed.
        """
        apps: dict[str, AppManifest] = {}
        paths: dict[str, Path] = {}
        unparsed: list[Any] = []
        read_sig: tuple[int, int, int] | None = None
        try:
            with open(self.registry_file, encoding="utf-8") as f:
                st = os.fstat(f.fileno())
                read_sig = (st.st_mtime_ns, st.st_size, st.st_ino)
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("apps", []), list):
                raise ValueError("registry root must be an object with an 'apps' list")
        except FileNotFoundError:
            self._save_blocked = False
            self._loaded_signature = None
            return apps, paths, unparsed
        except Exception as e:
            backup: Path | None = None
            signature = read_sig if read_sig is not None else self._file_signature()
            self._loaded_signature = signature
            known = self._corrupt_backup
            if known is not None and signature == known[0] and known[1].exists():
                backup = known[1]
            else:
                backup = self._backup_unreadable_registry()
                self._corrupt_backup = (signature, backup) if backup is not None and signature is not None else None
            self._save_blocked = backup is None
            logger.error(
                "Registry cache %s is unreadable (%s); %s",
                self.registry_file,
                e,
                f"original preserved at {backup}" if backup else "saving is disabled to protect it",
            )
            return None
        self._save_blocked = False
        self._loaded_signature = read_sig

        for index, app_data in enumerate(data.get("apps", [])):
            try:
                if not isinstance(app_data, dict):
                    raise ValueError("entry is not an object")
                manifest = AppManifest.from_dict(app_data)
                if not isinstance(manifest.app_id, str) or not manifest.app_id:
                    raise ValueError("entry has no app_id")
                apps[manifest.app_id] = manifest
                manifest_path = app_data.get("manifest_path")
                if isinstance(manifest_path, str) and os.path.exists(manifest_path):
                    paths[manifest.app_id] = Path(manifest_path)
            except Exception as e:
                unparsed.append(app_data)
                logger.warning(
                    "Skipping corrupt entry #%d in registry cache %s: %s (entry preserved)",
                    index,
                    self.registry_file,
                    e,
                )
        return apps, paths, unparsed

    def _apply_pending(self) -> None:
        """Replays this instance's unsaved changes on top of ``_apps`` / ``_app_paths``."""
        for op, app_id, manifest, manifest_path in self._pending:
            if op == _SET and manifest is not None:
                self._apps[app_id] = manifest
                if manifest_path is not None:
                    self._app_paths[app_id] = manifest_path
            elif op == _DELETE:
                self._apps.pop(app_id, None)
                self._app_paths.pop(app_id, None)

    def load_registry(self) -> None:
        """Replaces this instance's view with the registry file plus its own unsaved changes.

        Entries are parsed independently: a corrupt entry is skipped (and preserved for the
        next save) without affecting the others. A file that cannot be parsed at all is
        copied to ``<name>.corrupt-<timestamp>`` before any later save may replace it; the
        instance then keeps the view it had (empty for a new instance).
        """
        loaded = self._read_registry_file()
        if loaded is None:
            return
        self._apps, self._app_paths, self._unparsed_entries = loaded
        self._apply_pending()
        logger.debug("Loaded %d apps from registry cache %s", len(self._apps), self.registry_file)

    def save_registry(self) -> bool:
        """Merges this instance's unsaved changes into the registry file (atomic replace).

        Read-modify-write under the exclusive inter-process lock: the file is reloaded, the
        pending changes are applied to that current state and the result replaces the file,
        so registrations saved by other processes in the meantime are kept. Returns True if
        the file was written. On failure the changes stay pending for the next save and
        ``last_save_error`` says why (lock timeout, unwritable directory, unreadable file).
        """
        tmp_name: str | None = None
        self.last_save_error = None
        try:
            with self._exclusive_lock():
                self.load_registry()
                if self._save_blocked:
                    self.last_save_error = (
                        f"registry file {self.registry_file} failed to parse and could not be backed up; "
                        "repair or move the file, then retry"
                    )
                    logger.error("Not saving registry: %s", self.last_save_error)
                    return False
                apps_payload: list[Any] = []
                for app_id, manifest in self._apps.items():
                    app_dict = manifest.to_dict()
                    if app_id in self._app_paths:
                        app_dict["manifest_path"] = str(self._app_paths[app_id])
                    apps_payload.append(app_dict)
                apps_payload.extend(self._unparsed_entries)
                fd, tmp_name = tempfile.mkstemp(
                    prefix=f".{self.registry_file.name}.", suffix=".tmp", dir=self.registry_file.parent
                )
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"version": "1.0.0", "apps": apps_payload}, f, indent=2)
                    f.flush()
                    st = os.fstat(f.fileno())
                    saved_sig = (st.st_mtime_ns, st.st_size, st.st_ino)
                os.replace(tmp_name, self.registry_file)
                tmp_name = None
                self._pending.clear()
                self._loaded_signature = saved_sig
            logger.debug("Saved %d apps to %s", len(self._apps), self.registry_file)
            return True
        except Exception as e:
            self.last_save_error = f"registry file {self.registry_file} could not be written: {e or type(e).__name__}"
            logger.error("Failed to save registry cache %s: %s", self.registry_file, e)
            return False
        finally:
            if tmp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)

    def discover_apps(self, search_dirs: list[Path] | None = None) -> list[AppManifest]:
        """Scan directories for `smdc-app.yaml` / `smdc-app.json` manifests.

        Returns:
            List of successfully discovered and validated AppManifest objects.
        """
        dirs = search_dirs or DEFAULT_APP_DIRECTORIES
        discovered_map: dict[str, AppManifest] = {}

        for d in dirs:
            if not d.exists() or not d.is_dir():
                continue
            for pattern in ["smdc-app.yaml", "smdc-app.yml", "smdc-app.json", "**/smdc-app.yaml", "**/smdc-app.json"]:
                for match in d.glob(pattern):
                    manifest = self.load_manifest_file(match)
                    if manifest and manifest.app_id not in discovered_map:
                        self.register_app(manifest, manifest_path=match, persist=False)
                        discovered_map[manifest.app_id] = manifest

        self.save_registry()
        return list(discovered_map.values())

    def load_manifest_file(self, path: Path | str) -> AppManifest | None:
        """Parse and validate a manifest file from disk (YAML or JSON)."""
        p = Path(path)
        if not p.exists():
            logger.error("Manifest file not found: %s", p)
            return None

        try:
            content = p.read_text(encoding="utf-8")
            if p.suffix in [".yaml", ".yml"]:
                data = self._parse_yaml(content)
            else:
                data = json.loads(content)

            manifest = AppManifest.from_dict(data)
            errors = manifest.validate()
            if errors:
                logger.error("Validation failed for %s: %s", p, "; ".join(errors))
                return None
            return manifest
        except Exception as e:
            logger.error("Error reading manifest %s: %s", p, e)
            return None

    def register_app(
        self, manifest: AppManifest, manifest_path: Path | str | None = None, persist: bool = True
    ) -> tuple[bool, list[str]]:
        """Register an enterprise application in the local registry.

        With ``persist`` the registration only succeeds if the registry file was written: when
        the save fails (lock timeout, unwritable directory, unreadable file) the registration
        is taken back from this instance as well and ``(False, [<reason>])`` is returned, so
        a caller is never told "registered" about an app that no other process can see.
        """
        errors = manifest.validate()
        if errors:
            return False, errors

        path = Path(manifest_path) if manifest_path else None
        app_id = manifest.app_id
        had_app, previous_app = app_id in self._apps, self._apps.get(app_id)
        had_path, previous_path = app_id in self._app_paths, self._app_paths.get(app_id)
        entry = (_SET, app_id, manifest, path)
        self._pending.append(entry)
        self._apps[app_id] = manifest
        if path is not None:
            self._app_paths[app_id] = path

        if persist and not self.save_registry():
            reason = self.last_save_error or f"registry file {self.registry_file} could not be written"
            self._discard_pending(entry)
            self._restore_entry(app_id, had_app, previous_app, had_path, previous_path)
            logger.error("Not registering '%s': %s", app_id, reason)
            return False, [f"registration was not saved: {reason}"]
        logger.info("Registered enterprise application '%s' (%s)", manifest.name, app_id)
        return True, []

    def _discard_pending(self, entry: tuple[str, str, AppManifest | None, Path | None]) -> None:
        """Drops one unsaved change (by identity) from the pending list."""
        self._pending = [item for item in self._pending if item is not entry]

    def _restore_entry(
        self,
        app_id: str,
        had_app: bool,
        previous_app: AppManifest | None,
        had_path: bool,
        previous_path: Path | None,
    ) -> None:
        """Puts this instance's view of one app back to what it was before a failed change."""
        if had_app and previous_app is not None:
            self._apps[app_id] = previous_app
        else:
            self._apps.pop(app_id, None)
        if had_path and previous_path is not None:
            self._app_paths[app_id] = previous_path
        else:
            self._app_paths.pop(app_id, None)

    def unregister_app(self, app_id: str, persist: bool = True) -> bool:
        """Unregister an application from the registry.

        The registry file is re-read first, so an app registered by another process since
        this instance loaded can be removed, and one already removed there reports False.
        With ``persist``, a removal that could not be saved is taken back and reports False
        too; ``last_save_error`` then holds the reason (it is None for "not registered").
        """
        self.last_save_error = None
        self.load_registry()
        if app_id in self._apps:
            previous_app = self._apps[app_id]
            had_path, previous_path = app_id in self._app_paths, self._app_paths.get(app_id)
            entry: tuple[str, str, AppManifest | None, Path | None] = (_DELETE, app_id, None, None)
            self._pending.append(entry)
            del self._apps[app_id]
            self._app_paths.pop(app_id, None)
            if persist and not self.save_registry():
                self._discard_pending(entry)
                self._restore_entry(app_id, True, previous_app, had_path, previous_path)
                logger.error("Not unregistering '%s': %s", app_id, self.last_save_error)
                return False
            logger.info("Unregistered enterprise app %s", app_id)
            return True
        return False

    def get_app(self, app_id: str) -> AppManifest | None:
        """Retrieve manifest for a registered app by ID (re-reading the file if it changed)."""
        self.refresh()
        return self._apps.get(app_id)

    def get_app_path(self, app_id: str) -> Path | None:
        """Get filesystem path to registered manifest."""
        return self._app_paths.get(app_id)

    def list_apps(self) -> list[AppManifest]:
        """Return all registered application manifests (re-reading the file if it changed)."""
        self.refresh()
        return list(self._apps.values())

    @staticmethod
    def _parse_yaml(content: str) -> dict[str, Any]:
        """Parse YAML content using PyYAML if present, or fallback simple parser."""
        try:
            import yaml

            parsed = yaml.safe_load(content)
            if isinstance(parsed, dict):
                return parsed
        except ImportError:
            pass

        # Fallback basic JSON or simple line parser
        try:
            return json.loads(content)
        except Exception:
            pass

        # Lightweight rudimentary YAML parser for core key-values
        result: dict[str, Any] = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if v.isdigit():
                    result[k] = int(v)
                elif v.lower() == "true":
                    result[k] = True
                elif v.lower() == "false":
                    result[k] = False
                else:
                    result[k] = v
        return result

    @classmethod
    def scaffold_manifest(
        cls,
        name: str,
        app_id: str,
        category: AppCategory = AppCategory.CUSTOM,
        runtime: RuntimeType = RuntimeType.PROCESS,
        entrypoint: str = "python3 app.py",
        gpu_required: bool = False,
        power_tier: PowerPriority = PowerPriority.L1_STANDARD,
        ports: list[int] | None = None,
    ) -> AppManifest:
        """Generate a pre-configured turnkey manifest template for a given archetype."""
        if category == AppCategory.IOT:
            resources = ResourceQuotas(
                cpu_cores=0.5, ram_mb=256, gpu_vram_mb=0, storage_mb=512, gpu_required=False, max_power_w=8.0
            )
            power = PowerPolicy(tier=PowerPriority.L0_CRITICAL, min_battery_soc=15.0, min_solar_watts=0.0)
            network = NetworkPolicy(ports=ports or [8081], expose_wireguard=True, lora_heartbeat=True)
            health = HealthProbe(type="http", endpoint="/health", port=8081)
        elif category == AppCategory.AI_INFERENCE:
            resources = ResourceQuotas(
                cpu_cores=4.0,
                ram_mb=4096,
                gpu_vram_mb=4096,
                storage_mb=8192,
                gpu_required=True,
                max_power_w=65.0,
            )
            power = PowerPolicy(
                tier=power_tier if power_tier != PowerPriority.L1_STANDARD else PowerPriority.L2_BACKGROUND,
                min_battery_soc=45.0,
                min_solar_watts=300.0,
            )
            network = NetworkPolicy(ports=ports or [8000], expose_wireguard=True)
            health = HealthProbe(type="http", endpoint="/v1/health", port=8000)
        elif category == AppCategory.SPATIAL_MEDIA:
            resources = ResourceQuotas(
                cpu_cores=2.0,
                ram_mb=2048,
                gpu_vram_mb=2048,
                storage_mb=4096,
                gpu_required=gpu_required,
                max_power_w=40.0,
            )
            power = PowerPolicy(tier=PowerPriority.L1_STANDARD, min_battery_soc=30.0)
            network = NetworkPolicy(ports=ports or [8085], expose_wireguard=True)
            health = HealthProbe(type="http", endpoint="/health", port=8085)
        elif category == AppCategory.DISTRIBUTED:
            resources = ResourceQuotas(
                cpu_cores=2.0, ram_mb=2048, gpu_vram_mb=0, storage_mb=4096, gpu_required=False, max_power_w=20.0
            )
            power = PowerPolicy(tier=PowerPriority.L1_STANDARD, min_battery_soc=25.0)
            network = NetworkPolicy(
                ports=ports or [9000, 9001], expose_wireguard=True, space_dtn_enabled=True, lora_heartbeat=True
            )
            health = HealthProbe(type="tcp", endpoint="localhost", port=9000)
        else:
            resources = ResourceQuotas(
                cpu_cores=1.0,
                ram_mb=512,
                gpu_vram_mb=1024 if gpu_required else 0,
                storage_mb=1024,
                gpu_required=gpu_required,
                max_power_w=25.0,
            )
            power = PowerPolicy(tier=power_tier, min_battery_soc=30.0)
            network = NetworkPolicy(ports=ports or [8080], expose_wireguard=True)
            health = HealthProbe(type="http", endpoint="/health", port=8080)

        return AppManifest(
            name=name,
            app_id=app_id,
            version="1.0.0",
            description=f"Enterprise {category.value} workload for Sovereign Mini Datacenter.",
            author="Enterprise Engineer",
            category=category,
            runtime=runtime,
            entrypoint=entrypoint,
            resources=resources,
            power=power,
            network=network,
            storage=StoragePolicy(persistent_volume=f"{app_id}-data", mount_point=f"/var/lib/smdc/apps/{app_id}/data"),
            health_check=health,
        )

    @staticmethod
    def render_manifest_yaml(manifest: AppManifest) -> str:
        """Serializes a manifest as an ``smdc-app.yaml`` document using a real YAML emitter."""
        header = "# Sovereign Mini Datacenter (SMDC) — Enterprise App Manifest\n"
        payload = manifest.to_dict()
        try:
            import yaml
        except ImportError:
            # JSON is a YAML subset, so this stays a loadable smdc-app.yaml.
            return header + json.dumps(payload, indent=2) + "\n"
        return header + yaml.safe_dump(payload, sort_keys=False, default_flow_style=False, allow_unicode=True)

    @staticmethod
    def _render_sample_app(manifest: AppManifest) -> str:
        """Renders the sample ``app.py``. Manifest strings are embedded only as ``repr()`` literals."""
        return f'''"""Sample Enterprise Workload for Sovereign Mini Datacenter."""

from __future__ import annotations

import logging
import time

from sovereign_dc.enterprise.sdk import AppLifecycleHandler, SMDCClient

APP_ID = {str(manifest.app_id)!r}
APP_NAME = {str(manifest.name)!r}

logger = logging.getLogger(APP_ID)


def main() -> None:
    logger.info("Starting %s (%s)...", APP_NAME, APP_ID)
    client = SMDCClient()
    lifecycle = AppLifecycleHandler(APP_ID, client)

    logger.info("Reading SMDC power telemetry...")
    telemetry = client.get_telemetry()
    logger.info(
        "Current Solar: %.1f W | Battery SoC: %.1f%% (source: %s)",
        telemetry.get("solar_watts", 0.0),
        telemetry.get("battery_soc", 0.0),
        telemetry.get("source", "unknown"),
    )

    try:
        while lifecycle.is_running:
            # is_paused turns True when the EnterpriseManager sharing this process's event
            # bus pauses this app (operator request or load shedding).
            if lifecycle.is_paused:
                logger.warning("Workload paused by the SMDC enterprise manager. Idling...")
                time.sleep(5)
                continue

            logger.info("Executing enterprise workload iteration...")
            # Perform custom business logic here (IoT aggregation, AI inference, etc.)
            client.emit_telemetry(APP_ID, {{"status": "ok", "processed_records": 10}})
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Gracefully shutting down %s...", APP_ID)
    finally:
        lifecycle.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
'''

    @staticmethod
    def _render_dockerfile(manifest: AppManifest) -> str:
        """Renders the Dockerfile; the entrypoint is emitted in JSON exec form only."""
        try:
            argv = shlex.split(str(manifest.entrypoint))
        except ValueError:
            argv = str(manifest.entrypoint).split()
        if not argv:
            argv = ["python3", "app.py"]
        ports: list[int] = []
        for port in manifest.network.ports:
            try:
                port_num = int(port)
            except (TypeError, ValueError):
                continue
            if 1 <= port_num <= 65535:
                ports.append(port_num)
        expose = " ".join(str(p) for p in ports) if ports else "8080"
        return (
            "FROM python:3.11-slim\n"
            "WORKDIR /app\n"
            "COPY smdc-app.yaml smdc-app.json app.py /app/\n"
            "RUN pip install --no-cache-dir sovereign-dc || true\n"
            f"EXPOSE {expose}\n"
            f"ENTRYPOINT {json.dumps(argv[:1])}\n"
            f"CMD {json.dumps(argv[1:])}\n"
        )

    @staticmethod
    def _render_readme(manifest: AppManifest) -> str:
        """Renders the scaffold README; manifest strings cannot leave their heading / code span."""
        # Collapse whitespace/newlines and drop backticks so the values stay inside their
        # heading / inline-code span / shell line.
        safe_name = " ".join(str(manifest.name).split()) or "Enterprise App"
        safe_id = shlex.quote(str(manifest.app_id).replace("`", ""))
        return f"""# {safe_name}

> Enterprise Onboarding Package for **Sovereign Mini Datacenter (SMDC)**
> **App ID**: `{safe_id}` | **Category**: `{manifest.category.value}` | **Power Tier**: `{manifest.power.tier.value}`

## Quickstart

```bash
# 1. Validate manifest against local SMDC node
smdc app validate .

# 2. Register application with local SMDC node
smdc app register .

# 3. Start application runtime
smdc app start {safe_id}

# 4. Check application telemetry and power budget
smdc app status {safe_id}
```
"""

    @staticmethod
    def scaffold_file_names(create_sample_code: bool = True) -> tuple[str, ...]:
        """Names of the files ``create_project_scaffold`` writes into the target directory."""
        return SCAFFOLD_MANIFEST_FILES + (SCAFFOLD_SAMPLE_FILES if create_sample_code else ())

    @classmethod
    def existing_scaffold_files(cls, target_dir: Path | str, create_sample_code: bool = True) -> list[Path]:
        """Scaffold targets already present in ``target_dir`` (files, directories or symlinks)."""
        dest = Path(target_dir)
        return [dest / name for name in cls.scaffold_file_names(create_sample_code) if os.path.lexists(dest / name)]

    @staticmethod
    def _backup_path(path: Path, stamp: str) -> Path:
        """First free ``<name>.bak-<stamp>[-N]`` next to ``path``."""
        candidate = path.with_name(f"{path.name}.bak-{stamp}")
        counter = 1
        while os.path.lexists(candidate):
            candidate = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
            counter += 1
        return candidate

    @staticmethod
    def _write_text_atomic(path: Path, content: str) -> None:
        """Writes ``content`` to a temporary file beside ``path`` and renames it into place."""
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_name, 0o644)
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    @classmethod
    def create_project_scaffold(
        cls,
        target_dir: Path | str,
        manifest: AppManifest,
        create_sample_code: bool = True,
        *,
        force: bool = False,
        backups: list[tuple[Path, Path]] | None = None,
    ) -> Path:
        """Generate a complete enterprise application project directory.

        An application under development is never overwritten silently. Every file the
        scaffold would write is checked before anything is written; if one already exists,
        ``ScaffoldConflictError`` (listing them) is raised and the directory is left
        untouched. With ``force=True`` each existing file is first moved to
        ``<name>.bak-<UTC timestamp>`` beside it (the ``(original, backup)`` pairs are
        appended to ``backups`` when a list is passed) and only then replaced. Each file is
        written to a temporary file and renamed into place.
        """
        dest = Path(target_dir)
        # Values are serialized by a YAML/JSON emitter (never string interpolation), so quotes,
        # colons or newlines in a name cannot corrupt the manifest.
        contents: dict[str, str] = {
            "smdc-app.json": manifest.to_json(indent=2),
            "smdc-app.yaml": cls.render_manifest_yaml(manifest),
        }
        if create_sample_code:
            contents["app.py"] = cls._render_sample_app(manifest)
            contents["Dockerfile"] = cls._render_dockerfile(manifest)
            contents["README.md"] = cls._render_readme(manifest)
        if set(contents) != set(cls.scaffold_file_names(create_sample_code)):  # pragma: no cover - invariant
            raise RuntimeError("scaffold file list and rendered contents disagree")

        existing = cls.existing_scaffold_files(dest, create_sample_code)
        if existing and not force:
            raise ScaffoldConflictError(dest, existing)

        dest.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        for path in existing:
            backup = cls._backup_path(path, stamp)
            os.rename(path, backup)
            logger.warning("Scaffold --force: moved existing %s to %s", path, backup)
            if backups is not None:
                backups.append((path, backup))

        for name in cls.scaffold_file_names(create_sample_code):
            cls._write_text_atomic(dest / name, contents[name])

        return dest
