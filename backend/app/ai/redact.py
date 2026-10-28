"""Remove personal details before text is sent to a cloud AI provider.

Names, phone numbers, e-mail addresses, Aadhaar-style numbers, pincodes and health IDs are replaced with
placeholders; the mother's name comes back in the reply (``restore``), so answers stay personal while the
provider never sees who she is. Health details needed for a useful answer (week, symptoms, report values) stay.
"""
from __future__ import annotations

import re

NAME = "[NAME]"
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_AADHAAR = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
_PIN = re.compile(r"(?i)\b(pin\s*code|pincode|pin)(\s*[:\-]?\s*)[1-9]\d{5}(?!\d)")
_IDS = re.compile(r"\b(?:RCH|ABHA|JMD|MCTS|UP|RJ)-[A-Z0-9][A-Z0-9-]{3,}\b")
_NAME_LINE = re.compile(r"^(Name|Husband|Contact|Village|Address)\s*:\s*(.+)$", re.M | re.I)


def _names_from(text: str) -> list[str]:
    names = []
    for m in _NAME_LINE.finditer(text):
        if m.group(1).lower() in ("name", "husband", "contact"):
            names += [w for w in re.split(r"[\s,]+", m.group(2).strip()) if len(w) > 2]
    return names


def scrub(text: str, names: list[str] | None = None) -> str:
    if not text:
        return text
    names = sorted({n for n in (names or []) if n and len(n) > 2}, key=len, reverse=True)
    out = _NAME_LINE.sub(lambda m: f"{m.group(1)}: {NAME}" if m.group(1).lower() == "name" else "", text)
    for n in names:
        out = re.sub(rf"\b{re.escape(n)}\b", NAME, out, flags=re.I)
    out = _EMAIL.sub("[EMAIL]", out)
    out = _PHONE.sub("[PHONE]", out)
    out = _AADHAAR.sub("[ID]", out)
    out = _IDS.sub("[ID]", out)
    out = _PIN.sub(lambda m: m.group(1) + m.group(2) + "[PINCODE]", out)
    return re.sub(r"\n{3,}", "\n\n", out)


def scrub_messages(messages: list) -> tuple[list[dict], str | None]:
    """Scrub chat messages; returns (payload, first name to restore in the reply)."""
    names: list[str] = []
    for m in messages:
        names += _names_from(m.content)
    first = names[0] if names else None
    return [{"role": m.role, "content": scrub(m.content, names)} for m in messages], first


def restore(reply: str | None, first_name: str | None) -> str | None:
    if not reply:
        return reply
    return reply.replace(NAME, first_name) if first_name else reply.replace(NAME + ", ", "").replace(NAME, "")
