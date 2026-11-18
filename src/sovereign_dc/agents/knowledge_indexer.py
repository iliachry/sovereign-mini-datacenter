#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Autonomous Document & Knowledge Base Indexer
Extracts, chunks, embeds via local Ollama, and indexes into Qdrant for Open-WebUI RAG.
"""

import fnmatch
import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

# Logging is configured only when this file runs as a script (see the bottom of the file):
# importing it as a library must not touch the root logger. Records propagate to whatever
# the host application configured.
logger = logging.getLogger("KnowledgeIndexer")
LOG_FORMAT = "%(asctime)s [KnowledgeIndexer] %(message)s"

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
QDRANT_URL = os.getenv("QDRANT_BASE_URL", "http://qdrant:6333")
DEFAULT_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
ECO_EMBEDDING_MODEL = os.getenv("ECO_EMBEDDING_MODEL", "all-minilm")
COLLECTION_NAME = "sovereign_knowledge"
# Vector size of the Qdrant collection. Every embedding written must have this size.
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
# Shared Sentinel mode file. It must live on a volume shared with sentinel_copilot. Empty
# means "use the default in the private state directory" (see mode_file_path()).
MODE_FILE = os.getenv("SOVEREIGN_MODE_FILE", "")
# Sentinel modes in which batch vectorisation is deferred to conserve power.
POWER_SAVING_MODES = frozenset({"ECO_PRESERVATION", "CRITICAL_SHEDDING"})
POLL_INTERVAL_SECONDS = 30
# Healthy polls (with indexed documents) between Qdrant collection and source reconciliations.
COLLECTION_VERIFY_IDLE_POLLS = 10
WATCH_DIR = os.getenv("DOCS_WATCH_DIR", "/data/documents")
INDEXED_SUFFIXES = (".md", ".txt", ".csv", ".json", ".py", ".sh")
# Directory names (fnmatch patterns, matched against each directory's own name) that are
# never descended into. The defaults keep Nextcloud's internals (app data, trash bin, file
# versions, external-storage metadata, caches, partial uploads) and hidden directories out
# of the index. An empty DOCS_EXCLUDE_DIRS disables the exclusion.
DEFAULT_EXCLUDE_DIRS = "appdata_*,files_trashbin,files_versions,files_external,cache,uploads,.*"


def _split_patterns(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


EXCLUDE_DIRS = _split_patterns(os.getenv("DOCS_EXCLUDE_DIRS", DEFAULT_EXCLUDE_DIRS))
# Comma-separated globs matched against a document's path relative to the watch directory
# ("/"-separated; "*" and "?" stay within one path segment, "**" spans segments). Empty
# (the default) means every document under the watch directory. The Nextcloud deployment
# sets "*/files/**" so that only users' own files are indexed.
INCLUDE_GLOBS = _split_patterns(os.getenv("DOCS_INCLUDE_GLOB", ""))
# Documents larger than this are skipped (one warning per file): a huge file would need
# thousands of embedding calls and its points would not fit a sane Qdrant request.
MAX_FILE_BYTES = int(os.getenv("DOCS_MAX_FILE_BYTES", str(5 * 1024 * 1024)))
# Points per Qdrant upsert request. One request holding every chunk of a large document can
# exceed Qdrant's request-size limit, which fails identically on every retry.
UPSERT_BATCH_SIZE = 64
# Payload field that identifies the document a point belongs to (its full path). Points
# are reconciled and removed per source through a filter on this field.
SOURCE_PATH_FIELD = "path"
# mtime recorded for a source that is only known from the collection (not indexed by this
# process yet): it never equals a real mtime, so the file is re-indexed if it still exists.
UNKNOWN_MTIME = -1.0
DocumentVersion = float | tuple[int, int, str]
QDRANT_TIMEOUT_SECONDS = 30
SCROLL_PAGE_SIZE = 1000


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compiles a "/"-separated glob ("*", "?" within a segment; "**" across segments)."""
    segments = pattern.strip("/").split("/")
    out: list[str] = []
    for position, segment in enumerate(segments):
        last = position == len(segments) - 1
        if segment == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
            continue
        body = "".join("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in segment)
        out.append(body if last else body + "/")
    return re.compile("".join(out) + r"\Z")


def is_excluded_dir(name: str) -> bool:
    """True if a directory with this name must not be descended into (``EXCLUDE_DIRS``)."""
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in EXCLUDE_DIRS)


