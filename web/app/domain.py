from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
        }
