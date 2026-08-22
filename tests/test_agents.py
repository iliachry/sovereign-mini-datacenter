import os
import json
import logging
import pytest
from unittest.mock import patch, MagicMock
import urllib.error

from sovereign_dc.agents.gitlab_reviewer import query_ollama, review_code_diff, run_worker as run_gitlab_worker
from sovereign_dc.agents.knowledge_indexer import (
    chunk_text,
    ensure_qdrant_collection,
    get_embedding,
    index_file,
    run_worker as run_indexer_worker
)

# === GitLab Reviewer Tests ===

def test_gitlab_reviewer_diff_prompt():
    diff_sample = """
--- a/auth.py
+++ b/auth.py
@@ -10,3 +10,4 @@
+import secrets
+def generate_token():
+    return secrets.token_hex(32)
"""
    assert len(diff_sample) > 0

def test_gitlab_query_ollama_success():
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"response": "Looks secure. LGTM."}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = query_ollama("Review this code")
        assert "Looks secure" in res

def test_gitlab_query_ollama_failure():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        res = query_ollama("Review this code")
        assert "unavailable" in res.lower()

def test_gitlab_review_code_diff_dry_run(caplog):
    caplog.set_level(logging.INFO)
    with patch("sovereign_dc.agents.gitlab_reviewer.query_ollama", return_value="Security LGTM."):
        with patch("sovereign_dc.agents.gitlab_reviewer.GITLAB_TOKEN", ""):
            review_code_diff(project_id=42, mr_iid=7, diff_text="diff --git a/b")
            assert "Dry-run mode" in caplog.text

def test_gitlab_review_code_diff_with_token(caplog):
    caplog.set_level(logging.INFO)
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"{}"
    mock_resp.__enter__.return_value = mock_resp

    with patch("sovereign_dc.agents.gitlab_reviewer.query_ollama", return_value="AI Review Output"):
        with patch("sovereign_dc.agents.gitlab_reviewer.GITLAB_TOKEN", "glpat-fake-token"):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                review_code_diff(project_id=42, mr_iid=7, diff_text="diff --git a/b")
                assert "Successfully posted AI review to MR !7" in caplog.text

def test_gitlab_review_code_diff_post_error(caplog):
    with patch("sovereign_dc.agents.gitlab_reviewer.query_ollama", return_value="AI Review Output"):
        with patch("sovereign_dc.agents.gitlab_reviewer.GITLAB_TOKEN", "glpat-fake-token"):
            with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 403, "Forbidden", {}, None)):
                review_code_diff(project_id=42, mr_iid=7, diff_text="diff --git a/b")
                assert "Failed to post comment to GitLab" in caplog.text

def test_gitlab_run_worker(caplog):
    caplog.set_level(logging.INFO)
    mrs_resp = MagicMock()
    mrs_resp.read.return_value = json.dumps([{"project_id": 1, "iid": 10}]).encode("utf-8")
    mrs_resp.__enter__.return_value = mrs_resp

    diff_resp = MagicMock()
    diff_resp.read.return_value = json.dumps({"changes": [{"diff": "+print('hello')"}]}).encode("utf-8")
    diff_resp.__enter__.return_value = diff_resp

    with patch("sovereign_dc.agents.gitlab_reviewer.GITLAB_TOKEN", "fake-token"):
        with patch("urllib.request.urlopen", side_effect=[mrs_resp, diff_resp]):
            with patch("sovereign_dc.agents.gitlab_reviewer.review_code_diff") as mock_rev:
                with patch("time.sleep", side_effect=StopIteration("End loop")):
                    with pytest.raises(StopIteration):
                        run_gitlab_worker()
                    mock_rev.assert_called_once()


# === Knowledge Indexer Tests ===

def test_knowledge_indexer_chunking():
    text = "Word " * 1200
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) >= 2
    assert len(chunks[0].split()) == 500

def test_ensure_qdrant_collection_exists(caplog):
    caplog.set_level(logging.INFO)
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        ensure_qdrant_collection()
        assert "ready" in caplog.text

def test_ensure_qdrant_collection_created_on_404(caplog):
    caplog.set_level(logging.INFO)
    err_404 = urllib.error.HTTPError("http://qdrant/collections/sovereign_knowledge", 404, "Not Found", {}, None)
    mock_create_resp = MagicMock()
    mock_create_resp.status = 200
    mock_create_resp.__enter__.return_value = mock_create_resp

    with patch("urllib.request.urlopen", side_effect=[err_404, mock_create_resp]):
        ensure_qdrant_collection()
        assert "Created Qdrant collection" in caplog.text

def test_ensure_qdrant_collection_failure(caplog):
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Refused")):
        ensure_qdrant_collection()
        assert "not reachable" in caplog.text

def test_get_embedding():
    fake_vec = [0.1] * 768
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"embedding": fake_vec}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        vec = get_embedding("Test chunk")
        assert len(vec) == 768
        assert vec[0] == 0.1

def test_index_file(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    test_doc = tmp_path / "architecture.md"
    test_doc.write_text("# Sovereign Datacenter\nThis is a sovereign node test document.")

    fake_vec = [0.05] * 768
    mock_qdrant_resp = MagicMock()
    mock_qdrant_resp.status = 200
    mock_qdrant_resp.__enter__.return_value = mock_qdrant_resp

    with patch("sovereign_dc.agents.knowledge_indexer.get_embedding", return_value=fake_vec):
        with patch("urllib.request.urlopen", return_value=mock_qdrant_resp):
            index_file(str(test_doc))
            assert "Indexed" in caplog.text

def test_index_file_error(caplog):
    with patch("builtins.open", side_effect=IOError("Permission denied")):
        index_file("/invalid/path.md")
        assert "Failed to index" in caplog.text

def test_knowledge_indexer_run_worker(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    test_doc = tmp_path / "manual.txt"
    test_doc.write_text("Hardware assembly instructions.")

    with patch("sovereign_dc.agents.knowledge_indexer.WATCH_DIR", str(tmp_path)):
        with patch("sovereign_dc.agents.knowledge_indexer.ensure_qdrant_collection"):
            with patch("sovereign_dc.agents.knowledge_indexer.index_file") as mock_idx:
                with patch("time.sleep", side_effect=StopIteration("End loop")):
                    with pytest.raises(StopIteration):
                        run_indexer_worker()
                    mock_idx.assert_called_with(str(test_doc))
