"""Regression tests for Tri-Council Round 42 findings."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sovereign_dc.agents import knowledge_indexer

REPO_ROOT = Path(__file__).resolve().parent.parent


def _extract_nc_snapshot() -> str:
    backup_sh = (REPO_ROOT / "software" / "backup" / "backup.sh").read_text(encoding="utf-8")
    match = re.search(r"^nc_snapshot\(\) \{\n.*?^\}", backup_sh, re.MULTILINE | re.DOTALL)
    assert match is not None, "nc_snapshot() function not found in software/backup/backup.sh"
    return match.group(0)


def test_round42_nc_snapshot_rsync_link_dest_detects_preserved_mtime_overwrites(tmp_path: Path) -> None:
    """nc_snapshot passes --checksum with --link-dest so preserved-mtime same-size updates copy fresh bytes."""
    func_def = _extract_nc_snapshot()
    vol = tmp_path / "vol"
    (vol / "data" / "admin" / "files").mkdir(parents=True)
    invoice = vol / "data" / "admin" / "files" / "invoice.txt"
    unchanged = vol / "data" / "admin" / "files" / "unchanged.txt"
    db_file = vol / "data" / "owncloud.db"

    invoice.write_bytes(b"original invoice")
    unchanged.write_bytes(b"keep me")
    db_file.write_bytes(b"sqlite")
    fixed_ts = (1_700_000_000, 1_700_000_000)
    os.utime(invoice, fixed_ts)
    os.utime(unchanged, fixed_ts)

    # Always-run argv check using an isolated rsync shim (covers containers without rsync binary).
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    calls_log = tmp_path / "rsync_calls.jsonl"
    shim = shim_dir / "rsync"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"with open({str(calls_log)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "os.makedirs(sys.argv[-1], exist_ok=True)\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)

    snap_shim = tmp_path / "snap_shim"
    prev_shim = tmp_path / "prev_shim"
    harness = (
        "set -eu\n"
        'err() { echo "$*" >&2; exit 1; }\n'
        f"{func_def}\n"
        f'NCSNAP_NEW="{snap_shim}"\n'
        f'PREV_NCSNAP="{prev_shim}"\n'
        f'nc_snapshot "{vol}" "data/owncloud.db" rsync\n'
        f'mkdir -p "{prev_shim}/volume"\n'
        f'nc_snapshot "{vol}" "data/owncloud.db" rsync\n'
    )
    shim_env = {**os.environ, "PATH": f"{shim_dir}:{os.environ.get('PATH', '')}"}
    subprocess.run(["sh", "-c", harness], env=shim_env, check=True)

    recorded_calls = [json.loads(line) for line in calls_log.read_text(encoding="utf-8").splitlines()]
    assert len(recorded_calls) == 2
    first_args, second_args = recorded_calls
    assert "--link-dest" not in first_args
    assert "--checksum" not in first_args
    assert "--checksum" in second_args
    assert "--link-dest" in second_args
    assert "--exclude" in second_args and "/data/owncloud.db" in second_args

    # End-to-end verification against real rsync when installed on the host.
    if shutil.which("rsync"):
        snap1 = tmp_path / "snap1"
        snap2 = tmp_path / "snap2"
        prev_real = tmp_path / "prev_real"
        real_harness = (
            "set -eu\n"
            'err() { echo "$*" >&2; exit 1; }\n'
            f"{func_def}\n"
            f'NCSNAP_NEW="{snap1}"\n'
            f'PREV_NCSNAP="{prev_real}"\n'
            f'nc_snapshot "{vol}" "data/owncloud.db" rsync\n'
        )
        subprocess.run(["sh", "-c", real_harness], check=True)
        snap1.rename(prev_real)

        invoice.write_bytes(b"modified invoice")
        assert len(b"modified invoice") == len(b"original invoice")
        os.utime(invoice, fixed_ts)

        real_harness_2 = (
            "set -eu\n"
            'err() { echo "$*" >&2; exit 1; }\n'
            f"{func_def}\n"
            f'NCSNAP_NEW="{snap2}"\n'
            f'PREV_NCSNAP="{prev_real}"\n'
            f'nc_snapshot "{vol}" "data/owncloud.db" rsync\n'
        )
        subprocess.run(["sh", "-c", real_harness_2], check=True)

        snap2_invoice = snap2 / "volume" / "data" / "admin" / "files" / "invoice.txt"
        prev_invoice = prev_real / "volume" / "data" / "admin" / "files" / "invoice.txt"
        snap2_unchanged = snap2 / "volume" / "data" / "admin" / "files" / "unchanged.txt"
        prev_unchanged = prev_real / "volume" / "data" / "admin" / "files" / "unchanged.txt"

        assert snap2_invoice.read_bytes() == b"modified invoice"
        assert snap2_invoice.stat().st_ino != prev_invoice.stat().st_ino
        assert snap2_unchanged.stat().st_ino == prev_unchanged.stat().st_ino
        assert not (snap2 / "volume" / "data" / "owncloud.db").exists()


def test_round42_knowledge_indexer_reindexes_when_mtime_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scan_and_index detects content/size edits even when mtime is preserved identically."""
    points: dict[Any, dict[str, Any]] = {}

    def fake_qdrant(method: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "PUT" and endpoint.startswith("/points") and payload is not None:
            for pt in payload["points"]:
                points[pt["id"]] = pt
            return {"status": "ok"}
        if method == "POST" and endpoint.startswith("/points/delete") and payload is not None:
            must = payload.get("filter", {}).get("must", [])
            paths = {c["match"]["value"] for c in must if c.get("key") == knowledge_indexer.SOURCE_PATH_FIELD}
            keep_ids: set[Any] = set()
            for cond in payload.get("filter", {}).get("must_not", []):
                keep_ids.update(cond.get("has_id", []))
            for pid in list(points):
                p_val = points[pid]["payload"]["path"]
                if p_val in paths and pid not in keep_ids:
                    del points[pid]
            return {"status": "ok"}
        raise AssertionError(f"Unexpected Qdrant call: {method} {endpoint}")

    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "invoice.md"
    doc.write_text("original invoice", encoding="utf-8")
    fixed_ts = (1_700_000_000, 1_700_000_000)
    os.utime(doc, fixed_ts)

    monkeypatch.setattr(knowledge_indexer, "WATCH_DIR", str(docs))
    monkeypatch.setattr(knowledge_indexer, "get_embedding", lambda text: [float(len(text))])
    monkeypatch.setattr(knowledge_indexer, "_qdrant_request", fake_qdrant)
    monkeypatch.setattr(knowledge_indexer, "_cleanup_pending", set())
    monkeypatch.setattr(knowledge_indexer, "_reconcile_pending", set())

    indexed: set[Any] = set()
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert [pt["payload"]["text"] for pt in points.values()] == ["original invoice"]
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)

    # Longer replacement with preserved mtime triggers re-index.
    longer = "modified invoice with additional corrected details"
    doc.write_text(longer, encoding="utf-8")
    os.utime(doc, fixed_ts)
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert [pt["payload"]["text"] for pt in points.values()] == [longer]
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)

    # Same-length replacement with preserved mtime ALSO triggers re-index.
    same_len = "modified invoice with alternate! corrected details"
    assert len(same_len) == len(longer)
    doc.write_text(same_len, encoding="utf-8")
    os.utime(doc, fixed_ts)
    assert knowledge_indexer.scan_and_index(indexed) == (1, 0)
    assert [pt["payload"]["text"] for pt in points.values()] == [same_len]

    # FileNotFoundError during version check is treated as vanished (skipped without failure).
    def raise_fnf(_path: str) -> tuple[int, int, str]:
        raise FileNotFoundError("vanished")

    monkeypatch.setattr(knowledge_indexer, "_document_version", raise_fnf)
    assert knowledge_indexer.scan_and_index(indexed) == (0, 0)

    # PermissionError (non-FileNotFound OSError) falls back to index_file / failure counting.
    def raise_perm(_path: str) -> tuple[int, int, str]:
        raise PermissionError("denied")

    monkeypatch.setattr(knowledge_indexer, "_document_version", raise_perm)
    monkeypatch.setattr(knowledge_indexer, "index_file", lambda _p: False)
    assert knowledge_indexer.scan_and_index(indexed) == (0, 1)
