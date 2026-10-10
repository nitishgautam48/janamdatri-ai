"""Health Assistant: user message → Gemma (+ knowledge retrieval) → safety layer → response."""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.language import detect_language
from app.ai.llm import ChatMessage, get_llm
from app.ai.prompts import ASSISTANT_SYSTEM, format_context, format_profile
from app.ai.safety import apply, check_message
from app.api.deps import Principal
from app.assistant.context import mother_profile
from app.core.enums import NotificationType, Role
from app.database.models import AshaWorker, ConversationMessage, Mother, Report
from app.knowledge.service import get_kb, source_ref
from app.notifications.service import notify

HISTORY_TURNS = 6
REPORT_INTENT = re.compile(r"\b(report|cbc|result|explain)\b|रिपोर्ट|जाँच का नतीजा", re.I)
LOW_HB_SYMPTOMS = re.compile(r"dizz|tired|weak|breathless|faint|pale|chakkar|thak|kamzor|चक्कर|थकान|कमज़ोर|कमजोर", re.I)
BP_SYMPTOMS = re.compile(r"headache|swell|blur|sir ?dard|sujan|सिरदर्द|सूजन|धुंधला", re.I)

QUICK_QUESTIONS = [
    {"text": "Explain my report", "text_hi": "मेरी रिपोर्ट समझाएँ"},
    {"text": "Low hemoglobin", "text_hi": "खून की कमी"},
    {"text": "Swelling in pregnancy", "text_hi": "गर्भावस्था में सूजन"},
    {"text": "Iron-rich foods", "text_hi": "आयरन वाला खाना"},
]


REPORT_TYPES = {"cbc": r"\bcbc\b|blood count|खून की जाँच", "prescription": r"prescription|पर्ची|dawai|दवा",
                "ultrasound": r"ultrasound|scan|sonography|अल्ट्रासाउंड", "blood": r"sugar|glucose|शुगर"}


def _report_for(db: Session, mother: Mother, message: str) -> Report | None:
    for rtype, rx in REPORT_TYPES.items():
        if re.search(rx, message, re.I):
            return db.scalar(select(Report).where(Report.mother_id == mother.id, Report.report_type == rtype,
                                                  Report.status == "analyzed").order_by(Report.created_at.desc()))
    return None


def _personal_note(profile: dict, message: str, lang: str) -> str | None:
    latest = profile.get("latest") or {}
    hb = latest.get("hemoglobin")
    if hb and hb["flag"] in ("low", "critical") and LOW_HB_SYMPTOMS.search(message):
        src = (hb.get("report_type") or "report").upper() if hb.get("report_type") == "cbc" else "report"
        return (f"आपकी पिछली {src} रिपोर्ट में हीमोग्लोबिन कम ({hb['num']:g}) था, जिससे ऐसा हो सकता है।" if lang == "hi"
                else f"Your last {src} showed low hemoglobin ({hb['num']:g}), which can cause this.")
    bp = latest.get("bp")
    if bp and bp["flag"] in ("high", "critical") and BP_SYMPTOMS.search(message):
        return (f"आपका पिछला BP ({bp['value']}) ज़्यादा था, इसलिए आज BP जाँच कराएँ।" if lang == "hi"
                else f"Your last BP reading ({bp['value']}) was high, so please get your BP checked today.")
    return None


_FEELING = re.compile(r"\b(dizz|tired|weak|pain|ache|vomit|nause|swell|worr|scared|afraid|can'?t sleep|itch|cramp|"
                      r"chakkar|dard|ulti|thakan|kamzor|soojan|dar lag|chinta)|चक्कर|दर्द|उल्टी|थकान|कमज़ोर|कमजोर|सूजन|डर|चिंता|नींद",
                      re.I)
_QUESTION = re.compile(r"\?|\b(what|how|why|when|can i|should i|is it|kya|kaise|kab|kyu|kyon)\b|क्या|कैसे|कब|क्यों", re.I)
_CLOSERS = {
    "en": ["Take care of yourself, and ask me anything, anytime.", "You're doing a wonderful job for your baby.",
           "I'm right here if you want to know more."],
    "hi": ["अपना ख़याल रखिए, और जब मन हो कुछ भी पूछिए।", "आप अपने बच्चे के लिए बहुत अच्छा कर रही हैं।",
           "और कुछ जानना हो तो मैं यहीं हूँ।"],
}


