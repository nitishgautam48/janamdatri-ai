from __future__ import annotations

import re

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def detect_language(text: str, default: str = "en") -> str:
    """Very small heuristic: Devanagari script → Hindi, otherwise ``default``."""
    if not text:
        return default
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return default
    deva = sum(1 for c in letters if _DEVANAGARI.match(c))
    return "hi" if deva / len(letters) > 0.3 else default
