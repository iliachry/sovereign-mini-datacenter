#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Autonomous GitLab Code Reviewer Agent
Inspects open GitLab merge request diffs and posts local LLM code reviews.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

# Logging is configured only when this file runs as a script (see the bottom of the file):
# importing it as a library must not touch the root logger.
logger = logging.getLogger("GitLabReviewer")
LOG_FORMAT = "%(asctime)s [GitLabReviewer] %(message)s"

GITLAB_URL = os.getenv("GITLAB_API_URL", "http://gitlab/api/v4")
GITLAB_TOKEN = os.getenv("GITLAB_API_TOKEN", "")
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
REVIEW_MODEL = os.getenv("OLLAMA_CODE_MODEL", "qwen2.5-coder:7b")
POLL_INTERVAL_SECONDS = 60

# Sentinel strings returned by query_ollama() when no usable review was produced.
LLM_UNAVAILABLE_PREFIX = "Local LLM review unavailable"
LLM_EMPTY_OUTPUT = "No review output."

# Hidden marker embedded in every posted note so already-reviewed revisions can be
# recognised after a restart (the in-memory set alone would be lost). The marker is only
# trusted in notes authored by the reviewer's own account: anyone who can comment on an
# MR can type it.
REVIEW_MARKER_TEMPLATE = "<!-- smdc-ai-review sha={sha} -->"

# User id of the account behind GITLAB_API_TOKEN, resolved once via GET /user.
_reviewer_user_id: int | None = None

_QUICK_ACTION_LINE = re.compile(r"^([ \t]*)/", re.MULTILINE)

ReviewKey = tuple[int, int, str]


