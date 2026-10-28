"""Ask the Pregnancy Guide: a back-and-forth Q&A about her week, limited to pregnancy and mother-and-baby care.

Layers, in order:
1. Safety: danger signs and self-harm are always answered first (108 / SOS / Tele-MANAS), whatever the topic.
2. Scope guard: only questions about pregnancy, birth, recovery after birth, newborn care, breastfeeding, a mother's
   food, body and feelings in this time, and the related government schemes are answered. Rules decide the clear
   cases; the language model classifies the rest (with the conversation, so "and after delivery?" stays in scope).
   Off-topic questions get a kind note and suggestions of what she can ask.
3. Answer: the knowledge base passages and her week's guide are given to the model; without a model, the best
   passage is answered directly.
"""
from __future__ import annotations

import re

from app.ai.language import detect_language
from app.ai.llm import ChatMessage, get_llm
from app.ai.prompts import COMMON_RULES, format_context
from app.ai.safety import apply, check_message
from app.core.pregnancy import trimester
from app.knowledge.service import get_kb, source_ref, week_guide

IN_SCOPE = re.compile(
    r"pregnan|garbh|गर्भ|baby|bach?ch?a|बच्च|shishu|शिशु|newborn|infant|foetus|fetus|womb|uterus|bachedani|बच्चेदानी|"
    r"deliver|prasav|प्रसव|labou?r|c-?section|caesar|operation se|normal delivery|birth|janam|जन्म|due date|edd|"
    r"trimester|tim[aā]hi|तिमाही|week \d|hafte|हफ़्त|हफ्त|mahin|महीन|\bmonths?\b|"
    r"breast ?feed|stan ?pan|स्तनपान|doodh pila|दूध पिला|feeding|colostrum|"
    r"\bkick|hal ?chal|हलचल|movement|ultrasound|sonograph|\bscan|anc\b|check ?up|jaanch|जाँच|tika|टीका|vaccin|tt injection|td\b|"
    r"\biron\b|ifa|folic|calcium|haemoglobin|hemoglobin|\bhb\b|anaemi|anemi|khoon ki kami|खून की कमी|"
    r"nause|vomit|ulti|उल्टी|morning sickness|heartburn|acidity|constipat|kabz|कब्ज़|swell|sujan|सूजन|cramp|back ?pain|kamar|कमर|"
    r"bleed|spotting|discharge|khoon|खून|headache|sir ?dard|सिरदर्द|blood pressure|\bbp\b|sugar|diabet|gestational|thyroid|"
    r"\bdiet\b|\beat(ing)?\b|\bfood|khana|खाना|kha sakt|खा सकत|nutrition|poshan|पोषण|fruit|papaya|pineapple|\btea\b|coffee|\bchai\b|"
    r"exercise|yoga|\bwalk|tahal|\bsleep|neend|नींद|\bsex\b|intercourse|travel|yatra|safar|"
    r"\bmood|\bsad\b|udaas|उदास|anxious|chinta|चिंता|stress|depress|postpartum|after delivery|prasav ke baad|प्रसव के बाद|"
    r"\bperiods?\b|mahwari|माहवारी|contracepti|family planning|copper.?t|mother|\bmaa\b|माँ|asha|anm|janani|jsy|pmmvy|jssk|pmsma|"
    r"matru|vandana|scheme|yojana|योजना|108|102|mcp card|hospital|doctor|medicine|dawa|दवा|\bwater\b|paani|\bpani\b|पानी|\bdrink|"
    r"\bpain|dard|दर्द|\bpet\b|पेट|weight|vajan|wazan|वज़न|वजन|tired|thakan|थकान|chakkar|चक्कर|dizz|fever|bukhar|बुखार", re.I)
OFF_TOPIC = re.compile(
    r"cricket|ipl|football|match score|movie|film|song|gana|actor|actress|bollywood|netflix|web series|"
    r"election|politic|minister|modi|rahul|party|stock|share market|sensex|bitcoin|crypto|loan|"
    r"\bcode\b|python|java(script)?|program(ming)?|html|homework|maths?|equation|essay|capital of|history of|"
    r"weather|mausam|recipe for (cake|pizza|biryani)|joke|game|pubg|free fire|horoscope|rashifal|lottery", re.I)
SMALL_TALK = re.compile(r"^(hi|hello|hey|namaste|namaskar|नमस्ते|thanks|thank you|dhanyavad|shukriya|धन्यवाद|ok|okay|theek hai|ठीक है)\W*$", re.I)

CLASSIFY_SYSTEM = """Decide if a question belongs in a pregnancy guide app for mothers in India.
YES: pregnancy, the unborn baby, birth and delivery, recovery after birth, newborn and infant care, breastfeeding,
a mother's food, body, sleep, work, travel, sex or feelings during pregnancy or after birth, tests and check-ups,
family planning after birth, and government mother-and-child schemes. Also YES for short follow-ups to such a conversation.
NO: anything else (sport, films, politics, money, studies, coding, general knowledge, other people's illnesses unrelated to pregnancy).
Reply with exactly one word: YES or NO."""

GUIDE_SYSTEM = """You are Janamdatri's Pregnancy Guide. You answer a mother's questions about pregnancy, birth, recovery
after birth, newborn care, breastfeeding and the related government schemes, and nothing else. If a question is about
something else, kindly say you can only help with pregnancy and baby care.
She is in week {week} ({trimester}). This week: {week_note}
Answer for her stage of pregnancy when it matters (for example food, exercise, tests and what to expect now).
""" + COMMON_RULES

