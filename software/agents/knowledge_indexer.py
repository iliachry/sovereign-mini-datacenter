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


def ensure_qdrant_collection() -> bool:
    """Creates the Qdrant collection if it does not exist. Returns True when it is ready."""
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


def index_file(filepath: str) -> bool:
    """Chunks and embeds a document, then makes Qdrant hold exactly its current chunks.

    Every chunk is embedded first; the points are then upserted in batches of
    ``UPSERT_BATCH_SIZE`` and finally every other point of this document (same ``path``
    payload, different id) is deleted. A document that shrank therefore loses its surplus
    chunks, an emptied document loses all of them, and points stored under the integer ids
    of older releases are replaced the first time the document is re-indexed. If embedding
    fails nothing is written or deleted, so the previous version stays searchable; if an
    upsert batch fails nothing is deleted (earlier batches may already hold the new text)
    and the whole document is retried.

    A document larger than ``MAX_FILE_BYTES`` is refused (see ``is_oversized``).

    Returns True only if the collection was brought in line with the file.
    """
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
            _qdrant_request("PUT", "/points?wait=true", {"points": points[start : start + batch_size]})
        delete_source_points(filepath, keep_ids=[str(p["id"]) for p in points])
        logger.info("Indexed %d chunks from '%s'.", len(points), os.path.basename(filepath))
        return True
    except Exception as e:
        logger.error("Failed to index %s: %s", filepath, e)
        return False


def remove_file(filepath: str) -> bool:
    """Removes every point of a document that no longer exists. Returns True on success."""
    try:
        delete_source_points(filepath)
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


def scan_and_index(indexed_files: set[tuple[str, float]]) -> tuple[int, int]:
    """Brings the index in line with WATCH_DIR. Returns (succeeded, failed).

    New or modified documents are (re-)indexed. A file is recorded in ``indexed_files`` only
    after it was indexed successfully, so failures are retried on the next scan.

    Documents recorded in ``indexed_files`` that are no longer eligible are removed from the
    index (and from ``indexed_files`` once that succeeded; a failed removal counts as a
    failure and is retried). "No longer eligible" covers a deleted file, a file that now
    sits under an excluded directory or outside ``INCLUDE_GLOBS`` (so tightening the filters
    purges what an earlier configuration indexed), and a file that grew beyond
    ``MAX_FILE_BYTES``. Removal is skipped for a scan in which WATCH_DIR is missing or could
    not be listed completely, so an unmounted volume or an unreadable sub-directory never
    looks like "every document was deleted"; only paths inside WATCH_DIR are removed.
    """
    succeeded = 0
    failed = 0
    present: set[str] = set()
    walk_errors: list[OSError] = []
    for p in list_documents(WATCH_DIR, walk_errors):
        if is_oversized(p):
            continue  # skipped, not a failure; a previously indexed version is removed below
        present.add(p)
        try:
            key = (p, os.path.getmtime(p))
        except OSError:
            continue  # vanished during the scan: handled as a removal on the next one
        if key in indexed_files:
            continue
        logger.info("New or modified document detected: %s", os.path.basename(p))
        if index_file(p):
            # Drop entries for older versions of the same document.
            indexed_files.difference_update({k for k in indexed_files if k[0] == p})
            indexed_files.add(key)
            succeeded += 1
        else:
            failed += 1

    if walk_errors or not os.path.isdir(WATCH_DIR):
        if walk_errors:
            logger.warning(
                "Could not list all of %s (%s); skipping removal of vanished documents.", WATCH_DIR, walk_errors[0]
            )
        return succeeded, failed

    vanished = {path for path, _ in indexed_files if path not in present and _is_under_watch_dir(path)}
    for path in sorted(vanished):
        if remove_file(path):
            indexed_files.difference_update({k for k in indexed_files if k[0] == path})
            succeeded += 1
        else:
            failed += 1
    return succeeded, failed


def seed_indexed_sources(indexed_files: set[tuple[str, float]]) -> bool:
    """Records the documents already present in the collection as known sources.

    The set of indexed files lives in memory only, so after a restart the worker would not
    know about documents that were indexed earlier and deleted while it was down. Seeding
    them (with ``UNKNOWN_MTIME``) lets the next scan re-index those that still exist and
    remove the points of those that do not. Returns False if Qdrant could not be read.
    """
    try:
        sources = list_indexed_sources()
    except Exception as e:
        logger.warning("Could not list indexed documents: %s", e)
        return False
    known = {path for path, _ in indexed_files}
    indexed_files.update((path, UNKNOWN_MTIME) for path in sources if path not in known)
    return True


def run_worker() -> None:
    """Polls WATCH_DIR forever and keeps the Qdrant collection in line with it.

    The watch directory is never created here: in the container deployment it is a
    read-only mount that Nextcloud populates on its first start, so creating it would fail
    and restart-loop the service on a fresh node. While it is missing the worker waits
    (logged once) and looks again on every poll.
    """
    logger.info("Starting Autonomous Knowledge Indexer Worker...")
    collection_ready = ensure_qdrant_collection()

    indexed_files: set[tuple[str, float]] = set()
    sources_seeded = False
    waiting_for_watch_dir = False
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
                    collection_ready = ensure_qdrant_collection()
                if collection_ready and not sources_seeded:
                    sources_seeded = seed_indexed_sources(indexed_files)
                _, failed = scan_and_index(indexed_files)
                if failed:
                    # The collection may have been missing or dropped: re-verify before retrying.
                    collection_ready = False
        except Exception as e:
            logger.error("Indexer loop error: %s", e)
        time.sleep(POLL_INTERVAL_SECONDS)


# Backward compatibility alias
process_file = index_file

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    run_worker()
