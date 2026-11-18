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
renaming its files to the names above (keys and balances are kept as they are):

==========  ==========================  ==========================  ===========================
surface     wallet                      ledger                      ML-DSA-87 wallet
==========  ==========================  ==========================  ===========================
CLI         smdc_default_wallet.json    smdc_economy_ledger.db      -
dashboard   smdc_dashboard_wallet.json  smdc_dashboard_ledger.db    -
MCP         smdc_mcp_wallet.json        smdc_mcp_ledger.db          smdc_mcp_pqc_wallet.json
==========  ==========================  ==========================  ===========================

If exactly one set exists it is adopted. If several exist, the CLI set is adopted (then the
dashboard set, then the MCP set, when there is no CLI set), the others stay on disk untouched
and one warning names them. No file is ever deleted.
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


def _has_established_ledger(path: str) -> bool:
    """True when ``path`` is an intact SQLite ledger that already holds transactions."""
    if not os.path.isfile(path) or os.path.getsize(path) < 100:
        return False
    try:
        with open(path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return False
        uri = f"{Path(os.path.abspath(path)).as_uri()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as conn:
            ok = conn.execute("PRAGMA quick_check").fetchone() == ("ok",)
            has_rows = ok and conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone() is not None
        return has_rows and Ledger(db_path=path).verify_chain()
    except sqlite3.Error:
        return False


def _read_migrating_owned(marker_path: str) -> set[str]:
    """Components ('ledger', 'pqc_wallet') an interrupted migration already owned at destination."""
    try:
        st = os.lstat(marker_path)
        if not stat.S_ISREG(st.st_mode):
            return set()
        with open(marker_path, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError, TypeError):
        return set()
    raw = data.get("owned", []) if isinstance(data, dict) else []
    return {item for item in raw if item in {"ledger", "pqc_wallet"}} if isinstance(raw, list) else set()


def _write_migrating_owned(marker_path: str, owned: set[str]) -> None:
    """Atomically records which destination components this migration is permitted to replace."""
    state_dir = os.path.dirname(marker_path)
    fd, tmp_marker = tempfile.mkstemp(prefix=f"{_MIGRATING_FILENAME}.", dir=state_dir)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fd = -1
            fp.write(json.dumps({"owned": sorted(owned)}))
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


def _adopt(legacy: LegacySet, directory: str, paths: NodeStatePaths) -> None:
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

    plan: list[tuple[str, str, bool]] = []
    stale_sidecars: list[str] = []

    legacy_ledger = os.path.join(directory, legacy.ledger)
    if os.path.isfile(legacy_ledger) and ("ledger" in owned or not _has_established_ledger(paths.ledger)):
        replace_ledger = os.path.lexists(paths.ledger)
        # Side files first: a leftover -wal belongs to the database it was written for.
        for suffix in _SQLITE_SIDECARS:
            src_side = legacy_ledger + suffix
            dst_side = paths.ledger + suffix
            if os.path.isfile(src_side):
                plan.append((src_side, dst_side, True))
            elif os.path.lexists(dst_side):
                stale_sidecars.append(dst_side)
        plan.append((legacy_ledger, paths.ledger, replace_ledger))
        new_owned.add("ledger")

    if legacy.pqc_wallet is not None and ("pqc_wallet" in owned or not os.path.lexists(paths.pqc_wallet)):
        legacy_pqc = os.path.join(directory, legacy.pqc_wallet)
        if os.path.isfile(legacy_pqc):
            plan.append((legacy_pqc, paths.pqc_wallet, os.path.lexists(paths.pqc_wallet)))
            new_owned.add("pqc_wallet")

    # The wallet goes last: its presence is what marks the node identity as established.
    plan.append((os.path.join(directory, legacy.wallet), paths.wallet, False))

    staged: list[tuple[str, str, bool, str]] = []
    try:
        for src, dst, overwrite in plan:
            if not overwrite and os.path.lexists(dst):
                raise FileExistsError(dst)
            staged.append(
                (
                    src,
                    dst,
                    overwrite,
                    _stage_adopt_item(src, dst, state_dir, same_dir, require_trusted=not same_dir),
                )
            )

        _write_migrating_owned(marker_path, new_owned)

        for dst_side in stale_sidecars:
            with contextlib.suppress(OSError):
                os.unlink(dst_side)

        for _src, dst, overwrite, tmp_dst in staged:
            _publish_staged_item(tmp_dst, dst, overwrite=overwrite)
    finally:
        for _src, _dst, _overwrite, tmp_dst in staged:
            with contextlib.suppress(OSError):
                os.unlink(tmp_dst)

    # Unlink legacy sources only after paths.wallet is published so any interrupted attempt
    # retains the complete legacy set (including SQLite -wal/-shm sidecars) for retry.
    for src, _dst, _overwrite, _tmp_dst in staged:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(src)
    with contextlib.suppress(OSError):
        os.unlink(marker_path)


def migrate_legacy_node_state() -> str | None:
    """Adopts a legacy per-surface wallet/ledger set as the node identity, if there is none yet.

    Returns the adopted surface name ("CLI", "dashboard" or "MCP"), or None when nothing was
    migrated (a node wallet already exists, or no legacy set is on disk). Idempotent.
    """
    paths = node_state_paths()
    with _state_lock():
        if os.path.lexists(paths.wallet):
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(os.path.dirname(paths.wallet), _MIGRATING_FILENAME))
            return None
        found = _find_legacy_sets(os.path.dirname(paths.wallet))
        if not found:
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(os.path.dirname(paths.wallet), _MIGRATING_FILENAME))
            return None
        chosen, directory = found[0]
        _adopt(chosen, directory, paths)
        logger.info(
            "Adopted the %s wallet and ledger (%s) as this node's identity: %s, %s",
            chosen.surface,
            os.path.join(directory, chosen.wallet),
            paths.wallet,
            paths.ledger,
        )
        leftovers = [
            os.path.join(other_dir, name)
            for other, other_dir in found[1:]
            for name in (other.wallet, other.ledger, other.pqc_wallet)
            if name is not None and os.path.lexists(os.path.join(other_dir, name))
        ]
        if leftovers:
            logger.warning(
                "Several wallet/ledger sets from an older release were found. The %s set is now the node "
                "identity; these files were left untouched and are no longer used: %s",
                chosen.surface,
                ", ".join(leftovers),
            )
        return chosen.surface


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
