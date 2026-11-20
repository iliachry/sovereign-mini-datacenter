"""The node's single economic identity: one wallet pair and one ledger in the state directory.

Every surface of a node (the ``smdc economy`` CLI, the operations dashboard and the MCP
server) resolves its wallet and ledger here, so all of them report the same address and the
same balance:

* ``smdc_node_wallet.json``      the node wallet (Ed25519 when created here); holds the balance
* ``smdc_node_pqc_wallet.json``  the node's ML-DSA-87 (FIPS 204) wallet
* ``smdc_node_ledger.db``        the node ledger

Nothing here mints. A new ledger starts empty and the node wallet starts at zero; credits only
appear through an explicit operator action (``mint_local_test_credits``, i.e.
``smdc economy mint``), and such credits are locally issued test credits with no external value.

Migration from releases that kept one wallet and ledger per surface (each with a self-minted
starting balance): on first use, if no node wallet exists yet, one legacy set is adopted by
renaming its files to the names above (keys and balances are kept as they are, except that an
already-established ``smdc_node_ledger.db`` or existing ``smdc_node_pqc_wallet.json`` is kept
in place and the corresponding file of the chosen legacy set stays on disk untouched):

==========  ==========================  ==========================  ===========================
surface     wallet                      ledger                      ML-DSA-87 wallet
==========  ==========================  ==========================  ===========================
CLI         smdc_default_wallet.json    smdc_economy_ledger.db      -
dashboard   smdc_dashboard_wallet.json  smdc_dashboard_ledger.db    -
MCP         smdc_mcp_wallet.json        smdc_mcp_ledger.db          smdc_mcp_pqc_wallet.json
==========  ==========================  ==========================  ===========================

If exactly one set exists it is adopted. If several exist, the CLI set is adopted (then the
dashboard set, then the MCP set, when there is no CLI set), the others (plus any skipped file of
the chosen set) stay on disk untouched and one warning names them. No file is ever deleted.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # POSIX advisory locks; absent on Windows (see ``_state_lock``).
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

from sovereign_dc.config import _current_uid, get_config, get_state_dir, is_trusted_legacy_file
from sovereign_dc.economy.ledger import MAX_CREDITS, Ledger, Transaction, credits_to_micro
from sovereign_dc.economy.wallet import AddressType, NodeWallet
from sovereign_dc.log import get_logger

logger = get_logger("sovereign_dc.economy.node_state")

NODE_WALLET_FILENAME = "smdc_node_wallet.json"
NODE_PQC_WALLET_FILENAME = "smdc_node_pqc_wallet.json"
NODE_LEDGER_FILENAME = "smdc_node_ledger.db"
_LOCK_FILENAME = "smdc_node_state.lock"
_MIGRATING_FILENAME = ".smdc_node_state.migrating"

# Memo of credits issued by ``mint_local_test_credits``.
LOCAL_TEST_MINT_MEMO = "LOCAL_TEST_CREDIT"
LOCAL_TEST_CREDIT_NOTICE = (
    "These are locally issued test credits: this node created them for itself in its own "
    "ledger. They have no external value and are not backed by, or redeemable for, anything."
)

_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


@dataclass(frozen=True)
class LegacySet:
    """File names one surface of an older release used for its own wallet and ledger."""

    surface: str
    wallet: str
    ledger: str
    pqc_wallet: str | None = None


# In adoption order (see the module docstring).
LEGACY_SETS: tuple[LegacySet, ...] = (
    LegacySet("CLI", "smdc_default_wallet.json", "smdc_economy_ledger.db"),
    LegacySet("dashboard", "smdc_dashboard_wallet.json", "smdc_dashboard_ledger.db"),
    LegacySet("MCP", "smdc_mcp_wallet.json", "smdc_mcp_ledger.db", "smdc_mcp_pqc_wallet.json"),
)


@dataclass(frozen=True)
class NodeStatePaths:
    """Where the node identity lives."""

    wallet: str
    pqc_wallet: str
    ledger: str


@dataclass
class NodeIdentity:
    """The node's wallets and ledger, as every surface sees them."""

    wallet: NodeWallet
    pqc_wallet: NodeWallet
    ledger: Ledger
    paths: NodeStatePaths

    @property
    def balance(self) -> float:
        """Balance of the node wallet in credits."""
        return self.ledger.get_balance(self.wallet.address)


_THREAD_LOCK = threading.RLock()


def node_state_paths() -> NodeStatePaths:
    """Paths of the node wallet, ML-DSA-87 wallet and ledger in the private state directory."""
    state_dir = get_state_dir()
    return NodeStatePaths(
        wallet=os.path.join(state_dir, NODE_WALLET_FILENAME),
        pqc_wallet=os.path.join(state_dir, NODE_PQC_WALLET_FILENAME),
        ledger=os.path.join(state_dir, NODE_LEDGER_FILENAME),
    )


_FCNTL_UNSET = object()


@contextlib.contextmanager
def _state_lock() -> Iterator[None]:
    """Serialises identity creation and migration across threads and processes."""
    from sovereign_dc.enterprise.registry import exclusive_file_lock

    with _THREAD_LOCK, contextlib.ExitStack() as stack:
        lock_path = os.path.join(get_state_dir(), _LOCK_FILENAME)
        active_fcntl = None if (fcntl is None or sys.modules.get("fcntl", _FCNTL_UNSET) is None) else fcntl
        try:
            stack.enter_context(exclusive_file_lock(Path(lock_path), use_fcntl=active_fcntl))
        except TimeoutError:
            raise
        except OSError as err:
            logger.debug("Could not lock %s (%s); continuing with the in-process lock only", lock_path, err)
        yield


def _is_trusted_stat(st: os.stat_result) -> bool:
    if not stat.S_ISREG(st.st_mode):
        return False
    uid = _current_uid()
    if uid is None:
        return True
    return st.st_uid == uid and not (st.st_mode & 0o022)