def is_included_path(relative_path: str) -> bool:
    """True if ``relative_path`` (relative to the watch directory) passes ``INCLUDE_GLOBS``."""
    if not INCLUDE_GLOBS:
        return True
    posix = relative_path.replace(os.sep, "/")
    return any(_glob_to_regex(pattern).match(posix) for pattern in INCLUDE_GLOBS)


_oversize_warned: set[str] = set()


def is_oversized(filepath: str) -> bool:
    """True if the document exceeds ``MAX_FILE_BYTES`` (warns once per file, not per scan).

    A file whose size cannot be read is not reported as oversized: the read error then
    surfaces in ``index_file`` like any other failure.
    """
    try:
        size = os.path.getsize(filepath)
    except OSError:
        return False
    if size <= MAX_FILE_BYTES:
        _oversize_warned.discard(filepath)
        return False
    if filepath not in _oversize_warned:
        _oversize_warned.add(filepath)
        logger.warning(
            "Skipping '%s': %d bytes exceeds DOCS_MAX_FILE_BYTES=%d; it is not indexed.",
            filepath,
            size,
            MAX_FILE_BYTES,
        )
    return True


def mode_file_path() -> str:
    """Path of the shared Sentinel mode file.

    ``SOVEREIGN_MODE_FILE`` if set (the container deployment points it at a volume shared by
    the agents), else ``sovereign_mode`` inside the private state directory
    (``SMDC_STATE_DIR`` or ``~/.smdc``, mode 0700). The default is never a predictable name
    in shared ``/tmp``, where any local user could pre-create or replace it.
    """
    configured = os.getenv("SOVEREIGN_MODE_FILE") or MODE_FILE
    if configured:
        return configured
    state_dir = os.environ.get("SMDC_STATE_DIR") or os.path.expanduser("~/.smdc")
    try:
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        os.chmod(state_dir, 0o700)
    except OSError as e:
        logger.warning("State directory %s is unusable: %s", state_dir, e)
    return os.path.join(state_dir, "sovereign_mode")


def get_current_mode() -> str:
    """Reads the current Sentinel mode from the shared mode file ("NORMAL" if absent)."""
    mode_file = mode_file_path()
    try:
        if os.path.exists(mode_file):
            with open(mode_file, encoding="utf-8") as f:
                return f.read().strip() or "NORMAL"
    except Exception as e:
        logger.warning("Failed to read sovereign mode: %s", e)
    return "NORMAL"


def get_current_model() -> str:
    """Returns the embedding model used for indexing.

    The model is deliberately NOT switched with the Sentinel mode: the Qdrant collection
    has a fixed vector size (``EMBEDDING_DIM``) and the former ECO model (``all-minilm``,
    384-dim) cannot be written into a 768-dim collection. Power is saved by deferring
    indexing in ECO mode instead (see ``run_worker``). ``ECO_EMBEDDING_MODEL`` is kept
    only for backward compatibility of the module interface.
    """
    return DEFAULT_EMBEDDING_MODEL


_collection_created = False


