from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MAX_SCRIPT_ITEMS = 3000


@dataclass(frozen=True)
class ScriptItem:
    order: int
    source_line: int
    text: str
    pronunciation: str
    generated_text: str
    direction: str
    emphasis: tuple[str, ...]
    hold_units: tuple[int, ...] = ()
    raw_mode: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "source_line": self.source_line,
            "text": self.text,
            "pronunciation": self.pronunciation,
            "generated_text": self.generated_text,
            "direction": self.direction,
            "emphasis": list(self.emphasis),
            "hold_units": list(self.hold_units),
            "syllable_count": len(self.hold_units),
            "raw_mode": self.raw_mode,
        }
