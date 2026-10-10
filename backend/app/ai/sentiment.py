"""Lightweight sentiment and emotional-signal detection (English, Hindi, Hinglish).

Used by the Emotional Companion to notice stress, isolation, anxiety and distress over
time. Scores are internal only: mothers never see a number, and nothing here is a
diagnosis. Swap ``analyze`` for a multilingual classifier if one is available.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ai.safety import SELF_HARM_PATTERNS

LEXICON: dict[str, list[str]] = {
    "positive": [
        r"\bhappy\b", r"\bgood\b", r"\bgreat\b", r"\bbetter\b", r"\bexcited\b", r"\bcalm\b", r"\brelaxed\b",
        r"\bgrateful\b", r"\bthank", r"\blove\b", r"\bpeaceful\b", r"\bfine\b", r"\bkhush\b", r"\baccha\b",
        r"\bachha\b", r"खुश", r"अच्छा", r"अच्छी", r"शांत", r"बढ़िया",
    ],
    "stress": [
        r"\bstress", r"\bpressure\b", r"\btired\b", r"\bexhausted\b", r"\boverwhelm", r"\btoo much work\b",
        r"\bcan'?t cope\b", r"\bthak (gayi|gai)\b", r"\btension\b", r"थक", r"तनाव", r"टेंशन", r"परेशान",
    ],
    "isolation": [
        r"\blonely\b", r"\balone\b", r"\bnobody (understands|cares|listens)", r"\bno one (understands|cares|listens|to talk)",
        r"\bisolated\b", r"\bmiss (my )?(mother|maa|family|home)", r"\bakeli\b", r"अकेल", r"कोई (नहीं|समझता)",
        r"कोई बात नहीं करता", r"\bkoi nahi samajhta\b",
    ],
    "anxiety": [
        r"\bworr(y|ied|ying)\b", r"\banxious\b", r"\bscared\b", r"\bafraid\b", r"\bfear\b", r"\bnervous\b",
        r"\bpanic", r"\bwhat if\b", r"\bdar lag", r"\bchinta\b", r"चिंता", r"डर", r"घबरा",
    ],
    "sadness": [
        r"\bsad\b", r"\bcry(ing)?\b", r"\bcried\b", r"\bdepress", r"\bhopeless\b", r"\bempty\b", r"\bworthless\b",
        r"\bdifficult\b", r"\bhard day\b", r"\bupset\b", r"\budaas\b", r"\bro rahi\b", r"उदास", r"रो", r"दुखी", r"मुश्किल",
    ],
    "sleep": [r"\bcan'?t sleep\b", r"\binsomnia\b", r"\bno sleep\b", r"\bneend nahi\b", r"नींद नहीं"],
    "conflict": [
        r"\bfight", r"\bshout", r"\bscold", r"\bin-?laws?\b", r"\bsaas\b", r"\bhusband (is )?(angry|not)", r"\bhits? me\b",
        r"\bbeat(s|en)? me\b", r"झगड़", r"डाँट", r"मारत", r"सास",
    ],
}

THEME_HINTS = {
    "family": [r"\bhusband\b", r"\bsaas\b", r"\bin-?laws?\b", r"\bfamily\b", r"\bmother\b", r"पति", r"परिवार", r"सास"],
    "baby_health": [r"\bbaby\b", r"\bbachch?a\b", r"बच्च", r"\bdelivery\b", r"प्रसव"],
    "body_changes": [r"\bweight\b", r"\bbody\b", r"\bpain\b", r"\bsleep\b", r"दर्द", r"नींद"],
    "money": [r"\bmoney\b", r"\bpaisa\b", r"\bpaise\b", r"\bexpens", r"पैस", r"खर्च"],
    "work": [r"\bwork\b", r"\bjob\b", r"\bkaam\b", r"काम"],
}


@dataclass
class EmotionAnalysis:
    sentiment: float  # -1 … 1
    signals: dict[str, int] = field(default_factory=dict)
    themes: list[str] = field(default_factory=list)
    self_harm: bool = False
    safety_concern: bool = False  # e.g. mentions of being hit

    @property
    def dominant(self) -> str | None:
        negatives = {k: v for k, v in self.signals.items() if k != "positive" and v}
        return max(negatives, key=negatives.get) if negatives else None


def _count(patterns: list[str], text: str) -> int:
    return sum(len(re.findall(p, text, re.I)) for p in patterns)


def analyze(text: str) -> EmotionAnalysis:
    t = (text or "").lower()
    signals = {k: _count(v, t) for k, v in LEXICON.items()}
    neg = sum(v for k, v in signals.items() if k != "positive")
    pos = signals["positive"]
    # Negations like "not good" / "accha nahi" flip a positive into a negative.
    negated = len(re.findall(r"\b(not|isn'?t|nahi|nahin)\s+(good|fine|happy|okay|ok|accha|achha)\b|(अच्छा|ठीक) नहीं", t))
    pos, neg = max(pos - negated, 0), neg + negated
    total = pos + neg
    sentiment = 0.0 if total == 0 else round((pos - neg) / total, 3)
    themes = [k for k, pats in THEME_HINTS.items() if _count(pats, t)]
    self_harm = any(re.search(p, t, re.I) for p in SELF_HARM_PATTERNS)
    safety_concern = bool(re.search(r"\b(hits?|beats?|beaten|hurts?) me\b|मारत|मारते|पीटत", t))
    return EmotionAnalysis(sentiment=sentiment, signals=signals, themes=themes, self_harm=self_harm,
                           safety_concern=safety_concern)


def concern_level(signal_totals: dict[str, int], avg_sentiment: float, self_harm: bool,
                  safety_concern: bool = False) -> str:
    """Session-level concern from accumulated signals. none | mild | elevated | high."""
    if self_harm:
        return "high"
    distress = sum(signal_totals.get(k, 0) for k in ("sadness", "isolation", "anxiety", "stress", "sleep", "conflict"))
    if safety_concern or (distress >= 6 and avg_sentiment < -0.4):
        return "elevated"
    if distress >= 2 or avg_sentiment < -0.3:
        return "mild"
    return "none"