def _is_trusted_legacy_db(ledger: str) -> bool:
    candidates = (ledger, *(ledger + suffix for suffix in _SQLITE_SIDECARS))
    return all(is_trusted_legacy_file(p) for p in candidates if os.path.lexists(p))


def _legacy_cli_tmp_dir() -> str | None:
    """Temp directory in which the CLI of older releases may have left its wallet and ledger."""
    tmp_dir = tempfile.gettempdir()
    return tmp_dir if os.path.realpath(tmp_dir) != os.path.realpath(get_state_dir()) else None


def _find_legacy_sets(state_dir: str) -> list[tuple[LegacySet, str]]:
    """Legacy sets present on disk as ``(set, directory)``, in adoption order.

    A set is present when its wallet file exists. The CLI set is also looked for in the
    temp directory, where the CLI kept using a wallet created there by an earlier release,
    but only if that file is verifiably ours (regular file, our uid, not group/world writable).
    """
    found: list[tuple[LegacySet, str]] = []
    for legacy in LEGACY_SETS:
        if os.path.isfile(os.path.join(state_dir, legacy.wallet)):
            found.append((legacy, state_dir))
        elif legacy.surface == "CLI":
            tmp_dir = _legacy_cli_tmp_dir()
            if tmp_dir is not None and is_trusted_legacy_file(os.path.join(tmp_dir, legacy.wallet)):
                if _is_trusted_legacy_db(os.path.join(tmp_dir, legacy.ledger)):
                    found.append((legacy, tmp_dir))
    return found


def _restrict_adopted_mode(dst: str) -> None:
    try:
        os.chmod(dst, 0o600)
    except OSError as err:
        # The file is already inside the 0700 state directory; say that its own mode is wider.
        logger.warning("Could not restrict adopted file %s to mode 0600: %s", dst, err)


def _file_sha256(path: str) -> tuple[int, str]:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        return size, hashlib.file_digest(f, "sha256").hexdigest()


def _stage_copy(src: str, dst_dir: str, prefix: str, *, require_trusted: bool = False) -> str:
    """Copies ``src`` into an exclusive 0o600 staging file in ``dst_dir``, fsyncs, and verifies its digest."""
    open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if require_trusted:
        open_flags |= getattr(os, "O_NOFOLLOW", 0)
    src_fd = os.open(src, open_flags)
    fd = -1
    tmp_dst = ""
    try:
        src_st = os.fstat(src_fd)
        if require_trusted and not _is_trusted_stat(src_st):
            raise PermissionError(f"Untrusted legacy file: {src}")
        if not stat.S_ISREG(src_st.st_mode):
            raise OSError(f"Not a regular file: {src}")
        with os.fdopen(src_fd, "rb") as src_fp:
            src_fd = -1
            expected_digest = hashlib.file_digest(src_fp, "sha256").hexdigest()
            expected_size = os.fstat(src_fp.fileno()).st_size
            src_fp.seek(0)
            fd, tmp_dst = tempfile.mkstemp(prefix=prefix, dir=dst_dir)
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as dst_fp:
                fd = -1
                shutil.copyfileobj(src_fp, dst_fp)
                dst_fp.flush()
                if hasattr(os, "fsync"):
                    os.fsync(dst_fp.fileno())
        if _file_sha256(tmp_dst) != (expected_size, expected_digest):
            raise OSError(f"Incomplete or corrupted staged copy for {src}")
        return tmp_dst
    except BaseException:
        if src_fd >= 0:
            os.close(src_fd)
        if fd >= 0:
            os.close(fd)
        if tmp_dst:
            with contextlib.suppress(OSError):
                os.unlink(tmp_dst)
        raise


def _stage_adopt_item(src: str, dst: str, state_dir: str, same_dir: bool, *, require_trusted: bool = False) -> str:
    """Stages ``src`` into an exclusive temporary path inside ``state_dir`` without removing ``src``."""
    prefix = f".{os.path.basename(dst)}.adopt."
    if same_dir:
        tmp_dst = os.path.join(state_dir, f"{prefix}{os.getpid()}.{os.urandom(8).hex()}")
        try:
            os.link(src, tmp_dst)
            return tmp_dst
        except OSError:
            pass
    return _stage_copy(src, state_dir, prefix, require_trusted=require_trusted)


def _publish_staged_item(tmp_dst: str, dst: str, *, overwrite: bool) -> None:
    """Atomically publishes staged ``tmp_dst`` to ``dst`` without clobbering when ``overwrite`` is False."""
    if overwrite:
        os.replace(tmp_dst, dst)
    else:
        try:
            os.link(tmp_dst, dst)
        except FileExistsError:
            raise
        except OSError:
            if os.path.lexists(dst):
                raise FileExistsError(dst) from None
            os.replace(tmp_dst, dst)
        with contextlib.suppress(OSError):
            os.unlink(tmp_dst)
    _restrict_adopted_mode(dst)


def _move(src: str, dst: str, *, overwrite: bool = False) -> None:
    """Renames ``src`` to ``dst``; stages and verifies atomically across filesystems."""
    if not overwrite and os.path.lexists(dst):
        raise FileExistsError(dst)
    dst_dir = os.path.dirname(os.path.abspath(dst))
    same_dir = os.path.realpath(os.path.dirname(os.path.abspath(src))) == os.path.realpath(dst_dir)
    tmp_dst = _stage_adopt_item(src, dst, dst_dir, same_dir)
    try:
        _publish_staged_item(tmp_dst, dst, overwrite=overwrite)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_dst)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(src)


_CORRUPT_SQLITE_CODES: frozenset[int] = frozenset(
    getattr(sqlite3, name) for name in ("SQLITE_CORRUPT", "SQLITE_NOTADB") if hasattr(sqlite3, name)
)