REFUSAL = {
    "en": "I'm your pregnancy guide, so I can only help with questions about pregnancy, your baby, delivery, care after "
          "birth and the schemes for mothers. You could ask me, for example:",
    "hi": "मैं आपकी गर्भावस्था गाइड हूँ, इसलिए मैं सिर्फ़ गर्भावस्था, आपके बच्चे, प्रसव, जन्म के बाद की देखभाल और माँओं की "
          "योजनाओं के बारे में मदद कर सकती हूँ। आप ऐसे पूछ सकती हैं:",
}
GREETING = {
    "en": "Hello! Ask me anything about week {week}: your baby's growth, food, tests, aches and pains, or what comes next.",
    "hi": "नमस्ते! हफ़्ता {week} के बारे में कुछ भी पूछिए: बच्चे का बढ़ना, खाना, जाँच, दर्द-तकलीफ़, या आगे क्या होगा।",
}


def suggestions(week: int, hi: bool) -> list[str]:
    t = trimester(week)
    en = {1: ["What should I eat in the first months?", "How do I manage nausea?", "When is my first check-up?"],
          2: [f"What is my baby doing in week {week}?", "Which tests are due now?", "Is walking or yoga safe?"],
          3: ["How do I count my baby's kicks?", "What are the signs of labour?", "What should I pack for the hospital?"]}[t]
    hi_ = {1: ["शुरू के महीनों में क्या खाऊँ?", "जी मिचलाना कैसे कम करूँ?", "पहली जाँच कब है?"],
           2: [f"हफ़्ता {week} में बच्चा क्या कर रहा है?", "अभी कौन सी जाँच होनी है?", "क्या टहलना या योग ठीक है?"],
           3: ["बच्चे की हलचल कैसे गिनूँ?", "प्रसव के संकेत क्या हैं?", "अस्पताल के लिए क्या सामान रखूँ?"]}[t]
    out = hi_ if hi else en
    return out + (["मुझे कौन सी सरकारी योजनाएँ मिल सकती हैं?"] if hi else ["Which government schemes can I get?"])


def classify(question: str, history: list[dict], hits: list, llm) -> tuple[bool, str]:
    """(in scope?, how it was decided)."""
    q = question.strip()
    if IN_SCOPE.search(q):
        return True, "rules"
    if OFF_TOPIC.search(q):
        return False, "rules"
    if llm is not None and llm.available:
        convo = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in history[-4:])
        out = llm.generate([ChatMessage("system", CLASSIFY_SYSTEM),
                            ChatMessage("user", (f"Conversation so far:\n{convo}\n\n" if convo else "") + f"Question: {q}")],
                           temperature=0, max_tokens=5)
        if out and out.strip().upper().startswith(("YES", "NO")):
            return out.strip().upper().startswith("YES"), "ai"
    # No model: follow-ups to an in-scope conversation stay in; otherwise trust a strong knowledge-base match.
    if history and len(q.split()) <= 8:
        return True, "follow_up"
    return bool(hits and hits[0].score >= 0.3), "retrieval"


def ask(question: str, week: int, history: list[dict] | None = None, language: str | None = None,
        name: str | None = None, visits: int = 0) -> dict:
    history = [m for m in (history or []) if m.get("role") in ("user", "assistant") and m.get("content")][-6:]
    lang = language or detect_language(question)
    hi = lang == "hi"
    check = check_message(question)
    guide = week_guide(week, visits)
    kb = get_kb()
    hits = kb.search(question, k=4, week=week)
    llm = get_llm("knowledge")
    base = {"question": question, "week": guide["week"], "language": lang, "suggestions": suggestions(guide["week"], hi),
            "safety": {"emergency": check.emergency, "danger_signs": check.danger_signs, "self_harm": check.self_harm}}

    if SMALL_TALK.match(question.strip()) and not check.flags:
        return {**base, "in_scope": True, "scope_check": "rules", "generated_by": "rules", "sources": [],
                "answer": GREETING["hi" if hi else "en"].format(week=guide["week"])}

    in_scope, how = (True, "safety") if check.flags else classify(question, history, hits, llm)
    if not in_scope:
        tips = "\n".join(f"• {s}" for s in base["suggestions"][:3])
        return {**base, "in_scope": False, "scope_check": how, "generated_by": "scope_guard", "sources": [],
                "answer": f"{REFUSAL['hi' if hi else 'en']}\n{tips}"}

    week_note = f"{guide['milestone']} Baby is about the size of a {guide['baby']['size']}. " + " ".join(
        f["text"] for f in guide["focus"][:3])
    raw, generated_by = None, "knowledge_base"
    if llm.available:
        msgs = [ChatMessage("system", GUIDE_SYSTEM.format(
            week=guide["week"], trimester=guide["trimester_label"], week_note=week_note,
            language="Hindi" if hi else "English") + (f"\nHer name: {name}." if name else "")
            + "\n\nContext:\n" + format_context([h.doc for h in hits]))]
        msgs += [ChatMessage(m["role"], m["content"][:1500]) for m in history]
        msgs.append(ChatMessage("user", question))
        raw = llm.generate(msgs, max_tokens=500)
        if raw:
            generated_by = "ai+retrieval"
    if not raw:
        if hits:
            top = hits[0].doc
            raw = top["text_hi"] if hi and top.get("text_hi") else top["text"]
            if top.get("category") == "pregnancy_guide" or re.search(r"week|hafte|हफ़्त|baby|bach", question, re.I):
                raw = (guide["milestone_hi"] if hi else guide["milestone"]) + "\n\n" + raw
        else:
            raw = ("इस बारे में मेरे पास पक्की जानकारी नहीं है। अपनी आशा दीदी या डॉक्टर से पूछिए।" if hi else
                   "I don't have reliable information on that one. Please ask your ASHA didi or doctor.")
    return {**base, "in_scope": True, "scope_check": how, "generated_by": generated_by,
            "answer": apply(raw, check, lang), "sources": [source_ref(h.doc) for h in hits]}
