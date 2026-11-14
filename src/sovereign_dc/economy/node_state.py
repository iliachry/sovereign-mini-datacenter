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
import math
import os
import shutil
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass

from sovereign_dc.config import get_config, get_state_dir, is_trusted_legacy_file
from sovereign_dc.economy.ledger import MAX_CREDITS, Ledger, Transaction, credits_to_micro
from sovereign_dc.economy.wallet import AddressType, NodeWallet
from sovereign_dc.log import get_logger

logger = get_logger("sovereign_dc.economy.node_state")

NODE_WALLET_FILENAME = "smdc_node_wallet.json"
NODE_PQC_WALLET_FILENAME = "smdc_node_pqc_wallet.json"
NODE_LEDGER_FILENAME = "smdc_node_ledger.db"
_LOCK_FILENAME = "smdc_node_state.lock"

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


@contextlib.contextmanager
def _state_lock() -> Iterator[None]:
    """Serialises identity creation and migration across threads and (where flock exists) processes."""
    with _THREAD_LOCK:
        lock_path = os.path.join(get_state_dir(), _LOCK_FILENAME)
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows: the thread lock is all there is
            yield
            return
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as err:
            logger.debug("Could not open %s (%s); continuing with the in-process lock only", lock_path, err)
            yield
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


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
                ledger = os.path.join(tmp_dir, legacy.ledger)
                if not os.path.lexists(ledger) or is_trusted_legacy_file(ledger):
                    found.append((legacy, tmp_dir))
    return found


def _move(src: str, dst: str) -> None:
    """Renames ``src`` to ``dst`` (never over an existing file); falls back to a move across filesystems."""
    if os.path.lexists(dst):
        raise FileExistsError(dst)
    try:
        os.rename(src, dst)
    except OSError:
        shutil.move(src, dst)
    with contextlib.suppress(OSError):
        os.chmod(dst, 0o600)


def _adopt(legacy: LegacySet, directory: str, paths: NodeStatePaths) -> None:
    """Renames one legacy set to the node identity file names."""
    legacy_ledger = os.path.join(directory, legacy.ledger)
    if os.path.isfile(legacy_ledger) and not os.path.lexists(paths.ledger):
        # Side files first: a leftover -wal belongs to the database it was written for.
        for suffix in _SQLITE_SIDECARS:
            if os.path.isfile(legacy_ledger + suffix):
                _move(legacy_ledger + suffix, paths.ledger + suffix)
        _move(legacy_ledger, paths.ledger)
    if legacy.pqc_wallet is not None and not os.path.lexists(paths.pqc_wallet):
        legacy_pqc = os.path.join(directory, legacy.pqc_wallet)
        if os.path.isfile(legacy_pqc):
            _move(legacy_pqc, paths.pqc_wallet)
    # The wallet goes last: its presence is what marks the node identity as established.
    _move(os.path.join(directory, legacy.wallet), paths.wallet)


def migrate_legacy_node_state() -> str | None:
    """Adopts a legacy per-surface wallet/ledger set as the node identity, if there is none yet.

    Returns the adopted surface name ("CLI", "dashboard" or "MCP"), or None when nothing was
    migrated (a node wallet already exists, or no legacy set is on disk). Idempotent.
    """
    paths = node_state_paths()
    with _state_lock():
        if os.path.lexists(paths.wallet):
            return None
        found = _find_legacy_sets(os.path.dirname(paths.wallet))
        if not found:
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
    wallet.save_to_file(path)
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
