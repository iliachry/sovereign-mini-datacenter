"""Regression tests for tri-council review round 22.

1. ``gitlab_reviewer.neutralize_quick_actions()`` strips ``\\r`` (matching GitLab's
   ``content.delete!("\\r")``) and escapes ``/`` after any horizontal/non-newline
   whitespace so ``\\r/merge`` or ``Review.\\n\\r/close`` cannot bypass escaping.
2. ``COMMERCIALIZATION.md`` Year-3 EBITDA and Net Operating Cash rows (``+$0.40M``)
   match ``gross profit - operating expenses`` across all 5 years.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from sovereign_dc.agents import gitlab_reviewer

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "payload",
    [
        "\r/merge",
        "Review complete.\n\r/close",
        "Review complete.\r\n/approve",
        "  \r\t/reassign @attacker",
        "\u00a0/merge",
        "Line 1\r/spend 100h",
    ],
)
def test_neutralize_quick_actions_defeats_carriage_return_bypass(payload: str) -> None:
    escaped = gitlab_reviewer.neutralize_quick_actions(payload)
    assert "\r" not in escaped
    # Emulate GitLab's extractor: content.delete!("\r") then check each line for /^\s*\//
    gitlab_normalized = escaped.replace("\r", "")
    for line in gitlab_normalized.split("\n"):
        assert not line.lstrip().startswith("/"), f"Unescaped quick action line: {line!r}"

    # Verify end-to-end through review_code_diff POST payload interception
    posted_bodies: list[str] = []

    class FakeResp:
        status = 201

        def read(self) -> bytes:
            return b"{}"

        def __enter__(self) -> FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def capture_urlopen(req: object, timeout: float = 30) -> FakeResp:
        data = getattr(req, "data", b"") or b""
        if data:
            import json

            posted_bodies.append(json.loads(data.decode("utf-8"))["body"])
        return FakeResp()

    with (
        patch.object(gitlab_reviewer, "GITLAB_TOKEN", "glpat-test"),
        patch.object(gitlab_reviewer, "query_ollama", return_value=payload),
        patch("urllib.request.urlopen", side_effect=capture_urlopen),
    ):
        assert gitlab_reviewer.review_code_diff(1, 2, "+x = 1", head_sha="abc1234") is True

    assert len(posted_bodies) == 1
    for line in posted_bodies[0].replace("\r", "").split("\n"):
        assert not line.lstrip().startswith("/")


def test_commercialization_all_five_years_ebitda_match_stated_derivation() -> None:
    comm = (ROOT / "COMMERCIALIZATION.md").read_text(encoding="utf-8")
    assert "EBITDA                -$0.33M      -$0.23M      +$0.40M      +$1.99M      +$7.62M" in comm
    assert "Net Operating Cash    -$0.33M      -$0.23M      +$0.40M      +$1.99M      +$7.62M" in comm

    nodes = [45, 180, 620, 1850, 4200]
    arr = [0.11, 0.54, 2.02, 6.44, 16.45]
    srv = [0.25, 0.85, 2.40, 6.20, 12.50]
    opex = [0.86, 2.35, 6.79, 19.36, 41.66]
    expected_ebitda = [-0.33, -0.23, 0.40, 1.99, 7.62]
    for y in range(5):
        gp_exact = nodes[y] * 0.006799 + arr[y] * 0.88 + srv[y] * 0.50
        gp_rounded = round(gp_exact, 2)
        assert round(gp_exact - opex[y], 2) == expected_ebitda[y]
        assert round(gp_rounded - opex[y], 2) == expected_ebitda[y]