def query_ollama(prompt: str) -> str:
    """Generates code review comments via local Ollama LLM or OpenAI-compatible SGLang endpoint."""
    openai_base = os.getenv("OPENAI_BASE_URL", OPENAI_BASE_URL).rstrip("/")
    if openai_base:
        url = f"{openai_base}/chat/completions" if openai_base.endswith("/v1") else f"{openai_base}/v1/chat/completions"
        payload = json.dumps(
            {
                "model": os.getenv("OLLAMA_CODE_MODEL", REVIEW_MODEL),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 512,
                "chat_template_kwargs": {"enable_thinking": False},
            }
        ).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                msg = data.get("choices", [{}])[0].get("message", {})
                return str(msg.get("content") or msg.get("reasoning_content") or LLM_EMPTY_OUTPUT)
        except Exception as e:
            logger.error("OpenAI/SGLang review generation failed: %s", e)
            return f"{LLM_UNAVAILABLE_PREFIX}: {e}"

    url = f"{OLLAMA_URL}/api/generate"
    payload = json.dumps(
        {"model": REVIEW_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return str(data.get("response") or LLM_EMPTY_OUTPUT)
    except Exception as e:
        logger.error("Ollama review generation failed: %s", e)
        return f"{LLM_UNAVAILABLE_PREFIX}: {e}"


def is_review_failure(review_markdown: str) -> bool:
    """True when query_ollama() returned a failure sentinel instead of a real review."""
    text = review_markdown.strip()
    return not text or text == LLM_EMPTY_OUTPUT or text.startswith(LLM_UNAVAILABLE_PREFIX)


def neutralize_quick_actions(markdown: str) -> str:
    """Escapes lines starting with ``/`` so GitLab cannot run them as quick actions.

    LLM output is untrusted (it is derived from attacker-controllable diffs); a line
    such as ``/merge`` or ``/approve`` in a note body would otherwise be executed with
    the reviewer token's permissions. A leading backslash renders as a literal slash.
    """
    return _QUICK_ACTION_LINE.sub(lambda m: m.group(1) + "\\/", markdown)


def review_code_diff(project_id: int, mr_iid: int, diff_text: str, head_sha: str = "") -> bool:
    """Sends a git diff to the local LLM and posts an automated code review comment.

    Returns True if a review was generated and posted (or printed in dry-run mode);
    False if generation or posting failed, in which case nothing is posted.
    """
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
    logger.info("Generating review for MR !%s in project %s using %s...", mr_iid, project_id, REVIEW_MODEL)
    review_markdown = query_ollama(prompt)

    if is_review_failure(review_markdown):
        logger.warning(
            "No review generated for MR !%s in project %s (%s); not posting a note.",
            mr_iid,
            project_id,
            review_markdown.strip() or "empty output",
        )
        return False

    marker = REVIEW_MARKER_TEMPLATE.format(sha=head_sha or "unknown")
    comment = (
        f"### 🤖 Sovereign AI Code Review (`{REVIEW_MODEL}`)\n\n"
        f"{neutralize_quick_actions(review_markdown)}\n\n"
        f"---\n*Generated by a locally hosted LLM; verify before acting.*\n\n{marker}"
    )

    if GITLAB_TOKEN:
        url = f"{GITLAB_URL}/projects/{project_id}/merge_requests/{mr_iid}/notes"
        payload = json.dumps({"body": comment}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"PRIVATE-TOKEN": GITLAB_TOKEN, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                logger.info("Successfully posted AI review to MR !%s.", mr_iid)
        except Exception as e:
            logger.error("Failed to post comment to GitLab: %s", e)
            return False
    else:
        logger.info("Dry-run mode (No GITLAB_API_TOKEN set). Generated review:\n%s", comment)
    return True


def _gitlab_get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": GITLAB_TOKEN})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_reviewer_user_id() -> int | None:
    """Returns the id of the account the token authenticates as (cached once resolved).

    Returns None when the identity cannot be resolved; the lookup is retried on the
    next call.
    """
    global _reviewer_user_id
    if _reviewer_user_id is not None:
        return _reviewer_user_id
    try:
        user = _gitlab_get(f"{GITLAB_URL}/user")
        user_id = user.get("id") if isinstance(user, dict) else None
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            raise ValueError("response has no integer 'id'")
    except Exception as e:
        logger.warning("Could not resolve the reviewer's GitLab identity: %s", e)
        return None
    _reviewer_user_id = user_id
    return user_id


def _note_author_id(note: dict[str, Any]) -> Any:
    author = note.get("author")
    return author.get("id") if isinstance(author, dict) else None


def already_reviewed_on_gitlab(project_id: int, mr_iid: int, head_sha: str) -> bool:
    """Checks the MR's notes for a marker for ``head_sha`` posted by this agent's own account.

    A marker in a note written by anyone else is ignored: otherwise an MR author could
    post it for the current head sha and suppress the automated review. If the
    reviewer's identity cannot be resolved no remote marker is trusted, and only the
    caller's in-memory set prevents duplicates.
    """
    marker = REVIEW_MARKER_TEMPLATE.format(sha=head_sha)
    url = f"{GITLAB_URL}/projects/{project_id}/merge_requests/{mr_iid}/notes?per_page=100&sort=desc"
    notes = _gitlab_get(url)
    if not isinstance(notes, list):
        return False
    marked = [n for n in notes if isinstance(n, dict) and marker in str(n.get("body", ""))]
    if not marked:
        return False
    reviewer_id = get_reviewer_user_id()
    if reviewer_id is None:
        logger.warning(
            "MR !%s (project %s): ignoring review marker(s) because the reviewer identity is unknown.",
            mr_iid,
            project_id,
        )
        return False
    if any(_note_author_id(n) == reviewer_id for n in marked):
        return True
    logger.warning(
        "MR !%s (project %s): ignoring review marker for %s posted by another account.",
        mr_iid,
        project_id,
        head_sha,
    )
    return False


def poll_once(reviewed: set[ReviewKey]) -> int:
    """Reviews each open MR revision at most once. Returns the number of reviews posted.

    ``reviewed`` holds (project_id, iid, head sha) keys already handled. A revision is
    recorded only after a review was actually posted, so LLM or API failures are
    retried on the next poll without leaving a note behind.
    """
    if not GITLAB_TOKEN:
        return 0

    posted = 0
    mrs = _gitlab_get(f"{GITLAB_URL}/merge_requests?state=opened&scope=all&per_page=100")
    for mr in mrs if isinstance(mrs, list) else []:
        pid = int(mr["project_id"])
        iid = int(mr["iid"])
        head_sha = str(mr.get("sha") or (mr.get("diff_refs") or {}).get("head_sha") or "")
        key: ReviewKey = (pid, iid, head_sha)
        if key in reviewed:
            continue
        try:
            if head_sha and already_reviewed_on_gitlab(pid, iid, head_sha):
                reviewed.add(key)
                continue
            diff_data = _gitlab_get(f"{GITLAB_URL}/projects/{pid}/merge_requests/{iid}/changes")
            changes = diff_data.get("changes", []) if isinstance(diff_data, dict) else []
            diff_text = "\n".join([c.get("diff", "") for c in changes])
            if not diff_text:
                reviewed.add(key)
                continue
            if review_code_diff(pid, iid, diff_text, head_sha):
                reviewed.add(key)
                posted += 1
        except Exception as e:
            logger.warning("GitLab MR !%s (project %s) check: %s", iid, pid, e)
    return posted


def run_worker() -> None:
    logger.info("Starting Autonomous GitLab Reviewer Agent...")
    reviewed: set[ReviewKey] = set()
    while True:
        try:
            poll_once(reviewed)
        except Exception as e:
            logger.warning("GitLab API check: %s", e)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    run_worker()
