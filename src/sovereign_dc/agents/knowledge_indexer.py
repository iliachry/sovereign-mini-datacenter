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
WATCH_DIR = os.getenv("DOCS_WATCH_DIR", "/data/documents")


def get_current_model() -> str:
    """Reads the current Sentinel mode and returns the appropriate embedding model."""
    try:
        if os.path.exists("/tmp/sovereign_mode"):
            with open("/tmp/sovereign_mode") as f:
                mode = f.read().strip()
            if mode == "ECO_PRESERVATION":
                return ECO_EMBEDDING_MODEL
    except Exception as e:
        logging.warning(f"Failed to read sovereign mode: {e}")
    return DEFAULT_EMBEDDING_MODEL


def ensure_qdrant_collection():
    """Creates the Qdrant collection if it does not exist."""
    url = f"{QDRANT_URL}/collections/{COLLECTION_NAME}"
    try:
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                logging.info(f"Qdrant collection '{COLLECTION_NAME}' ready.")
                return
    except urllib.error.HTTPError as e:
        if e.code == 404:
            payload = json.dumps({"vectors": {"size": 768, "distance": "Cosine"}}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="PUT")
            with urllib.request.urlopen(req, timeout=5) as resp:
                logging.info(f"Created Qdrant collection '{COLLECTION_NAME}'.")
        else:
            logging.error(f"Error checking Qdrant: {e}")
    except Exception as e:
        logging.warning(f"Qdrant not reachable yet: {e}")


def get_embedding(text: str) -> list[float]:
    """Generates embedding vector via local Ollama API."""
    url = f"{OLLAMA_URL}/api/embeddings"
    current_model = get_current_model()
    payload = json.dumps({"model": current_model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("embedding", [0.0] * 768)


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


def index_file(filepath: str) -> None:
    """Chunks and embeds a document, then stores vectors in Qdrant."""
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
                logging.info(f"Indexed {len(points)} chunks from '{os.path.basename(filepath)}'.")
    except Exception as e:
        logging.error(f"Failed to index {filepath}: {e}")


def run_worker():
    logging.info("Starting Autonomous Knowledge Indexer Worker...")
    ensure_qdrant_collection()
    os.makedirs(WATCH_DIR, exist_ok=True)

    indexed_files = set()
    last_mode = get_current_model()

    while True:
        try:
            current_mode = get_current_model()
            if current_mode != last_mode:
                logging.info(f"🔄 Swapping AI Model due to Sentinel mode change: {last_mode} -> {current_mode}")
                last_mode = current_mode

            for root, _, files in os.walk(WATCH_DIR):
                for f in files:
                    if f.endswith((".md", ".txt", ".csv", ".json", ".py", ".sh")):
                        p = os.path.join(root, f)
                        mtime = os.path.getmtime(p)
                        key = (p, mtime)
                        if key not in indexed_files:
                            logging.info(f"New or modified document detected: {f}")
                            index_file(p)
                            indexed_files.add(key)
        except Exception as e:
            logging.error(f"Indexer loop error: {e}")
        time.sleep(30)


# Backward compatibility alias
process_file = index_file

if __name__ == "__main__":
    run_worker()