def _is_corrupt_sqlite_error(err: sqlite3.Error) -> bool:
    """True only when SQLite definitively reports ``path`` is malformed or not a database.

    Any lock, busy, recovery-required (e.g. ``SQLITE_READONLY_ROLLBACK`` or
    ``SQLITE_READONLY_RECOVERY`` on a hot journal or unrecovered WAL), permission, or I/O error
    returns False so callers re-raise and preserve the existing destination database untouched.
    """
    code = getattr(err, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xFF) in _CORRUPT_SQLITE_CODES
    msg = str(err).lower()
    return "malformed" in msg or "not a database" in msg


def _is_readonly_recovery_error(err: sqlite3.Error) -> bool:
    code = getattr(err, "sqlite_errorcode", None)
    readonly_code = getattr(sqlite3, "SQLITE_READONLY", 8)
    if isinstance(code, int) and (code & 0xFF) == readonly_code:
        return True
    return "readonly" in str(err).lower()


def _sqlite_family_fingerprint(abs_path: str) -> dict[str, tuple[int, int, str] | None]:
    """Captures ``(st_ino, size, sha256)`` for ``abs_path`` and each SQLite sidecar (or None if absent)."""
    fp: dict[str, tuple[int, int, str] | None] = {}
    for candidate in (abs_path, *(abs_path + suffix for suffix in _SQLITE_SIDECARS)):
        try:
            st = os.lstat(candidate)
            if not stat.S_ISREG(st.st_mode):
                raise OSError(f"Refusing non-regular ledger path: {candidate}")
            size, digest = _file_sha256(candidate)
        except FileNotFoundError:
            fp[candidate] = None
            continue
        fp[candidate] = (st.st_ino, size, digest)
    return fp


def _inspect_unrecovered_ledger_copy(path: str) -> bool | None:
    """Inspects a verified consistent copy when ``path`` needs rollback/WAL recovery under ``mode=ro``."""
    abs_path = os.path.abspath(path)
    before_fp = _sqlite_family_fingerprint(abs_path)
    if before_fp[abs_path] is None:
        return False
    parent_dir = os.path.dirname(abs_path)
    with tempfile.TemporaryDirectory(prefix=".smdc_ledger_probe.", dir=parent_dir) as tmp_dir:
        os.chmod(tmp_dir, 0o700)
        probe_db = os.path.join(tmp_dir, "probe.db")
        copied: dict[str, tuple[int, str]] = {}
        try:
            shutil.copy2(abs_path, probe_db, follow_symlinks=False)
            os.chmod(probe_db, 0o600)
            copied[abs_path] = _file_sha256(probe_db)
            for suffix in _SQLITE_SIDECARS:
                side = abs_path + suffix
                if before_fp[side] is None:
                    continue
                dst_side = probe_db + suffix
                shutil.copy2(side, dst_side, follow_symlinks=False)
                os.chmod(dst_side, 0o600)
                copied[side] = _file_sha256(dst_side)
        except FileNotFoundError:
            return None
        if _sqlite_family_fingerprint(abs_path) != before_fp or any(
            exp is not None and copied.get(src_file) != exp[1:] for src_file, exp in before_fp.items()
        ):
            return None
        try:
            with contextlib.closing(sqlite3.connect(probe_db, timeout=5.0)) as rw_conn:
                rw_conn.execute("PRAGMA busy_timeout=5000")
                return Ledger.verify_readonly_conn(rw_conn)
        except sqlite3.Error as probe_err:
            if _is_corrupt_sqlite_error(probe_err):
                return False
            raise


def _probe_sqlite_ro(path: str) -> str:
    """Read-only SQLite ledger classification: ``'unestablished'``, ``'established'``, or ``'recovery'``."""
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"Refusing non-regular ledger path: {path}")
        if st.st_size < 100:
            return "unestablished"
        with open(path, "rb") as f:
            header = f.read(16)
    except FileNotFoundError:
        return "unestablished"
    if header != b"SQLite format 3\x00":
        return "unestablished"
    uri = f"{Path(os.path.abspath(path)).as_uri()}?mode=ro"
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            return "established" if Ledger.verify_readonly_conn(conn) else "unestablished"
    except sqlite3.Error as err:
        if _is_corrupt_sqlite_error(err):
            return "unestablished"
        if _is_readonly_recovery_error(err):
            return "recovery"
        raise


def _has_established_ledger(path: str) -> bool:
    """True when ``path`` is an intact SQLite ledger that already holds transactions."""
    for _attempt in range(4):
        status = _probe_sqlite_ro(path)
        if status == "established":
            return True
        if status == "unestablished":
            return False
        verdict = _inspect_unrecovered_ledger_copy(path)
        if verdict is not None:
            return verdict
    raise sqlite3.OperationalError(f"Canonical ledger {path} changed concurrently during inspection")


def _read_migrating_state(marker_path: str) -> dict[str, Any] | None:
    """Parsed migration marker dict when a migration is in progress, or None if absent/invalid."""
    try:
        st = os.lstat(marker_path)
        if not stat.S_ISREG(st.st_mode):
            return None
        with open(marker_path, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("owned"), list):
        return None
    return data


def _read_migrating_owned(marker_path: str) -> set[str]:
    """Components ('ledger', 'pqc_wallet') an interrupted migration already owned at destination."""
    state = _read_migrating_state(marker_path)
    if state is None:
        return set()
    return {item for item in state["owned"] if item in {"ledger", "pqc_wallet"}}


