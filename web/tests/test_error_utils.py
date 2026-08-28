from __future__ import annotations

from app.error_utils import safe_error_message


def test_safe_error_message_hides_windows_drive_relative_path() -> None:
    assert safe_error_message(RuntimeError("C:private"), "fallback") == "fallback"