def ensure_qdrant_collection() -> bool:
    """Creates the Qdrant collection if it does not exist. Returns True when it is ready."""
    global _collection_created
    _collection_created = False
    url = f"{QDRANT_URL}/collections/{COLLECTION_NAME}"
    try:
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                logger.info("Qdrant collection '%s' ready.", COLLECTION_NAME)
                return True
            logger.error("Unexpected Qdrant status %s for collection '%s'.", resp.status, COLLECTION_NAME)
            return False
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.error("Error checking Qdrant: %s", e)
            return False
    except Exception as e:
        logger.warning("Qdrant not reachable yet: %s", e)
        return False

    try:
        payload = json.dumps({"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="PUT")
        with urllib.request.urlopen(req, timeout=5):
            logger.info("Created Qdrant collection '%s'.", COLLECTION_NAME)
    except Exception as e:
        logger.error("Failed to create Qdrant collection '%s': %s", COLLECTION_NAME, e)
        return False
    _collection_created = True
    try:
        # Keyword index on the source path: per-document reconciliation filters on it.
        # Optional (Qdrant filters unindexed fields too, just slower), so failure is not fatal.
        _qdrant_request("PUT", "/index", {"field_name": SOURCE_PATH_FIELD, "field_schema": "keyword"}, timeout=5)
    except Exception as e:
        logger.warning("Could not create payload index on '%s': %s", SOURCE_PATH_FIELD, e)
    return True


def get_embedding(text: str) -> list[float]:
    """Generates embedding vector via local Ollama API.

    Raises ValueError if the response carries no embedding or one of the wrong size;
    a placeholder vector is never substituted.
    """
    url = f"{OLLAMA_URL}/api/embeddings"
    current_model = get_current_model()
    payload = json.dumps({"model": current_model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    embedding = data.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise ValueError(f"Embedding model '{current_model}' returned no embedding")
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(
            f"Embedding model '{current_model}' returned {len(embedding)} dimensions; "
            f"collection '{COLLECTION_NAME}' requires {EMBEDDING_DIM}"
        )
    return [float(v) for v in embedding]


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Splits document text into overlapping token windows."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


def _qdrant_request(
    method: str, suffix: str, body: dict[str, Any] | None = None, timeout: float = QDRANT_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Sends a JSON request to ``/collections/<collection><suffix>``; returns the decoded reply.

    Raises on transport errors and non-2xx statuses (``urllib`` raises HTTPError). A reply
    that is not a JSON object is returned as an empty dict.
    """
    url = f"{QDRANT_URL}/collections/{COLLECTION_NAME}{suffix}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        decoded = json.loads(raw.decode("utf-8")) if isinstance(raw, (bytes, bytearray)) and raw else {}
    except ValueError:
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


def chunk_point_id(filepath: str, chunk_index: int) -> str:
    """Deterministic Qdrant point id of one chunk of one document.

    A UUID built from the first 128 bits of ``sha256(path, chunk index)``. Qdrant accepts
    unsigned 64-bit integers or UUIDs as ids; the previous 32-bit md5-derived integer made
    unrelated chunks collide (and silently overwrite each other) at realistic corpus sizes.
    """
    digest = hashlib.sha256(f"{filepath}\x00{chunk_index}".encode("utf-8", errors="surrogatepass")).digest()
    return str(uuid.UUID(bytes=digest[:16]))


def delete_source_points(filepath: str, keep_ids: list[str] | None = None) -> None:
    """Deletes the points of document ``filepath`` except those whose id is in ``keep_ids``.

    Uses Qdrant delete-by-filter on the ``path`` payload field, so it removes every point of
    that document regardless of how its id was derived: chunks that no longer exist after
    the document shrank, and points written by releases that used 32-bit integer ids.
    Raises if Qdrant does not confirm the deletion.
    """
    point_filter: dict[str, Any] = {"must": [{"key": SOURCE_PATH_FIELD, "match": {"value": filepath}}]}
    if keep_ids:
        point_filter["must_not"] = [{"has_id": keep_ids}]
    _qdrant_request("POST", "/points/delete?wait=true", {"filter": point_filter})


def list_indexed_sources() -> set[str]:
    """Returns the distinct source paths that have points in the collection (payload scroll)."""
    sources: set[str] = set()
    offset: Any = None
    while True:
        body: dict[str, Any] = {"limit": SCROLL_PAGE_SIZE, "with_payload": [SOURCE_PATH_FIELD], "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = _qdrant_request("POST", "/points/scroll", body).get("result")
        if not isinstance(result, dict):
            raise ValueError("Qdrant scroll returned no result object")
        for point in result.get("points") or []:
            payload = point.get("payload") if isinstance(point, dict) else None
            path = payload.get(SOURCE_PATH_FIELD) if isinstance(payload, dict) else None
            if isinstance(path, str) and path:
                sources.add(path)
        offset = result.get("next_page_offset")
        if offset is None:
            return sources


# Documents whose partially written points could not be rolled back (see ``index_file``).
# They may hold points although the document is not recorded as indexed, so every scan
# retries the deletion until Qdrant confirms it (see ``retry_pending_cleanup``).
_cleanup_pending: set[str] = set()


# Documents that may hold points the caller does not record as indexed (see ``index_file``):
# either their current chunks were written but the stale-chunk deletion failed (the
# collection holds the complete new version, possibly next to chunks of an older one), or
# their first upsert failed with an unknown outcome (it may have been committed). They are
# tracked here so that ``scan_and_index`` removes their points if the file vanishes before a
# retry succeeds.
_reconcile_pending: set[str] = set()


def pending_cleanup_paths() -> frozenset[str]:
    """Documents whose partial points still have to be deleted from the collection."""
    return frozenset(_cleanup_pending)


def pending_reconcile_paths() -> frozenset[str]:
    """Documents that may hold points nothing else tracks (stale-chunk deletion or first upsert failed)."""
    return frozenset(_reconcile_pending)


def _roll_back_partial_index(filepath: str) -> bool:
    """Deletes every point of ``filepath`` after an indexing attempt that wrote only some.

    On failure the path is remembered in the needs-cleanup set. Returns True once Qdrant
    confirmed the deletion.
    """
    try:
        delete_source_points(filepath)
    except Exception as e:
        if filepath not in _cleanup_pending:
            logger.error("Could not remove the partial points of %s (%s); will retry every scan.", filepath, e)
        _cleanup_pending.add(filepath)
        return False
    _cleanup_pending.discard(filepath)
    _reconcile_pending.discard(filepath)  # no point of the document is left
    return True


def retry_pending_cleanup() -> int:
    """Retries the deletion for every document in the needs-cleanup set. Returns the failures."""
    failed = 0
    for path in sorted(_cleanup_pending):
        if _roll_back_partial_index(path):
            logger.info("Removed the partial points of '%s' left by a failed indexing attempt.", path)
        else:
            failed += 1
    return failed


def index_file(filepath: str) -> bool:
    """Chunks and embeds a document, then makes Qdrant hold exactly its current chunks.

    Every chunk is embedded first; the points are then upserted in batches of
    ``UPSERT_BATCH_SIZE`` and finally every other point of this document (same ``path``
    payload, different id) is deleted. A document that shrank therefore loses its surplus
    chunks, an emptied document loses all of them, and points stored under the integer ids
    of older releases are replaced the first time the document is re-indexed. If embedding
    fails nothing is written or deleted, so the previous version stays searchable.

    If an upsert batch fails after an earlier batch of the same attempt was written, the
    collection holds a partial document that nothing tracks (the caller records a document
    only on success). The attempt is therefore rolled back: every point of the document is
    deleted, so a file that is removed before the next scan leaves nothing searchable behind.
    If that deletion fails too, the path is kept in a needs-cleanup set which
    ``scan_and_index`` retries on every scan. A failure of the first batch deletes nothing
    (a previous version stays searchable), but its outcome is unknown: the request may have
    been committed before its response was lost. The path is therefore recorded in the
    pending-reconciliation set, exactly as after a failure of the final stale-chunk deletion
    (which leaves the complete new version in place, searchable): ``scan_and_index`` then
    still removes the document's points if the file is deleted before a retry succeeds; for a
    new document nothing else tracks them. In every case the whole document is retried on the
    next scan.

    A document larger than ``MAX_FILE_BYTES`` is refused (see ``is_oversized``).

    The document is identified by its absolute path (point ids and the ``path`` payload), so
    indexing it once through a relative and once through an absolute path gives one entry.

    Returns True only if the collection was brought in line with the file.
    """
    filepath = os.path.abspath(filepath)
    if is_oversized(filepath):
        return False
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            content = f.read()

        chunks = chunk_text(content)
        points: list[dict[str, Any]] = []

        for idx, chunk in enumerate(chunks):
            point_id = chunk_point_id(filepath, idx)
            vector = get_embedding(chunk)
            points.append(
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "source": os.path.basename(filepath),
                        "path": filepath,
                        "chunk_index": idx,
                        "text": chunk,
                        "indexed_at": time.time(),
                    },
                }
            )

        batch_size = max(1, UPSERT_BATCH_SIZE)
        for start in range(0, len(points), batch_size):
            try:
                _qdrant_request("PUT", "/points?wait=true", {"points": points[start : start + batch_size]})
            except Exception as e:
                if start > 0:
                    rolled_back = _roll_back_partial_index(filepath)
                    logger.error(
                        "Upsert of chunks %d+ of %s failed (%s); the %d chunks already written were %s.",
                        start,
                        filepath,
                        e,
                        start,
                        "rolled back" if rolled_back else "NOT removed yet (cleanup pending)",
                    )
                else:
                    # The outcome of a failed request is unknown: Qdrant may have committed the
                    # batch before the response was lost. Nothing is deleted (a previous version
                    # stays searchable), but the path is tracked so its points are removed if
                    # the file is gone before a retry succeeds.
                    _reconcile_pending.add(filepath)
                    logger.error(
                        "First upsert of %s failed (%s); its outcome is unknown, so the document is tracked "
                        "for reconciliation.",
                        filepath,
                        e,
                    )
                raise
        try:
            delete_source_points(filepath, keep_ids=[str(p["id"]) for p in points])
        except Exception:
            # The new chunks are stored but the document is not reconciled: keep track of it.
            _reconcile_pending.add(filepath)
            raise
        # The collection now holds exactly this version: nothing is left to clean up.
        _cleanup_pending.discard(filepath)
        _reconcile_pending.discard(filepath)
        logger.info("Indexed %d chunks from '%s'.", len(points), os.path.basename(filepath))
        return True
    except Exception as e:
        logger.error("Failed to index %s: %s", filepath, e)
        return False


def remove_file(filepath: str) -> bool:
    """Removes every point of a document that no longer exists. Returns True on success."""
    # A document is indexed under its absolute path (index_file). The spelling given here
    # is removed as well when it differs: points written by an earlier release under a
    # relative path carry that spelling.
    spellings = list(dict.fromkeys((os.path.abspath(filepath), filepath)))
    try:
        for spelling in spellings:
            delete_source_points(spelling)
            _cleanup_pending.discard(spelling)
            _reconcile_pending.discard(spelling)
        logger.info("Removed '%s' from the index (document no longer present).", os.path.basename(filepath))
        return True
    except Exception as e:
        logger.error("Failed to remove %s from the index: %s", filepath, e)
        return False


def _is_under_watch_dir(path: str) -> bool:
    root = os.path.join(os.path.abspath(WATCH_DIR), "")
    return os.path.abspath(path).startswith(root)


def list_documents(watch_dir: str, walk_errors: list[OSError] | None = None) -> list[str]:
    """Lists the documents under ``watch_dir`` that are eligible for indexing, sorted.

    Directories matching ``EXCLUDE_DIRS`` are pruned (never descended into); a file is
    eligible when it has one of ``INDEXED_SUFFIXES`` and its path relative to ``watch_dir``
    passes ``INCLUDE_GLOBS``. Listing errors are appended to ``walk_errors`` if given.
    """
    documents: list[str] = []
    watch_dir = os.path.abspath(watch_dir)  # one spelling per document (see index_file)
    onerror = walk_errors.append if walk_errors is not None else None
    for root, dirs, files in os.walk(watch_dir, onerror=onerror):
        dirs[:] = sorted(d for d in dirs if not is_excluded_dir(d))
        for name in files:
            if not name.endswith(INDEXED_SUFFIXES):
                continue
            path = os.path.join(root, name)
            if is_included_path(os.path.relpath(path, watch_dir)):
                documents.append(path)
    return sorted(documents)


def _document_version(path: str) -> tuple[int, int, str]:
    """Returns ``(mtime_ns, size_bytes, sha256_hex)`` so preserved-mtime edits still re-index."""
    st = os.stat(path)
    with open(path, "rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    return (st.st_mtime_ns, st.st_size, digest)


def scan_and_index(indexed_files: set[tuple[str, DocumentVersion]]) -> tuple[int, int]:
    """Brings the index in line with WATCH_DIR. Returns (succeeded, failed).

    New or modified documents are (re-)indexed. A new file is recorded in ``indexed_files``
    only after it is indexed successfully, and a failed re-index invalidates any cached
    version (downgrading to ``UNKNOWN_MTIME`` or deferring to the pending sets), so
    failures are always retried on the next scan.

    Documents recorded in ``indexed_files`` that are no longer eligible are removed from the
    index (and from ``indexed_files`` once that succeeded; a failed removal downgrades any
    cached fingerprint to ``UNKNOWN_MTIME`` so a restored file is re-indexed, counts as a
    failure, and is retried). "No longer eligible" covers a deleted file, a file that now
    sits under an excluded directory or outside ``INCLUDE_GLOBS`` (so tightening the filters
    purges what an earlier configuration indexed), and a file that grew beyond
    ``MAX_FILE_BYTES``. Removal is skipped for a scan in which WATCH_DIR is missing or could
    not be listed completely, so an unmounted volume or an unreadable sub-directory never
    looks like "every document was deleted"; only paths inside WATCH_DIR are removed.

    Before anything else the scan retries the deletion of partial points that a failed
    indexing attempt could not roll back (``retry_pending_cleanup``; each one still pending
    counts as a failure). That does not depend on the file still existing or on WATCH_DIR
    being listable: such points belong to no recorded document.

    A document whose chunks were written but whose stale-chunk deletion failed, or whose
    first upsert failed with an unknown outcome (``pending_reconcile_paths``), is not in
    ``indexed_files`` either. While its file is
    eligible it is simply indexed again; once the file is no longer eligible its points are
    removed exactly like those of a recorded document (same WATCH_DIR safeguards).
    """
    pending_invalid = _cleanup_pending | _reconcile_pending
    if pending_invalid:
        indexed_files.difference_update({k for k in indexed_files if k[0] in pending_invalid})
    succeeded = 0
    failed = retry_pending_cleanup()
    present: set[str] = set()
    walk_errors: list[OSError] = []
    for p in list_documents(WATCH_DIR, walk_errors):
        if is_oversized(p):
            continue  # skipped, not a failure; a previously indexed version is removed below
        present.add(p)
        try:
            key: tuple[str, DocumentVersion] = (p, _document_version(p))
        except FileNotFoundError:
            continue  # vanished during the scan: handled as a removal on the next one
        except OSError:
            key = (p, UNKNOWN_MTIME)
        if key != (p, UNKNOWN_MTIME) and key in indexed_files:
            continue
        was_indexed = any(k[0] == p for k in indexed_files)
        logger.info("New or modified document detected: %s", os.path.basename(p))
        if index_file(p):
            # Drop entries for older versions of the same document.
            indexed_files.difference_update({k for k in indexed_files if k[0] == p})
            indexed_files.add(key)
            succeeded += 1
        else:
            if was_indexed:
                indexed_files.difference_update({k for k in indexed_files if k[0] == p})
                if p not in _cleanup_pending and p not in _reconcile_pending:
                    indexed_files.add((p, UNKNOWN_MTIME))
            failed += 1

    if walk_errors or not os.path.isdir(WATCH_DIR):
        if walk_errors:
            logger.warning(
                "Could not list all of %s (%s); skipping removal of vanished documents.", WATCH_DIR, walk_errors[0]
            )
        return succeeded, failed

    known = {path for path, _ in indexed_files} | _reconcile_pending
    legacy_present = [path for path in sorted(known) if path not in present and os.path.abspath(path) in present]
    for path in legacy_present:
        canonical = os.path.abspath(path)
        if any(k[0] == canonical for k in indexed_files):
            try:
                delete_source_points(path)
            except Exception as e:
                logger.error("Failed to remove legacy points for %s from the index: %s", path, e)
                _reconcile_pending.add(path)
                failed += 1
            else:
                _cleanup_pending.discard(path)
                _reconcile_pending.discard(path)
                indexed_files.difference_update({k for k in indexed_files if k[0] == path})

    vanished = {
        path
        for path in known
        if path not in present and os.path.abspath(path) not in present and _is_under_watch_dir(path)
    }
    for path in sorted(vanished):
        if remove_file(path):
            indexed_files.difference_update({k for k in indexed_files if k[0] == path})
            succeeded += 1
        else:
            stale = {k[0] for k in indexed_files} & {path, os.path.abspath(path)}
            indexed_files.difference_update({k for k in indexed_files if k[0] in stale})
            indexed_files.update((s, UNKNOWN_MTIME) for s in stale)
            failed += 1
    return succeeded, failed


def _file_has_chunks(path: str) -> bool:
    """True when ``path`` is eligible and produces at least one indexable chunk."""
    if is_oversized(path):
        return False
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return bool(chunk_text(f.read()))
    except OSError as e:
        return not isinstance(e, FileNotFoundError)


def seed_indexed_sources(indexed_files: set[tuple[str, DocumentVersion]]) -> bool:
    """Reconciles ``indexed_files`` with the source paths currently stored in Qdrant.

    Adds unknown remote sources with ``UNKNOWN_MTIME`` (so files deleted while the worker
    was down are removed and existing ones are re-indexed), and downgrades any cached
    non-empty document whose points are absent from Qdrant back to ``UNKNOWN_MTIME`` so
    the next scan rebuilds it even after a partial or repopulated collection wipe.
    Returns False if Qdrant could not be read.
    """
    try:
        sources = list_indexed_sources()
    except Exception as e:
        logger.warning("Could not list indexed documents: %s", e)
        return False
    known = {path for path, _ in indexed_files}
    indexed_files.update((path, UNKNOWN_MTIME) for path in sources if path not in known)
    for path, ver in list(indexed_files):
        if ver != UNKNOWN_MTIME and path not in sources and _file_has_chunks(path):
            indexed_files.discard((path, ver))
            indexed_files.add((path, UNKNOWN_MTIME))
    return True


def run_worker() -> None:
    """Polls WATCH_DIR forever and keeps the Qdrant collection in line with it.

    The watch directory is never created here: in the container deployment it is a
    read-only mount that Nextcloud populates on its first start, so creating it would fail
    and restart-loop the service on a fresh node. While it is missing the worker waits
    (logged once) and looks again on every poll.
    """
    global _collection_created
    logger.info("Starting Autonomous Knowledge Indexer Worker...")
    collection_ready = ensure_qdrant_collection()

    indexed_files: set[tuple[str, DocumentVersion]] = set()
    sources_seeded = False
    waiting_for_watch_dir = False
    polls_since_verify = 0
    last_mode = get_current_mode()

    while True:
        try:
            current_mode = get_current_mode()
            if current_mode != last_mode:
                logger.info("🔄 Sentinel mode change: %s -> %s", last_mode, current_mode)
                last_mode = current_mode

            if current_mode in POWER_SAVING_MODES:
                logger.info("Sentinel mode %s: deferring batch vectorisation to conserve power.", current_mode)
            elif not os.path.isdir(WATCH_DIR):
                if not waiting_for_watch_dir:
                    waiting_for_watch_dir = True
                    logger.warning(
                        "Watch directory %s does not exist yet; waiting for it (checked every %d s).",
                        WATCH_DIR,
                        POLL_INTERVAL_SECONDS,
                    )
            else:
                if waiting_for_watch_dir:
                    waiting_for_watch_dir = False
                    logger.info("Watch directory %s is now available.", WATCH_DIR)
                if not collection_ready:
                    _collection_created = False
                    sources_seeded = False
                    collection_ready = ensure_qdrant_collection()
                    if (
                        collection_ready
                        and _collection_created
                        and (indexed_files or _cleanup_pending or _reconcile_pending)
                    ):
                        logger.info(
                            "Qdrant collection '%s' was recreated; scheduling full re-index.",
                            COLLECTION_NAME,
                        )
                        indexed_files.clear()
                        _cleanup_pending.clear()
                        _reconcile_pending.clear()
                if collection_ready and not sources_seeded:
                    sources_seeded = seed_indexed_sources(indexed_files)
                _, failed = scan_and_index(indexed_files)
                polls_since_verify = (
                    polls_since_verify + 1 if (not failed and indexed_files and COLLECTION_VERIFY_IDLE_POLLS > 0) else 0
                )
                if failed or (COLLECTION_VERIFY_IDLE_POLLS > 0 and polls_since_verify >= COLLECTION_VERIFY_IDLE_POLLS):
                    # The collection or individual sources may have been lost externally: re-verify.
                    collection_ready = False
                    polls_since_verify = 0
        except Exception as e:
            logger.error("Indexer loop error: %s", e)
        time.sleep(POLL_INTERVAL_SECONDS)


# Backward compatibility alias
process_file = index_file

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    run_worker()