def _write_migrating_owned(marker_path: str, owned: set[str], *, post_wallet: dict[str, Any] | None = None) -> None:
    """Atomically records migration ownership (and post-wallet retirement metadata when given)."""
    payload: dict[str, Any] = {"owned": sorted(owned)}
    if post_wallet is not None:
        payload["post_wallet"] = post_wallet
    state_dir = os.path.dirname(marker_path)
    fd, tmp_marker = tempfile.mkstemp(prefix=f"{_MIGRATING_FILENAME}.", dir=state_dir)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fd = -1
            fp.write(json.dumps(payload))
            fp.flush()
            if hasattr(os, "fsync"):
                os.fsync(fp.fileno())
        os.replace(tmp_marker, marker_path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp_marker)
        raise


def _verify_trusted_sqlite_family(src_db: str) -> dict[str, tuple[int, int]]:
    """Open-fd O_NOFOLLOW trust verification of ``src_db`` and all existing SQLite sidecars."""
    identities: dict[str, tuple[int, int]] = {}
    open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    for path in (src_db, *(src_db + suffix for suffix in _SQLITE_SIDECARS)):
        try:
            fd = os.open(path, open_flags)
        except FileNotFoundError:
            continue
        try:
            st = os.fstat(fd)
            if not _is_trusted_stat(st):
                raise PermissionError(f"Untrusted legacy SQLite file: {path}")
            identities[path] = (st.st_dev, st.st_ino)
        finally:
            os.close(fd)
    if src_db not in identities:
        raise FileNotFoundError(src_db)
    return identities


def _sqlite_state_digest(conn: sqlite3.Connection) -> str:
    """Deterministic SHA-256 over all user table schemas and ordered rows on ``conn``."""
    hasher = hashlib.sha256()
    tables = [
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name ASC"
        ).fetchall()
    ]
    for tbl in tables:
        quoted = '"' + tbl.replace('"', '""') + '"'
        cols = [str(c[1]) for c in conn.execute(f"PRAGMA table_info({quoted})").fetchall()]
        hasher.update(json.dumps([tbl, cols]).encode("utf-8") + b"\n")
        for row in conn.execute(f"SELECT * FROM {quoted} ORDER BY rowid ASC").fetchall():  # noqa: S608 - identifier read from sqlite_master and SQL-quoted
            hasher.update(json.dumps(list(row), default=str).encode("utf-8") + b"\n")
    return hasher.hexdigest()


def _rollback_and_close(conn: sqlite3.Connection) -> None:
    """Rolls back any active transaction on ``conn`` and closes it safely."""
    try:
        in_tx = conn.in_transaction
    except sqlite3.Error:
        in_tx = False
    if in_tx:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
    with contextlib.suppress(sqlite3.Error):
        conn.close()


@contextlib.contextmanager
def _stage_legacy_ledger_snapshot(
    src_db: str, state_dir: str, *, require_trusted: bool
) -> Iterator[tuple[str, sqlite3.Connection, str]]:
    """Stages a consistent single-file SQLite snapshot of ``src_db`` and holds ``BEGIN EXCLUSIVE``.

    While yielded, ``src_conn`` holds ``BEGIN EXCLUSIVE`` on ``src_db`` verified to match the
    staged snapshot, preventing any concurrent SQLite writer (or ``DELETE``-mode reader) from
    committing or blocking legacy retirement after destination publication. On rollback/failure
    before retirement, all committed transactions in ``src_db`` remain intact on disk (SQLite may
    fold ``-wal`` into ``src_db`` on close).
    """
    before_ids = _verify_trusted_sqlite_family(src_db) if require_trusted else {}
    prefix = f".{NODE_LEDGER_FILENAME}.adopt."
    src_conn = sqlite3.connect(src_db, timeout=5.0, isolation_level=None)
    tmp_dst = ""
    src_digest = ""
    try:
        src_conn.execute("PRAGMA busy_timeout=5000")
        if require_trusted:
            recheck = _verify_trusted_sqlite_family(src_db)
            if recheck.get(src_db) != before_ids.get(src_db):
                raise PermissionError(f"Legacy SQLite file changed during open: {src_db}")
        matched = False
        for _attempt in range(3):
            tmp_dst = os.path.join(state_dir, f"{prefix}{os.getpid()}.{os.urandom(8).hex()}")
            src_conn.execute("VACUUM INTO ?", (tmp_dst,))
            os.chmod(tmp_dst, 0o600)
            src_conn.execute("BEGIN EXCLUSIVE")
            if require_trusted:
                recheck_lock = _verify_trusted_sqlite_family(src_db)
                if recheck_lock.get(src_db) != before_ids.get(src_db):
                    raise PermissionError(f"Legacy SQLite file changed before lock: {src_db}")
            if (
                src_conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='_smdc_migrated'"
                ).fetchone()
                is not None
            ):
                with contextlib.closing(sqlite3.connect(tmp_dst, timeout=5.0)) as clean_conn:
                    clean_conn.execute("DROP TRIGGER IF EXISTS _smdc_migrated")
                    clean_conn.commit()
            with open(tmp_dst, "r+b") as dst_fp:
                if hasattr(os, "fsync"):
                    os.fsync(dst_fp.fileno())
            src_digest = _sqlite_state_digest(src_conn)
            dst_uri = f"{Path(os.path.abspath(tmp_dst)).as_uri()}?mode=ro"
            with contextlib.closing(sqlite3.connect(dst_uri, uri=True, timeout=5.0)) as dst_conn:
                if dst_conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise OSError(f"Corrupted staged SQLite snapshot for {src_db}")
                dst_digest = _sqlite_state_digest(dst_conn)
            if src_digest == dst_digest:
                matched = True
                break
            src_conn.execute("ROLLBACK")
            with contextlib.suppress(OSError):
                os.unlink(tmp_dst)
            tmp_dst = ""
        if not matched:
            raise sqlite3.OperationalError(f"Legacy ledger {src_db} was modified concurrently during migration")
        yield tmp_dst, src_conn, src_digest
    finally:
        if tmp_dst:
            with contextlib.suppress(OSError):
                os.unlink(tmp_dst)
        _rollback_and_close(src_conn)


