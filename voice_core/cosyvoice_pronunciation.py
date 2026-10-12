from __future__ import annotations

import re

from .pronunciation import RAW_PREFIX_RE, TOKEN_RE, PronunciationError


# CosyVoice3's native pinyin tokens preserve the consonants that the historical
# Chinese character map merges (for example, ra and la).
SYLLABLE_PHONEMES = {
    "a": "[a]",
    "da": "[d][a]",
    "du": "[d][u]",
    "ei": "[ei]",
    "gu": "[g][u]",
    "ha": "[h][a]",
    "ho": "[h][o]",
    "ka": "[k][a]",
    "ku": "[k][u]",
    "la": "[l][a]",
    "li": "[l][i]",
    "lu": "[l][u]",
    "luo": "[l][uo]",
    "luu": "[l][u]",
    "ma": "[m][a]",
    "mi": "[m][i]",
    "miu": "[m][iu]",
    "mo": "[m][o]",
    "mu": "[m][u]",
    "na": "[n][a]",
    "o": "[o]",
    "pa": "[p][a]",
    "ra": "[r][a]",
    "ro": "[r][o]",
    "sa": "[s][a]",
    "shu": "[sh][u]",
    "wa": "[w][a]",
    "wu": "[w][u]",
    "xi": "[x][i]",
    "ya": "[y][a]",
}


def cosyvoice_text(pronunciation: str, generated_text: str) -> str:
    """Render the original syllable order without going through Chinese text.

    Explicit raw input is already model-specific and stays untouched. Duration
    marks remain metadata: repeating a vowel token would add a spoken unit.
    """
    if not pronunciation or RAW_PREFIX_RE.match(pronunciation):
        return generated_text
    parts: list[str] = []
    for token in TOKEN_RE.findall(pronunciation):
        if token.isascii() and token.isalpha():
            phonemes = SYLLABLE_PHONEMES.get(token.lower())
            if phonemes is None:
                raise PronunciationError("CosyVoice3 台本包含未定义的音节")
            if token.isupper():
                phonemes = "<strong>" + phonemes + "</strong>"
            parts.append(phonemes)
        else:
            separator = re.sub(r"[-—–―－↗↘]", " ", token)
            parts.append(separator)
    text = re.sub(r"\s+", " ", "".join(parts)).strip()
    # Candidate direction overrides are carried by generated_text punctuation.
    if generated_text and generated_text[-1] in ".?!。？！":
        text = text.rstrip(".?!。？！") + generated_text[-1]
    elif text and text[-1] not in ".?!。？！……":
        text += "。"
    return text
