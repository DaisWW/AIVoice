from __future__ import annotations

import re


_SENSITIVE_ERROR_MARKERS = (
    "api key",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
)
_PATH_RE = re.compile(r"[\\/]|\b[A-Za-z]:")


def safe_error_message(
    error: BaseException, fallback: str, *, max_length: int = 300
) -> str:
    """Return a short exception message safe for users and application logs."""
    try:
        message = " ".join(str(error).split())
    except Exception:
        return fallback
    lowered = message.lower()
    if (
        not message
        or any(marker in lowered for marker in _SENSITIVE_ERROR_MARKERS)
        or _PATH_RE.search(message)
    ):
        return fallback
    return message[:max_length]


def safe_exception_summary(error: BaseException, fallback: str) -> str:
    """Include only an exception type and a sanitized, bounded message."""
    return f"{type(error).__name__}: {safe_error_message(error, fallback)}"
