#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Autonomous Document & Knowledge Base Indexer
Extracts, chunks, embeds via local Ollama, and indexes into Qdrant for Open-WebUI RAG.
"""

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [KnowledgeIndexer] %(message)s")

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
QDRANT_URL = os.getenv("QDRANT_BASE_URL", "http://qdrant:6333")
DEFAULT_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
ECO_EMBEDDING_MODEL = os.getenv("ECO_EMBEDDING_MODEL", "all-minilm")
COLLECTION_NAME = "sovereign_knowledge"
# Vector size of the Qdrant collection. Every embedding written must have this size.
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
# Shared Sentinel mode file. It must live on a volume shared with sentinel_copilot.
MODE_FILE = os.getenv("SOVEREIGN_MODE_FILE", "/tmp/sovereign_mode")
# Sentinel modes in which batch vectorisation is deferred to conserve power.
POWER_SAVING_MODES = frozenset({"ECO_PRESERVATION", "CRITICAL_SHEDDING"})
POLL_INTERVAL_SECONDS = 30
WATCH_DIR = os.getenv("DOCS_WATCH_DIR", "/data/documents")


def get_current_mode() -> str:
    """Reads the current Sentinel mode from the shared mode file ("NORMAL" if absent)."""
    mode_file = os.getenv("SOVEREIGN_MODE_FILE", MODE_FILE)
    try:
        if os.path.exists(mode_file):
            with open(mode_file, encoding="utf-8") as f:
                return f.read().strip() or "NORMAL"
    except Exception as e:
        logging.warning("Failed to read sovereign mode: %s", e)
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
                logging.info("Qdrant collection '%s' ready.", COLLECTION_NAME)
                return True
            logging.error("Unexpected Qdrant status %s for collection '%s'.", resp.status, COLLECTION_NAME)
            return False
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logging.error("Error checking Qdrant: %s", e)
            return False
    except Exception as e:
        logging.warning("Qdrant not reachable yet: %s", e)
        return False

    try:
        payload = json.dumps({"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="PUT")
        with urllib.request.urlopen(req, timeout=5):
            logging.info("Created Qdrant collection '%s'.", COLLECTION_NAME)
        return True
    except Exception as e:
        logging.error("Failed to create Qdrant collection '%s': %s", COLLECTION_NAME, e)
        return False


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


def index_file(filepath: str) -> bool:
    """Chunks and embeds a document, then stores vectors in Qdrant.

    Returns True only if every chunk was embedded and stored (or the file is empty).
    """
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            content = f.read()

        chunks = chunk_text(content)
        points = []

        for idx, chunk in enumerate(chunks):
            point_id = int(hashlib.md5(f"{filepath}-{idx}".encode()).hexdigest()[:8], 16)
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

        if points:
            url = f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points"
            payload = json.dumps({"points": points}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="PUT")
            with urllib.request.urlopen(req, timeout=10):
                logging.info("Indexed %d chunks from '%s'.", len(points), os.path.basename(filepath))
        return True
    except Exception as e:
        logging.error("Failed to index %s: %s", filepath, e)
        return False


def scan_and_index(indexed_files: set[tuple[str, float]]) -> tuple[int, int]:
    """Indexes new/modified documents under WATCH_DIR. Returns (succeeded, failed).

    A file is recorded in ``indexed_files`` only after it was indexed successfully, so
    failures are retried on the next scan.
    """
    succeeded = 0
    failed = 0
    for root, _, files in os.walk(WATCH_DIR):
        for f in files:
            if f.endswith((".md", ".txt", ".csv", ".json", ".py", ".sh")):
                p = os.path.join(root, f)
                key = (p, os.path.getmtime(p))
                if key in indexed_files:
                    continue
                logging.info("New or modified document detected: %s", f)
                if index_file(p):
                    indexed_files.add(key)
                    succeeded += 1
                else:
                    failed += 1
    return succeeded, failed


def run_worker() -> None:
    logging.info("Starting Autonomous Knowledge Indexer Worker...")
    collection_ready = ensure_qdrant_collection()
    os.makedirs(WATCH_DIR, exist_ok=True)

    indexed_files: set[tuple[str, float]] = set()
    last_mode = get_current_mode()

    while True:
        try:
            current_mode = get_current_mode()
            if current_mode != last_mode:
                logging.info("🔄 Sentinel mode change: %s -> %s", last_mode, current_mode)
                last_mode = current_mode

            if current_mode in POWER_SAVING_MODES:
                logging.info("Sentinel mode %s: deferring batch vectorisation to conserve power.", current_mode)
            else:
                if not collection_ready:
                    collection_ready = ensure_qdrant_collection()
                _, failed = scan_and_index(indexed_files)
                if failed:
                    # The collection may have been missing or dropped: re-verify before retrying.
                    collection_ready = False
        except Exception as e:
            logging.error("Indexer loop error: %s", e)
        time.sleep(POLL_INTERVAL_SECONDS)


# Backward compatibility alias
process_file = index_file

if __name__ == "__main__":
    run_worker()