def _warm(profile: dict, message: str, body: str, lang: str) -> str:
    """Wrap a knowledge-base answer the way a caring didi would say it."""
    name = (profile.get("name") or "").split(" ")[0]
    hi = lang == "hi"
    if _FEELING.search(message):
        opener = (f"{name}, आपको तकलीफ़ हो रही है, यह सुनकर बुरा लगा। यह मदद कर सकता है:" if name else
                  "आपको तकलीफ़ हो रही है, यह सुनकर बुरा लगा। यह मदद कर सकता है:") if hi else \
                 (f"Oh {name}, I'm sorry you're feeling this way. Here's what can help:" if name else
                  "I'm sorry you're feeling this way. Here's what can help:")
    elif _QUESTION.search(message):
        opener = (f"बहुत अच्छा सवाल है, {name}!" if name else "बहुत अच्छा सवाल है!") if hi else \
                 (f"That's a good question, {name}!" if name else "That's a good question!")
    else:
        opener = (f"{name}, यह जानना ज़रूरी है:" if name else "यह जानना ज़रूरी है:") if hi else \
                 (f"{name}, here's what I know about this:" if name else "Here's what I know about this:")
    closers = _CLOSERS["hi" if hi else "en"]
    closer = closers[sum(map(ord, message)) % len(closers)]
    return f"{opener}\n\n{body}\n\n{closer}"


def _fallback_answer(profile: dict, message: str, hits: list, lang: str) -> str:
    parts = []
    note = _personal_note(profile, message, lang)
    if hits:
        top = hits[0].doc
        parts.append(top["text_hi"] if lang == "hi" and top.get("text_hi") else top["text"])
    if note:
        parts.insert(0, note)
    if not parts:
        return ("माफ़ कीजिए, इस बारे में मेरे पास पक्की जानकारी नहीं है। आशा दीदी या डॉक्टर से ज़रूर पूछिए, वे आपकी मदद करेंगी।"
                if lang == "hi" else
                "I'm sorry, I don't have reliable information on that one. Please ask your ASHA didi or doctor; they'll help you.")
    return _warm(profile, message, "\n\n".join(parts), lang)


def _actions(check, profile: dict, hits: list, is_mother: bool) -> list[dict]:
    acts: list[dict] = []
    if check.emergency or check.self_harm:
        acts.append({"type": "sos", "label": "SOS · Call 108", "label_hi": "SOS · 108 पर कॉल"})
    if check.self_harm:
        acts.append({"type": "call", "label": "Call Tele-MANAS 14416", "label_hi": "14416 पर कॉल", "number": "14416"})
    if is_mother and profile.get("has_asha"):
        acts.append({"type": "tell_asha", "label": "Tell my ASHA", "label_hi": "आशा दीदी को बताएँ"})
    cats = {h.doc["category"] for h in hits}
    if "nutrition" in cats or (profile.get("latest", {}).get("hemoglobin") or {}).get("flag") == "low":
        acts.append({"type": "topic", "label": "Iron-rich foods", "label_hi": "आयरन वाला खाना", "query": "iron rich foods"})
    if check.danger_signs or "danger_signs" in cats:
        acts.append({"type": "nearby", "label": "Nearby care", "label_hi": "नज़दीकी अस्पताल"})
    if is_mother:
        acts.append({"type": "plan", "label": "Today's wellness plan", "label_hi": "आज की योजना"})
    return acts[:4]