def _retire_legacy_ledger(src_conn: sqlite3.Connection, legacy_ledger: str) -> None:
    """Blocks any blocked or subsequent writes on ``legacy_ledger`` and unlinks it + sidecars."""
    has_tx = (
        src_conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='transactions'").fetchone()
        is not None
    )
    if has_tx:
        src_conn.execute(
            "CREATE TRIGGER IF NOT EXISTS _smdc_migrated "
            "BEFORE INSERT ON transactions "
            "BEGIN SELECT RAISE(ABORT, 'legacy ledger migrated to smdc_node_ledger.db'); END"
        )
    src_conn.commit()
    for path in (legacy_ledger, *(legacy_ledger + suffix for suffix in _SQLITE_SIDECARS)):
        with contextlib.suppress(OSError):
            os.unlink(path)
    src_conn.close()
    for path in (legacy_ledger, *(legacy_ledger + suffix for suffix in _SQLITE_SIDECARS)):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def _replace_sqlite_contents_in_place(src_snapshot: str, dst_conn: sqlite3.Connection, expected_digest: str) -> None:
    """Replaces all user tables and indexes on ``dst_conn`` inside its active transaction."""
    src_uri = f"{Path(os.path.abspath(src_snapshot)).as_uri()}?mode=ro"
    table_specs: list[tuple[str, str, list[str], list[tuple[Any, ...]]]] = []
    with contextlib.closing(sqlite3.connect(src_uri, uri=True, timeout=5.0)) as src_ro:
        tables = [
            (str(r[0]), str(r[1]))
            for r in src_ro.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND sql IS NOT NULL ORDER BY name ASC"
            ).fetchall()
        ]
        indexes = [
            str(r[0])
            for r in src_ro.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' "
                "AND sql IS NOT NULL ORDER BY name ASC"
            ).fetchall()
        ]
        for tbl_name, create_sql in tables:
            q_tbl = '"' + tbl_name.replace('"', '""') + '"'
            cols = [str(c[1]) for c in src_ro.execute(f"PRAGMA table_info({q_tbl})").fetchall()]
            rows = [
                tuple(r)
                for r in src_ro.execute(f"SELECT * FROM {q_tbl} ORDER BY rowid ASC").fetchall()  # noqa: S608 - quoted sqlite_master identifier
            ]
            table_specs.append((tbl_name, create_sql, cols, rows))

    for (old_tbl,) in dst_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        q_old = '"' + str(old_tbl).replace('"', '""') + '"'
        dst_conn.execute(f"DROP TABLE IF EXISTS {q_old}")
    if (
        dst_conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'").fetchone()
        is not None
    ):
        dst_conn.execute("DELETE FROM sqlite_sequence")

    for tbl_name, create_sql, cols, rows in table_specs:
        dst_conn.execute(create_sql)
        if rows:
            q_tbl = '"' + tbl_name.replace('"', '""') + '"'
            q_cols = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
            placeholders = ", ".join("?" for _ in cols)
            dst_conn.executemany(
                f"INSERT INTO {q_tbl} ({q_cols}) VALUES ({placeholders})",  # noqa: S608 - quoted sqlite_master identifiers
                rows,
            )
    for idx_sql in indexes:
        dst_conn.execute(idx_sql)
    if _sqlite_state_digest(dst_conn) != expected_digest:
        raise OSError("In-place SQLite ledger sync digest mismatch")


@contextlib.contextmanager
def _lock_or_replace_destination_ledger(path: str, tmp_dst: str) -> Iterator[tuple[sqlite3.Connection, bool] | None]:
    """Acquires ``BEGIN EXCLUSIVE`` on ``path``, or populates ``path`` directly if absent/corrupt."""
    while True:
        if not os.path.lexists(path):
            try:
                _publish_staged_item(tmp_dst, path, overwrite=False)
                yield None
                return
            except FileExistsError:
                continue
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"Refusing non-regular ledger path: {path}")
        is_sqlite = False
        if st.st_size >= 100:
            with open(path, "rb") as f:
                is_sqlite = f.read(16) == b"SQLite format 3\x00"
        if not is_sqlite:
            for suffix in _SQLITE_SIDECARS:
                with contextlib.suppress(OSError):
                    os.unlink(path + suffix)
            _publish_staged_item(tmp_dst, path, overwrite=True)
            yield None
            return
        conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            try:
                conn.execute("BEGIN EXCLUSIVE")
                established = Ledger.verify_readonly_conn(conn)
            except sqlite3.Error as err:
                if not _is_corrupt_sqlite_error(err):
                    raise
                conn.close()
                for suffix in _SQLITE_SIDECARS:
                    with contextlib.suppress(OSError):
                        os.unlink(path + suffix)
                _publish_staged_item(tmp_dst, path, overwrite=True)
                yield None
                return
            if os.lstat(path).st_ino != st.st_ino:
                raise sqlite3.OperationalError(f"Canonical ledger {path} was replaced concurrently")
            yield conn, established
            return
        finally:
            _rollback_and_close(conn)


def _publish_over_unestablished_ledger(tmp_dst: str, dst: str, expected_digest: str) -> None:
    """Replaces an unestablished ``dst`` in place under ``BEGIN EXCLUSIVE`` if still unestablished."""
    with _lock_or_replace_destination_ledger(dst, tmp_dst) as locked:
        if locked is None:
            return
        conn, established = locked
        if established:
            raise sqlite3.OperationalError(f"Canonical ledger {dst} became established concurrently during migration")
        _replace_sqlite_contents_in_place(tmp_dst, conn, expected_digest)
        conn.execute("COMMIT")
        _restrict_adopted_mode(dst)


