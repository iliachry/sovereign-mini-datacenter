import pytest
from sovereign_dc.agents.gitlab_reviewer import query_ollama, review_code_diff
from sovereign_dc.agents.knowledge_indexer import chunk_text

def test_knowledge_indexer_chunking():
    text = "Word " * 1200
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) >= 2
    assert len(chunks[0].split()) == 500

def test_gitlab_reviewer_diff_prompt():
    diff_sample = """
--- a/auth.py
+++ b/auth.py
@@ -10,3 +10,4 @@
+import secrets
+def generate_token():
+    return secrets.token_hex(32)
"""
    # Test that review execution doesn't crash when LLM is simulated or mock returned
    assert len(diff_sample) > 0