def chat(db: Session, p: Principal, mother: Mother | None, message: str, thread_id: uuid.UUID | None,
         language: str | None, input_mode: str = "text") -> dict:
    lang = language or detect_language(message, default=(mother.language if mother and mother.language in ("hi", "en") else "en"))
    check = check_message(message)
    profile = mother_profile(db, mother)
    thread_id = thread_id or uuid.uuid4()

    # Report questions are answered from her latest analysed report (of the type she names, if any).
    report_answer = None
    if mother and REPORT_INTENT.search(message):
        report = _report_for(db, mother, message) or profile.get("latest_report")
        report_answer = report.explanation if report else None

    hits = get_kb().search(message, k=4, week=profile.get("week"))
    history: list[ConversationMessage] = []
    if p.user:
        history = list(reversed(db.scalars(
            select(ConversationMessage).where(ConversationMessage.user_id == p.user.id,
                                              ConversationMessage.thread_id == thread_id,
                                              ConversationMessage.channel == "assistant")
            .order_by(ConversationMessage.created_at.desc()).limit(HISTORY_TURNS)).all()))

    llm = get_llm("assistant")
    raw, generated_by = None, "knowledge_base"
    if report_answer:
        raw, generated_by = report_answer, "report_analysis"
    elif llm.available:
        msgs = [ChatMessage("system", ASSISTANT_SYSTEM.format(language="Hindi" if lang == "hi" else "English")
                            + "\n\nHer health details:\n" + format_profile(profile)
                            + "\n\nContext:\n" + format_context([h.doc for h in hits]))]
        msgs += [ChatMessage(m.role, m.content) for m in history]
        msgs.append(ChatMessage("user", message))
        raw = llm.generate(msgs)
        if raw:
            generated_by = "gemma"
    if not raw:
        raw = _fallback_answer(profile, message, hits, lang)
    reply = apply(raw, check, lang, disclaimer=not report_answer)

    asha_notified = False
    if mother and check.emergency and mother.assigned_asha_id:
        asha = db.get(AshaWorker, mother.assigned_asha_id)
        if asha and asha.user_id:
            notify(db, asha.user_id, NotificationType.EMERGENCY, "Danger sign reported",
                   f"{mother.name or 'A mother'} described: " + ", ".join(d["label"] for d in check.danger_signs)
                   + ". Please call her now.", data={"mother_id": str(mother.id)}, priority="urgent")
            asha_notified = True

    if p.user:  # guests: nothing is saved
        db.add(ConversationMessage(user_id=p.user.id, mother_id=mother.id if mother else None, channel="assistant",
                                   thread_id=thread_id, role="user", content=message, language=lang,
                                   input_mode=input_mode, safety_flags=check.flags))
        db.add(ConversationMessage(user_id=p.user.id, mother_id=mother.id if mother else None, channel="assistant",
                                   thread_id=thread_id, role="assistant", content=reply, language=lang,
                                   sources=[source_ref(h.doc) for h in hits]))
        db.commit()
    return {
        "thread_id": thread_id, "reply": reply, "language": lang, "generated_by": generated_by,
        "safety": {"emergency": check.emergency, "danger_signs": check.danger_signs, "self_harm": check.self_harm},
        "asha_notified": asha_notified,
        "sources": [source_ref(h.doc) for h in hits],
        "actions": _actions(check, profile, hits, p.role == Role.MOTHER),
        "quick_questions": QUICK_QUESTIONS,
        "saved": p.user is not None,
    }


def threads(db: Session, user_id: uuid.UUID, channel: str = "assistant", limit: int = 30) -> list[dict]:
    rows = db.scalars(select(ConversationMessage).where(ConversationMessage.user_id == user_id,
                                                        ConversationMessage.channel == channel)
                      .order_by(ConversationMessage.created_at.desc()).limit(500)).all()
    grouped: dict[uuid.UUID, list[ConversationMessage]] = {}
    for m in rows:
        grouped.setdefault(m.thread_id, []).append(m)
    out = []
    for tid, msgs in grouped.items():
        msgs.sort(key=lambda m: m.created_at)
        first_user = next((m for m in msgs if m.role == "user"), msgs[0])
        last_reply = next((m for m in reversed(msgs) if m.role == "assistant"), None)
        out.append({"thread_id": tid, "title": first_user.content[:60], "started_at": msgs[0].created_at,
                    "updated_at": msgs[-1].created_at, "messages": len(msgs),
                    "preview": (last_reply.content.split("\n")[0][:90] if last_reply else None),
                    "flags": sorted({f for m in msgs for f in (m.safety_flags or [])})})
    out.sort(key=lambda t: t["updated_at"], reverse=True)
    return out[:limit]


def thread_messages(db: Session, user_id: uuid.UUID, thread_id: uuid.UUID, channel: str = "assistant") -> list[dict]:
    rows = db.scalars(select(ConversationMessage).where(ConversationMessage.user_id == user_id,
                                                        ConversationMessage.thread_id == thread_id,
                                                        ConversationMessage.channel == channel)
                      .order_by(ConversationMessage.created_at)).all()
    return [{"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at, "input_mode": m.input_mode,
             "sources": m.sources, "safety_flags": m.safety_flags} for m in rows]
