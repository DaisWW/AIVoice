from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def add_import_paths(*paths: Path) -> None:
    for path in reversed(paths):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def missing_modules(names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in names if importlib.util.find_spec(name) is None)
