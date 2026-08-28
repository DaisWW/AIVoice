from __future__ import annotations

import math
from typing import Any


def stored_int(
    value: Any,
    default: int = 0,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Read a possibly corrupted persisted integer without raising at an API boundary."""
    if isinstance(value, bool):
        return default
    if isinstance(value, float) and not value.is_integer():
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if minimum is not None and parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return default
    return parsed


def stored_float(
    value: Any,
    default: float = 0.0,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Read a finite persisted float, falling back when a record is malformed."""
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed):
        return default
    if minimum is not None and parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return default
    return parsed
