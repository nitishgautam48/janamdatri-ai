"""Emotional Companion ("Talk To Me").

A friend-like space, not a questionnaire. Each message is analysed for sentiment and
emotional signals (stress, isolation, anxiety, distress), accumulated per session, and
summarised into a small memory so later conversations feel continuous. Only the mother
can read her conversations. If concern is high, her ASHA is asked to check in gently,
without seeing what she wrote, and she is told so.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.language import detect_language
from app.ai.llm import ChatMessage, get_llm
from app.ai.prompts import EMOTIONAL_SYSTEM
from app.ai.safety import CRISIS_NOTE_EN, CRISIS_NOTE_HI, scrub
from app.ai.sentiment import analyze, concern_level
from app.core.enums import Mood, NotificationType
from app.database.base import utcnow
from app.database.models import AshaWorker, ConversationMessage, EmotionalSession, Mother, User
from app.notifications.service import notify

SESSION_GAP = timedelta(hours=6)
HISTORY_TURNS = 10
CLOSING_AFTER = 6

MOOD_OPENERS = {
    Mood.GOOD: ("That's lovely to hear, {name}. What made today feel good?", "यह सुनकर अच्छा लगा, {name}। आज क्या अच्छा रहा?"),
    Mood.OKAY: ("Thank you for checking in, {name}. Some days are just okay, and that's fine. How has your day been?", "बताने के लिए शुक्रिया, {name}। कुछ दिन बस ठीक-ठाक होते हैं। आज का दिन कैसा रहा?"),
    Mood.LOW: ("I'm here with you, {name}. Feeling low is hard. Would you like to tell me what's been on your mind?", "मैं आपके साथ हूँ, {name}। मन उदास होना मुश्किल होता है। क्या आप बताना चाहेंगी कि मन में क्या चल रहा है?"),
    Mood.WORRIED: ("It sounds like something is weighing on you, {name}. I'm listening. What's worrying you?", "लगता है कुछ बात आपको परेशान कर रही है, {name}। मैं सुन रही हूँ। किस बात की चिंता है?"),
}

REFLECTIONS = {
    "isolation": [
        ("I'm really glad you told me, {name}. Feeling lonely in pregnancy is more common than people say. What made today hard? Take your time.",
         "आपने बताया, इसके लिए शुक्रिया, {name}। गर्भावस्था में अकेलापन जितना लोग कहते हैं, उससे ज़्यादा आम है। आज क्या मुश्किल लगा? आराम से बताइए।"),
        ("It sounds like you've been carrying a lot on your own. Is there someone, even one person, you feel a little easier talking to?",
         "लगता है आप बहुत कुछ अकेले सँभाल रही हैं। क्या कोई एक इंसान है जिससे बात करके आपको थोड़ा हल्का लगता है?"),
    ],
    "anxiety": [
        ("Worrying shows how much you already care for your baby. Would you like to tell me what worries you the most right now?",
         "चिंता होना दिखाता है कि आप अपने बच्चे की कितनी परवाह करती हैं। अभी सबसे ज़्यादा किस बात की चिंता है?"),
        ("That sounds really unsettling. Sometimes slow breathing helps a little: in for 4, out for 6. What would make tomorrow feel a bit lighter?",
         "यह सच में बेचैन करने वाला लगता है। धीमी साँस मदद कर सकती है: 4 गिनती अंदर, 6 बाहर। कल को थोड़ा हल्का क्या बना सकता है?"),
    ],
    "stress": [
        ("You're doing so much, and your body is working hard too. It's okay to feel tired. What is taking most of your energy these days?",
         "आप बहुत कुछ कर रही हैं, और आपका शरीर भी मेहनत कर रहा है। थकना ठीक है। इन दिनों सबसे ज़्यादा ताक़त किसमें जा रही है?"),
        ("That's a lot on your plate. Is there one small thing someone at home could take off your hands this week?",
         "यह बहुत सारा काम है। क्या घर में कोई इस हफ़्ते एक छोटा काम आपसे ले सकता है?"),
    ],
    "sadness": [
        ("I'm sorry today felt so heavy, {name}. Your feelings matter. Do you want to tell me more about it?",
         "मुझे दुख है कि आज इतना भारी लगा, {name}। आपकी भावनाएँ मायने रखती हैं। क्या आप और बताना चाहेंगी?"),
        ("Thank you for trusting me with this. Hard days don't last forever, and you don't have to go through them alone.",
         "मुझ पर भरोसा करने के लिए शुक्रिया। मुश्किल दिन हमेशा नहीं रहते, और आपको इन्हें अकेले नहीं झेलना है।"),
    ],
    "sleep": [
        ("Not sleeping well makes everything feel harder. Lying on your left side with a pillow between your knees can help. What keeps you awake?",
         "नींद न आए तो सब कुछ और मुश्किल लगता है। बाईं करवट, घुटनों के बीच तकिया रखकर लेटना मदद कर सकता है। क्या बात जगाए रखती है?"),
    ],
    "conflict": [
        ("That sounds really painful. You deserve to be treated with care, especially now. Are you safe at home?",
         "यह सच में दुखद है। आप प्यार और इज़्ज़त की हक़दार हैं, ख़ासकर अभी। क्या आप घर पर सुरक्षित हैं?"),
    ],
    "positive": [
        ("That's wonderful, {name}! Moments like these are worth holding on to. What else made you smile?",
         "यह तो बहुत अच्छा है, {name}! ऐसे पल सँजोकर रखने लायक हैं। और किस बात ने मुस्कान दी?"),
    ],
    "neutral": [
        ("I'm listening, {name}. Tell me more, whenever you're ready.", "मैं सुन रही हूँ, {name}। जब मन हो, और बताइए।"),
        ("Thank you for sharing that with me. How are you feeling about it?", "मुझसे साझा करने के लिए शुक्रिया। इस बारे में आप कैसा महसूस करती हैं?"),
    ],
}
SAFETY_NOTE = ("If anyone is hurting you, it is not your fault. You can call 181 (Women Helpline) or 112 any time.",
               "अगर कोई आपको चोट पहुँचाता है, तो यह आपकी गलती नहीं है। आप कभी भी 181 (महिला हेल्पलाइन) या 112 पर कॉल कर सकती हैं।")
CLOSING = ("Before we finish, would you like to tell me anything else? I'm always here to listen.",
           "बात ख़त्म करने से पहले, क्या आप कुछ और बताना चाहेंगी? मैं हमेशा सुनने के लिए यहाँ हूँ।")
ASHA_NOTE = ("I've asked your ASHA didi to check in on you. She won't see what you wrote here.",
             "मैंने आपकी आशा दीदी से आपका हाल पूछने को कहा है। आपने जो लिखा, वह उन्हें नहीं दिखेगा।")
GOODBYE_WORDS = ("bye", "good night", "thank you", "thanks", "ok bye", "बाय", "शुक्रिया", "धन्यवाद", "chalti hoon", "चलती हूँ")


def _session(db: Session, mother: Mother, user: User, session_id: uuid.UUID | None) -> EmotionalSession:
    if session_id:
        s = db.get(EmotionalSession, session_id)
        if s and s.mother_id == mother.id:
            return s
    last = db.scalar(select(EmotionalSession).where(EmotionalSession.mother_id == mother.id)
                     .order_by(EmotionalSession.updated_at.desc()))
    if last and last.last_message_at and utcnow() - _aware(last.last_message_at) < SESSION_GAP:
        return last
    s = EmotionalSession(mother_id=mother.id, user_id=user.id, signals={}, themes=[])
    db.add(s)
    db.flush()
    return s


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=utcnow().tzinfo)


def _memory(db: Session, mother: Mother, current: EmotionalSession) -> str:
    rows = db.scalars(select(EmotionalSession).where(EmotionalSession.mother_id == mother.id,
                                                     EmotionalSession.id != current.id,
                                                     EmotionalSession.memory_summary.is_not(None))
                      .order_by(EmotionalSession.updated_at.desc()).limit(3)).all()
    return " ".join(r.memory_summary for r in rows) or "This is your first conversation."


def _summarise(s: EmotionalSession) -> str:
    feelings = [k for k, v in sorted((s.signals or {}).items(), key=lambda kv: -kv[1]) if v and k != "positive"][:2]
    parts = []
    if s.mood_checkin:
        parts.append(f"checked in feeling {s.mood_checkin}")
    if feelings:
        parts.append("spoke about feeling " + " and ".join({"isolation": "lonely", "anxiety": "worried", "stress": "stressed",
                                                              "sadness": "low", "sleep": "unable to sleep",
                                                              "conflict": "upset by conflict at home"}.get(f, f) for f in feelings))
    if s.themes:
        parts.append("topics: " + ", ".join(t.replace("_", " ") for t in s.themes[:3]))
    if (s.signals or {}).get("positive"):
        parts.append("also shared good moments")
    return (f"On {s.created_at:%d %b} she " + "; ".join(parts) + ".") if parts else None


def _fallback(analysis, s: EmotionalSession, name: str, lang: str) -> str:
    key = analysis.dominant or ("positive" if analysis.sentiment > 0.2 else "neutral")
    options = REFLECTIONS.get(key) or REFLECTIONS["neutral"]
    en, hi = options[(s.message_count // 2) % len(options)]
    return (hi if lang == "hi" else en).format(name=name)


def chat(db: Session, mother: Mother, user: User, message: str | None, mood: Mood | None,
         session_id: uuid.UUID | None, language: str | None) -> dict:
    s = _session(db, mother, user, session_id)
    name = (mother.name or "").split(" ")[0] or ("सखी" if mother.language == "hi" else "friend")
    lang = language or (detect_language(message, default=mother.language if mother.language in ("hi", "en") else "en")
                        if message else (mother.language if mother.language in ("hi", "en") else "en"))
    hi = lang == "hi"
    now = utcnow()
    if mood:
        s.mood_checkin = mood.value
    if not message:
        reply = (MOOD_OPENERS[mood or Mood.OKAY][1 if hi else 0]).format(name=name)
        db.add(ConversationMessage(user_id=user.id, mother_id=mother.id, channel="emotional", thread_id=s.id,
                                   role="assistant", content=reply, language=lang))
        s.last_message_at = now
        db.commit()
        return _out(s, reply, lang, None, False, False)

    analysis = analyze(message)
    totals = dict(s.signals or {})
    for k, v in analysis.signals.items():
        totals[k] = totals.get(k, 0) + v
    if analysis.self_harm:
        totals["self_harm"] = totals.get("self_harm", 0) + 1
    if analysis.safety_concern:
        totals["safety_concern"] = totals.get("safety_concern", 0) + 1
    s.signals = totals
    n_user = s.message_count // 2 + 1
    s.sentiment = round((s.sentiment * (n_user - 1) + analysis.sentiment) / n_user, 3)
    s.themes = sorted(set(s.themes or []) | set(analysis.themes))
    s.concern_level = concern_level(totals, s.sentiment, bool(totals.get("self_harm")),
                                    bool(totals.get("safety_concern")))
    if mood in (Mood.LOW, Mood.WORRIED) and s.concern_level == "none":
        s.concern_level = "mild"

    history = list(reversed(db.scalars(select(ConversationMessage).where(
        ConversationMessage.thread_id == s.id, ConversationMessage.channel == "emotional")
        .order_by(ConversationMessage.created_at.desc()).limit(HISTORY_TURNS)).all()))
    llm = get_llm("emotional")
    reply = None
    if llm.available:
        msgs = [ChatMessage("system", EMOTIONAL_SYSTEM.format(language="Hindi" if hi else "English",
                                                              memory=_memory(db, mother, s)))]
        msgs += [ChatMessage(m.role, m.content) for m in history]
        msgs.append(ChatMessage("user", message))
        reply = llm.generate(msgs, temperature=0.7, max_tokens=200)
    reply = scrub(reply) if reply else _fallback(analysis, s, name, lang)
    if analysis.self_harm:
        reply = (CRISIS_NOTE_HI if hi else CRISIS_NOTE_EN) + "\n\n" + reply
    elif analysis.safety_concern:
        reply += "\n\n" + SAFETY_NOTE[1 if hi else 0]

    asha_requested = False
    if s.concern_level in ("high", "elevated") and not s.asha_flagged and mother.assigned_asha_id:
        asha = db.get(AshaWorker, mother.assigned_asha_id)
        if asha and asha.user_id:
            # Privacy: the ASHA gets a nudge to check in, never the conversation.
            notify(db, asha.user_id, NotificationType.WELLBEING, "Gentle check-in requested",
                   f"{mother.name or 'A mother'} may need some emotional support. Please visit or call her soon.",
                   data={"mother_id": str(mother.id)}, priority="urgent" if s.concern_level == "high" else "high")
            s.asha_flagged = True
            asha_requested = True
            reply += "\n\n" + ASHA_NOTE[1 if hi else 0]

    s.message_count += 2
    s.last_message_at = now
    db.add(ConversationMessage(user_id=user.id, mother_id=mother.id, channel="emotional", thread_id=s.id, role="user",
                               content=message, language=lang,
                               safety_flags=["self_harm"] if analysis.self_harm else []))
    db.add(ConversationMessage(user_id=user.id, mother_id=mother.id, channel="emotional", thread_id=s.id,
                               role="assistant", content=reply, language=lang))
    s.memory_summary = _summarise(s)
    closing = None
    if s.message_count >= CLOSING_AFTER * 2 or any(w in message.lower() for w in GOODBYE_WORDS):
        closing = CLOSING[1 if hi else 0]
    db.commit()
    return _out(s, reply, lang, closing, analysis.self_harm, asha_requested)


def _out(s: EmotionalSession, reply: str, lang: str, closing: str | None, crisis: bool, asha_requested: bool) -> dict:
    support = []
    if crisis or s.concern_level in ("high", "elevated"):
        support = [{"type": "call", "label": "Tele-MANAS 14416", "number": "14416"},
                   {"type": "call", "label": "Emergency 108", "number": "108"}]
    elif s.concern_level == "mild":
        support = [{"type": "topic", "label": "Calming techniques", "query": "calming techniques"},
                   {"type": "call", "label": "Tele-MANAS 14416", "number": "14416"}]
    return {"session_id": s.id, "reply": reply, "language": lang, "closing_prompt": closing,
            "support_options": support, "asha_check_in_requested": asha_requested,
            "privacy": "Only you can see this conversation.",
            "suggestions": [{"text": "Nobody understands me", "text_hi": "मुझे कोई नहीं समझता"},
                            {"text": "I am worried", "text_hi": "मुझे चिंता हो रही है"}]}


TREND_WORDS = {"brighter": ("Your recent days have felt a little brighter.", "पिछले कुछ दिन थोड़े बेहतर लगे हैं।"),
               "steady": ("Your mood has been fairly steady.", "आपका मन ठीक-ठाक रहा है।"),
               "heavier": ("The last few days seem to have felt heavier. Be gentle with yourself.", "पिछले कुछ दिन भारी लगे हैं। अपना ख़याल रखें।")}


def history(db: Session, mother: Mother, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    rows = db.scalars(select(EmotionalSession).where(EmotionalSession.mother_id == mother.id,
                                                     EmotionalSession.created_at >= since)
                      .order_by(EmotionalSession.created_at.desc())).all()
    sessions = []
    for s in rows:
        first = db.scalar(select(ConversationMessage).where(ConversationMessage.thread_id == s.id,
                                                            ConversationMessage.role == "user")
                          .order_by(ConversationMessage.created_at))
        sessions.append({"session_id": s.id, "date": s.created_at, "mood": s.mood_checkin,
                         "messages": s.message_count, "preview": first.content[:80] if first else None})
    # Mood trend in words only (no scores): compare the latest week with the one before.
    recent = [s for s in rows if _aware(s.created_at) >= utcnow() - timedelta(days=7)]
    older = [s for s in rows if _aware(s.created_at) < utcnow() - timedelta(days=7)]
    def avg(xs):
        return sum(x.sentiment for x in xs) / len(xs) if xs else None
    a_recent, a_old = avg(recent), avg(older)
    if a_recent is None:
        trend = None
    elif a_old is None:
        trend = "heavier" if a_recent < -0.3 else "brighter" if a_recent > 0.3 else "steady"
    else:
        diff = a_recent - a_old
        trend = "brighter" if diff > 0.2 else "heavier" if diff < -0.2 else "steady"
    moods: dict[str, int] = {}
    for s in rows:
        if s.mood_checkin:
            moods[s.mood_checkin] = moods.get(s.mood_checkin, 0) + 1
    hi = mother.language == "hi"
    return {"sessions": sessions, "mood_checkins": moods,
            "trend": trend, "trend_message": TREND_WORDS[trend][1 if hi else 0] if trend else None,
            "privacy": "Only you can see this."}


def session_messages(db: Session, mother: Mother, session_id: uuid.UUID) -> list[dict]:
    s = db.get(EmotionalSession, session_id)
    if not s or s.mother_id != mother.id:
        return []
    rows = db.scalars(select(ConversationMessage).where(ConversationMessage.thread_id == s.id,
                                                        ConversationMessage.channel == "emotional")
                      .order_by(ConversationMessage.created_at)).all()
    return [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in rows]
