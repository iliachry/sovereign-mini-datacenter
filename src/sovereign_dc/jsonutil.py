"""Strict JSON encoding for API responses.

Python's ``json.dumps`` writes ``NaN`` / ``Infinity`` for non-finite floats by default. Those
tokens are not JSON: ``JSON.parse`` in a browser and most other parsers reject the whole
document. Everything the node sends to a client goes through ``dumps_strict`` instead.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

logger = logging.getLogger("smdc.jsonutil")


def replace_non_finite(value: Any) -> Any:
    """Returns ``value`` with every NaN / infinite float replaced by None (containers are copied)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: replace_non_finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [replace_non_finite(item) for item in value]
    return value


def dumps_strict(value: Any, **kwargs: Any) -> str:
    """``json.dumps`` that never emits ``NaN`` or ``Infinity``.

    The document is encoded with ``allow_nan=False``. If it holds a non-finite float, that
    value is unknown as far as a client is concerned: it is sent as ``null`` (and the fact is
    logged) rather than as an invalid token or a made-up number.
    """
    kwargs.pop("allow_nan", None)
    try:
        return json.dumps(value, allow_nan=False, **kwargs)
    except ValueError:
        logger.warning("Non-finite number in a JSON response; sending null in its place")
        return json.dumps(replace_non_finite(value), allow_nan=False, **kwargs)
