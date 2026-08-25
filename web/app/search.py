from __future__ import annotations

import re
from collections.abc import Iterable

from pypinyin import Style, lazy_pinyin


def normalize_search(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def search_tokens(*values: object) -> tuple[str, ...]:
    tokens: list[str] = []
    for value in values:
        visible = str(value or "")
        if not visible:
            continue
        tokens.extend(
            (
                normalize_search(visible),
                normalize_search("".join(lazy_pinyin(visible))),
                normalize_search(
                    "".join(lazy_pinyin(visible, style=Style.FIRST_LETTER))
                ),
            )
        )
    return tuple(dict.fromkeys(token for token in tokens if token))


def search_text(*values: object) -> str:
    return " ".join(search_tokens(*values))


def matches_search(query: str, values: Iterable[object]) -> bool:
    needle = normalize_search(query)
    return bool(needle) and any(needle in token for token in search_tokens(*values))