def _reconcile_post_wallet_ledger(
    paths: NodeStatePaths,
    tmp_dst: str,
    held_conn: sqlite3.Connection,
    retire_ledger: str,
    leg_digest: str,
    published_digest: str | None,
) -> bool:
    """Reconciles canonical vs legacy ledger under dual ``BEGIN EXCLUSIVE`` locks."""
    with _lock_or_replace_destination_ledger(paths.ledger, tmp_dst) as locked:
        if locked is None:
            _retire_legacy_ledger(held_conn, retire_ledger)
            return True
        canon_conn, established = locked
        canon_digest = _sqlite_state_digest(canon_conn) if established else None
        if canon_digest == leg_digest or (
            published_digest is not None and leg_digest == published_digest and canon_digest is not None
        ):
            canon_conn.execute("ROLLBACK")
            _retire_legacy_ledger(held_conn, retire_ledger)
            return True
        if canon_digest is None or (published_digest is not None and canon_digest == published_digest):
            _replace_sqlite_contents_in_place(tmp_dst, canon_conn, leg_digest)
            canon_conn.execute("COMMIT")
            _restrict_adopted_mode(paths.ledger)
            _retire_legacy_ledger(held_conn, retire_ledger)
            return True
        canon_conn.execute("ROLLBACK")
        return False


def _log_migration_outcome(
    surface: str,
    wallet_src: str,
    adopted_ledger: bool,
    skipped_in_chosen: list[str],
    legacy_ledger: str,
    other_found: list[tuple[LegacySet, str]],
    paths: NodeStatePaths,
) -> str:
    """Emits migration summary info/warning logs matching adopted and skipped artifacts."""
    if adopted_ledger:
        logger.info(
            "Adopted the %s wallet and ledger (%s) as this node's identity: %s, %s",
            surface,
            wallet_src,
            paths.wallet,
            paths.ledger,
        )
    elif legacy_ledger in skipped_in_chosen:
        logger.info(
            "Adopted the %s wallet (%s) as this node's identity: %s (kept existing node ledger %s)",
            surface,
            wallet_src,
            paths.wallet,
            paths.ledger,
        )
    else:
        logger.info(
            "Adopted the %s wallet (%s) as this node's identity: %s",
            surface,
            wallet_src,
            paths.wallet,
        )
    other_leftovers = [
        os.path.join(other_dir, name)
        for other, other_dir in other_found
        for name in (other.wallet, other.ledger, other.pqc_wallet)
        if name is not None and os.path.lexists(os.path.join(other_dir, name))
    ]
    if other_leftovers and not skipped_in_chosen:
        logger.warning(
            "Several wallet/ledger sets from an older release were found. The %s set is now the node "
            "identity; these files were left untouched and are no longer used: %s",
            surface,
            ", ".join(other_leftovers),
        )
    elif skipped_in_chosen and not other_leftovers:
        logger.warning(
            "Existing node state was kept during %s migration; these legacy files were left untouched and are "
            "no longer used: %s",
            surface,
            ", ".join(skipped_in_chosen),
        )
    elif skipped_in_chosen and other_leftovers:
        logger.warning(
            "Several wallet/ledger sets or existing node state files were found during %s migration; these files "
            "were left untouched and are no longer used: %s",
            surface,
            ", ".join([*skipped_in_chosen, *other_leftovers]),
        )
    return surface


