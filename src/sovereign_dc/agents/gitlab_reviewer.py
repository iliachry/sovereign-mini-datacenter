#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Autonomous GitLab Code Reviewer Agent
Inspects open GitLab merge request diffs and posts local LLM code reviews.
"""

import os
import sys
import time
import json
import logging
import urllib.request
import urllib.error

logging.basicConfig(level=logging.INFO, format="%(asctime)s [GitLabReviewer] %(message)s")

GITLAB_URL = os.getenv("GITLAB_API_URL", "http://gitlab/api/v4")
GITLAB_TOKEN = os.getenv("GITLAB_API_TOKEN", "")
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
REVIEW_MODEL = os.getenv("OLLAMA_CODE_MODEL", "qwen2.5-coder:7b")

def query_ollama(prompt: str) -> str:
    """Generates code review comments via local Ollama LLM."""
    url = f"{OLLAMA_URL}/api/generate"
    payload = json.dumps({
        "model": REVIEW_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2}
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "No review output.")
    except Exception as e:
        logging.error(f"Ollama review generation failed: {e}")
        return f"Local LLM review unavailable: {e}"

def review_code_diff(project_id: int, mr_iid: int, diff_text: str):
    """Generates a structured review and posts it to GitLab MR notes."""
    prompt = f"""
You are an expert software engineer and security auditor running on the Sovereign Mini Datacenter.
Please review the following code changes from a GitLab Merge Request. Focus on:
1. Security vulnerabilities (input validation, auth, secrets, memory safety)
2. Logic bugs or edge cases
3. Performance and resource optimization

Code Diff:
```diff
{diff_text[:4000]}
```

Provide a concise, constructive markdown review with bullet points and code suggestions.
"""
    logging.info(f"Generating review for MR !{mr_iid} in project {project_id} using {REVIEW_MODEL}...")
    review_markdown = query_ollama(prompt)

    comment = f"### 🤖 Sovereign AI Code Review (`{REVIEW_MODEL}`)\n\n{review_markdown}\n\n---\n*100% locally evaluated on DGX GPU.*"

    if GITLAB_TOKEN:
        url = f"{GITLAB_URL}/projects/{project_id}/merge_requests/{mr_iid}/notes"
        payload = json.dumps({"body": comment}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={
            "PRIVATE-TOKEN": GITLAB_TOKEN,
            "Content-Type": "application/json"
        }, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                logging.info(f"Successfully posted AI review to MR !{mr_iid}.")
        except Exception as e:
            logging.error(f"Failed to post comment to GitLab: {e}")
    else:
        logging.info(f"Dry-run mode (No GITLAB_API_TOKEN set). Generated review:\n{comment}")

def run_worker():
    logging.info("Starting Autonomous GitLab Reviewer Agent...")
    while True:
        try:
            if GITLAB_TOKEN:
                url = f"{GITLAB_URL}/merge_requests?state=opened"
                req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": GITLAB_TOKEN})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    mrs = json.loads(resp.read().decode("utf-8"))
                    for mr in mrs:
                        pid = mr["project_id"]
                        iid = mr["iid"]
                        diff_url = f"{GITLAB_URL}/projects/{pid}/merge_requests/{iid}/changes"
                        req_diff = urllib.request.Request(diff_url, headers={"PRIVATE-TOKEN": GITLAB_TOKEN})
                        with urllib.request.urlopen(req_diff, timeout=10) as d_resp:
                            diff_data = json.loads(d_resp.read().decode("utf-8"))
                            changes = diff_data.get("changes", [])
                            diff_text = "\n".join([c.get("diff", "") for c in changes])
                            if diff_text:
                                review_code_diff(pid, iid, diff_text)
        except Exception as e:
            logging.warning(f"GitLab API check: {e}")
        time.sleep(60)

if __name__ == "__main__":
    run_worker()