def _adopt(legacy: LegacySet, directory: str, paths: NodeStatePaths) -> tuple[bool, list[str]]:
    """Atomically stages and publishes one legacy set to the node identity file names."""
    state_dir = os.path.dirname(paths.wallet)
    same_dir = os.path.realpath(directory) == os.path.realpath(state_dir)
    if not same_dir and (
        not is_trusted_legacy_file(os.path.join(directory, legacy.wallet))
        or not _is_trusted_legacy_db(os.path.join(directory, legacy.ledger))
        or (
            legacy.pqc_wallet is not None
            and os.path.lexists(os.path.join(directory, legacy.pqc_wallet))
            and not is_trusted_legacy_file(os.path.join(directory, legacy.pqc_wallet))
        )
    ):
        raise PermissionError(f"Refusing untrusted legacy state in {directory}")

    marker_path = os.path.join(state_dir, _MIGRATING_FILENAME)
    owned = _read_migrating_owned(marker_path)
    new_owned = set(owned)

    plan: list[tuple[str, str, str, bool]] = []
    stale_sidecars: list[str] = []
    skipped: list[str] = []
    adopted_ledger = False

    legacy_ledger = os.path.join(directory, legacy.ledger)
    has_legacy_ledger = os.path.isfile(legacy_ledger)
    if has_legacy_ledger and ("ledger" in owned or not _has_established_ledger(paths.ledger)):
        replace_ledger = os.path.lexists(paths.ledger)
        # Snapshot staging writes a self-contained single-file DB, so remove any old destination sidecars.
        for suffix in _SQLITE_SIDECARS:
            dst_side = paths.ledger + suffix
            if os.path.lexists(dst_side):
                stale_sidecars.append(dst_side)
        plan.append(("ledger", legacy_ledger, paths.ledger, replace_ledger))
        new_owned.add("ledger")
        adopted_ledger = True
    elif has_legacy_ledger:
        skipped.append(legacy_ledger)

    if legacy.pqc_wallet is not None:
        legacy_pqc = os.path.join(directory, legacy.pqc_wallet)
        has_legacy_pqc = os.path.isfile(legacy_pqc)
        if has_legacy_pqc and ("pqc_wallet" in owned or not os.path.lexists(paths.pqc_wallet)):
            plan.append(("file", legacy_pqc, paths.pqc_wallet, os.path.lexists(paths.pqc_wallet)))
            new_owned.add("pqc_wallet")
        elif has_legacy_pqc:
            skipped.append(legacy_pqc)

    # The wallet goes last: its presence is what marks the node identity as established.
    wallet_src = os.path.join(directory, legacy.wallet)
    plan.append(("file", wallet_src, paths.wallet, False))

    staged: list[tuple[str, str, bool, str]] = []
    held_ledger_conn: sqlite3.Connection | None = None
    staged_ledger_digest: str | None = None
    with contextlib.ExitStack() as adopt_stack:
        try:
            for item_kind, src, dst, overwrite in plan:
                if not overwrite and os.path.lexists(dst):
                    raise FileExistsError(dst)
                if item_kind == "ledger":
                    tmp_dst, held_ledger_conn, staged_ledger_digest = adopt_stack.enter_context(
                        _stage_legacy_ledger_snapshot(src, state_dir, require_trusted=not same_dir)
                    )
                    staged.append((src, dst, overwrite, tmp_dst))
                else:
                    staged.append(
                        (
                            src,
                            dst,
                            overwrite,
                            _stage_adopt_item(src, dst, state_dir, same_dir, require_trusted=not same_dir),
                        )
                    )

            _write_migrating_owned(marker_path, new_owned)

            has_rollback_journal = os.path.lexists(paths.ledger + "-journal")
            in_place_empty_publish = (
                adopted_ledger
                and os.path.lexists(paths.ledger)
                and ("ledger" not in owned)
                and not has_rollback_journal
                and (staged_ledger_digest is not None)
            )
            if not in_place_empty_publish:
                for dst_side in stale_sidecars:
                    with contextlib.suppress(OSError):
                        os.unlink(dst_side)

            for _src, dst, overwrite, tmp_dst in staged:
                if dst == paths.ledger and overwrite and in_place_empty_publish and staged_ledger_digest is not None:
                    try:
                        _publish_over_unestablished_ledger(tmp_dst, dst, staged_ledger_digest)
                    except BaseException:
                        if owned:
                            _write_migrating_owned(marker_path, owned)
                        else:
                            with contextlib.suppress(OSError):
                                os.unlink(marker_path)
                        raise
                else:
                    _publish_staged_item(tmp_dst, dst, overwrite=overwrite)

            _write_migrating_owned(
                marker_path,
                new_owned,
                post_wallet={
                    "surface": legacy.surface,
                    "directory": directory,
                    "wallet_src": wallet_src,
                    "legacy_ledger": legacy_ledger,
                    "adopted_ledger": adopted_ledger,
                    "skipped": list(skipped),
                    "published_ledger_digest": staged_ledger_digest,
                    "retire_ledger": legacy_ledger if adopted_ledger else None,
                    "retire_files": [src for src, _dst, _ovw, _tmp in staged],
                    "require_trusted": not same_dir,
                },
            )
        finally:
            for _src, _dst, _overwrite, tmp_dst in staged:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_dst)

        # Retire legacy sources only after paths.wallet is published and recorded in the marker
        # so any interrupted retirement reconciles cleanly on retry.
        if held_ledger_conn is not None:
            _retire_legacy_ledger(held_ledger_conn, legacy_ledger)
        for src, _dst, _overwrite, _tmp_dst in staged:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(src)
    with contextlib.suppress(OSError):
        os.unlink(marker_path)
    return adopted_ledger, skipped


def _recover_post_wallet_migration(
    migrating_state: dict[str, Any], marker_path: str, paths: NodeStatePaths
) -> str | None:
    """Completes legacy retirement or reconciles post-wallet writes when ``paths.wallet`` exists."""
    state_dir = os.path.dirname(paths.wallet)
    owned = {item for item in migrating_state.get("owned", []) if item in {"ledger", "pqc_wallet"}}
    pw = migrating_state.get("post_wallet")
    if isinstance(pw, dict):
        surface = str(pw["surface"])
        chosen_dir = str(pw["directory"])
        wallet_src = str(pw["wallet_src"])
        legacy_ledger = str(pw["legacy_ledger"])
        adopted_ledger = bool(pw["adopted_ledger"])
        skipped_in_chosen = [str(x) for x in pw.get("skipped", []) if isinstance(x, str)]
        published_digest = pw.get("published_ledger_digest")
        retire_ledger = pw.get("retire_ledger")
        retire_files = [str(x) for x in pw.get("retire_files", []) if isinstance(x, str)]
        require_trusted = bool(pw.get("require_trusted", False))
    else:
        found_before = _find_legacy_sets(state_dir)
        if not found_before:
            with contextlib.suppress(OSError):
                os.unlink(marker_path)
            return None
        chosen, chosen_dir = found_before[0]
        surface = chosen.surface
        wallet_src = os.path.join(chosen_dir, chosen.wallet)
        legacy_ledger = os.path.join(chosen_dir, chosen.ledger)
        adopted_ledger = "ledger" in owned and os.path.isfile(legacy_ledger)
        skipped_in_chosen = [legacy_ledger] if (not adopted_ledger and os.path.isfile(legacy_ledger)) else []
        retire_files = [wallet_src]
        if adopted_ledger:
            retire_files.append(legacy_ledger)
        if chosen.pqc_wallet is not None:
            legacy_pqc = os.path.join(chosen_dir, chosen.pqc_wallet)
            if "pqc_wallet" in owned and os.path.isfile(legacy_pqc):
                retire_files.append(legacy_pqc)
            elif os.path.isfile(legacy_pqc):
                skipped_in_chosen.append(legacy_pqc)
        published_digest = None
        retire_ledger = legacy_ledger if adopted_ledger else None
        require_trusted = os.path.realpath(chosen_dir) != os.path.realpath(state_dir)

    if isinstance(retire_ledger, str) and os.path.isfile(retire_ledger):
        with _stage_legacy_ledger_snapshot(retire_ledger, state_dir, require_trusted=require_trusted) as (
            tmp_dst,
            held_conn,
            leg_digest,
        ):
            if not _reconcile_post_wallet_ledger(
                paths,
                tmp_dst,
                held_conn,
                retire_ledger,
                leg_digest,
                published_digest if isinstance(published_digest, str) else None,
            ):
                # Both ledgers received independent writes (including pre-lock races): preserve both.
                retire_files = [item for item in retire_files if item != retire_ledger]
                adopted_ledger = False
                if retire_ledger not in skipped_in_chosen:
                    skipped_in_chosen.append(retire_ledger)

    for src in retire_files:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(src)
    with contextlib.suppress(OSError):
        os.unlink(marker_path)

    chosen_real = os.path.realpath(chosen_dir)
    other_found = [
        (other, other_dir)
        for other, other_dir in _find_legacy_sets(state_dir)
        if not (other.surface == surface and os.path.realpath(other_dir) == chosen_real)
    ]
    return _log_migration_outcome(
        surface,
        wallet_src,
        adopted_ledger,
        skipped_in_chosen,
        legacy_ledger,
        other_found,
        paths,
    )


def migrate_legacy_node_state() -> str | None:
    """Adopts a legacy per-surface wallet/ledger set as the node identity, if there is none yet.

    Returns the adopted surface name ("CLI", "dashboard" or "MCP"), or None when nothing was
    migrated (a node wallet already exists, or no legacy set is on disk). Idempotent.
    """
    paths = node_state_paths()
    with _state_lock():
        state_dir = os.path.dirname(paths.wallet)
        marker_path = os.path.join(state_dir, _MIGRATING_FILENAME)
        migrating_state = _read_migrating_state(marker_path)
        if os.path.lexists(paths.wallet):
            if migrating_state is not None:
                return _recover_post_wallet_migration(migrating_state, marker_path, paths)
            with contextlib.suppress(OSError):
                os.unlink(marker_path)
            return None
        found = _find_legacy_sets(state_dir)
        if not found:
            with contextlib.suppress(OSError):
                os.unlink(marker_path)
            return None
        chosen, directory = found[0]
        adopted_ledger, skipped_in_chosen = _adopt(chosen, directory, paths)
        return _log_migration_outcome(
            chosen.surface,
            os.path.join(directory, chosen.wallet),
            adopted_ledger,
            skipped_in_chosen,
            os.path.join(directory, chosen.ledger),
            found[1:],
            paths,
        )


def _load_or_create_wallet(path: str, algorithm: AddressType, node_id: str | None) -> NodeWallet:
    if os.path.lexists(path):
        return NodeWallet.load_from_file(path)
    wallet = NodeWallet.create(node_id=node_id or get_config().node_id, algorithm=algorithm)
    try:
        wallet.save_to_file(path, overwrite=False)
    except FileExistsError:
        return NodeWallet.load_from_file(path)
    return wallet


def load_node_wallet(node_id: str | None = None) -> NodeWallet:
    """The node wallet, created (Ed25519, zero balance) on first use after any legacy migration."""
    migrate_legacy_node_state()
    with _state_lock():
        return _load_or_create_wallet(node_state_paths().wallet, AddressType.ED25519, node_id)


def load_node_pqc_wallet(node_id: str | None = None) -> NodeWallet:
    """The node's ML-DSA-87 wallet, created on first use after any legacy migration."""
    migrate_legacy_node_state()
    with _state_lock():
        return _load_or_create_wallet(node_state_paths().pqc_wallet, AddressType.ML_DSA_87, node_id)


def open_node_ledger() -> Ledger:
    """The node ledger (created empty if absent: nothing is minted)."""
    migrate_legacy_node_state()
    return Ledger(db_path=node_state_paths().ledger)


def get_node_identity(node_id: str | None = None) -> NodeIdentity:
    """Wallets and ledger of this node: the one identity the CLI, dashboard and MCP server share."""
    wallet = load_node_wallet(node_id)
    return NodeIdentity(
        wallet=wallet,
        pqc_wallet=load_node_pqc_wallet(node_id),
        ledger=open_node_ledger(),
        paths=node_state_paths(),
    )


def parse_mint_amount(value: object) -> float:
    """Validates a mint amount given as text or a number; ValueError with a usable message otherwise.

    Accepted: a finite number greater than zero, with at most 6 decimal places, of at most
    ``MAX_CREDITS`` credits.
    """
    if isinstance(value, bool):
        raise ValueError("amount must be a number")
    try:
        amount = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{value!r} is not a number") from None
    if not math.isfinite(amount):
        raise ValueError("amount must be a finite number")
    if amount <= 0:
        raise ValueError("amount must be greater than zero")
    try:
        credits_to_micro(amount)
    except ValueError:
        raise ValueError(f"amount must have at most 6 decimal places and be at most {MAX_CREDITS:,} credits") from None
    return amount


def mint_local_test_credits(
    amount: float,
    memo: str = "",
    *,
    wallet: NodeWallet | None = None,
    ledger: Ledger | None = None,
) -> Transaction:
    """Mints ``amount`` local test credits to the node wallet (the only way a balance appears).

    This is the explicit operator action behind ``smdc economy mint``. The credits are issued
    by this node for itself (see ``LOCAL_TEST_CREDIT_NOTICE``). ``wallet`` / ``ledger`` default
    to the node identity. Raises ValueError for an amount ``parse_mint_amount`` refuses.
    """
    amount = parse_mint_amount(amount)
    target_wallet = wallet if wallet is not None else load_node_wallet()
    target_ledger = ledger if ledger is not None else open_node_ledger()
    tx = target_ledger.mint(target_wallet.address, amount, memo=memo or LOCAL_TEST_MINT_MEMO)
    logger.warning(
        "Minted %s local test credits to %s (memo %s). %s",
        amount,
        target_wallet.address,
        tx.memo,
        LOCAL_TEST_CREDIT_NOTICE,
    )
    return tx
