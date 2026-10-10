"""Safety layer applied to every AI conversation.

1. Danger-sign detection on the user's message (English, Hindi, Hinglish). Any hit puts an
   emergency instruction at the top of the reply, regardless of what the model said.
2. Self-harm detection → Tele-MANAS 14416 and 108, warm and direct.
3. Output scrubbing: no percentages / probabilities, no dosing instructions without a
   "confirm with your doctor" note, and a short "not a diagnosis" reminder.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# code: (label_en, label_hi, urgency, [patterns])
DANGER_SIGNS: dict[str, tuple[str, str, str, list[str]]] = {
    "heavy_bleeding": ("Bleeding", "खून आना", "immediate", [
        r"\bbleed(ing)?\b", r"\bblood (is )?(coming|flowing|discharge)", r"\bkhoon (aa|beh|nikal)", r"खून (आ|बह|निकल)",
        r"रक्तस्राव", r"\bspotting\b"]),
    "convulsions": ("Convulsions / fits", "दौरे पड़ना", "immediate", [
        r"\bfits?\b", r"\bseizure", r"\bconvuls", r"दौरा", r"दौरे", r"\bjhatke\b", r"झटके"]),
    "severe_headache": ("Severe headache", "तेज़ सिरदर्द", "immediate", [
        r"severe headache", r"(very|bahut|tez|terrible|worst) (bad )?(headache|sir ?dard)", r"तेज़? सिर ?दर्द", r"बहुत सिर ?दर्द"]),
    "blurred_vision": ("Blurred vision", "धुंधला दिखना", "immediate", [
        r"blur(red|ry)? vision", r"vision (is )?blur", r"can'?t see (clearly|properly)", r"dhundh?la", r"धुंधला",
        r"spots? (in|before) (my )?eyes"]),
    "reduced_fetal_movement": ("Baby moving less", "बच्चे की हलचल कम", "immediate", [
        r"baby (is )?(not|isn'?t|has stopped|stopped) mov", r"(less|reduced|no) (fetal |baby )?movement",
        r"baby (is )?moving less", r"bach?cha (hil|hal) ?(nahi|nahin|kam)", r"बच्चा (नहीं|कम) हिल", r"हलचल (कम|नहीं|बंद)"]),
    "breathing_difficulty": ("Breathing difficulty", "साँस लेने में तकलीफ़", "immediate", [
        r"(can'?t|cannot|difficulty|trouble|hard to) breath", r"breathless", r"short(ness)? of breath",
        r"saa?ns (nahi|lene mein|phool)", r"साँस (नहीं|लेने में|फूल)", r"सांस (नहीं|लेने में|फूल)", r"chest pain"]),
    "water_broke": ("Water broke / fluid leaking", "पानी की थैली फटना", "immediate", [
        r"water (has )?(broke|broken)", r"leaking (fluid|water)", r"fluid leak", r"पानी (आ|निकल|बह) (रहा|गया)"]),
    "unconscious": ("Fainting / unconscious", "बेहोशी", "immediate", [
        r"\bfaint(ed|ing)\b", r"unconscious", r"passed out", r"\bbehosh\b", r"बेहोश"]),
    "severe_abdominal_pain": ("Severe abdominal pain", "पेट में तेज़ दर्द", "immediate", [
        r"severe (abdominal|stomach|belly) pain", r"(stomach|abdomen|belly) pain (is )?(severe|unbearable|very bad)",
        r"pet (mein|me) (bahut |tez )?dard", r"पेट में (बहुत |तेज़ )?दर्द"]),
    "high_fever": ("High fever", "तेज़ बुखार", "same_day", [
        r"high fever", r"fever (with|and) chills", r"tez bukhar", r"तेज़ बुखार", r"बुखार"]),
    "face_hand_swelling": ("Swelling of face or hands", "चेहरे/हाथों पर सूजन", "same_day", [
        r"swelling (of|in|on) (my )?(face|hands)", r"(face|hands) (are |is )?swollen", r"चेहरे पर सूजन", r"हाथों में सूजन"]),
    "labour_signs": ("Possible labour", "प्रसव के संकेत", "immediate", [
        r"labou?r pain", r"contractions?", r"dard (shuru|aa rahe)", r"प्रसव पीड़ा"]),
}

SELF_HARM_PATTERNS = [
    r"\b(kill|hurt|harm) (myself|me)\b", r"suicid", r"end (my|this) life", r"don'?t want to live",
    r"want to die", r"no reason to live", r"better off dead", r"marna chahti", r"mar jaana", r"जीना नहीं",
    r"मरना चाहती", r"आत्महत्या", r"खुद को (नुकसान|खत्म)", r"zindagi khatam",
]

_PERCENT = re.compile(r"\(?\s*\d{1,3}(?:\.\d+)?\s?(?:%|percent\b|per cent\b|प्रतिशत)\s*\)?", re.I)
_PROB_WORDS = re.compile(r"\b(probability|likelihood|confidence (score|level)|risk score)\b", re.I)
_DOSE = re.compile(r"\b\d+(\.\d+)?\s?(mg|mcg|ml|iu|units?)\b", re.I)

EMERGENCY_NOTE_EN = (
    "This could be a danger sign. Please do not wait: call 108 for an ambulance (or 102 for pregnancy "
    "transport), tap SOS in the app, and inform your ASHA worker. Go to the nearest hospital now."
)
EMERGENCY_NOTE_HI = (
    "यह खतरे का संकेत हो सकता है। इंतज़ार न करें: 108 पर एम्बुलेंस बुलाएँ (या 102), ऐप में SOS दबाएँ और "
    "अपनी आशा दीदी को बताएँ। अभी नज़दीकी अस्पताल जाएँ।"
)
SAME_DAY_NOTE_EN = "Please get checked at your PHC today and let your ASHA worker know."
SAME_DAY_NOTE_HI = "आज ही PHC पर जाँच कराएँ और अपनी आशा दीदी को बताएँ।"
CRISIS_NOTE_EN = (
    "I'm really glad you told me. You matter, and you don't have to carry this alone. Please call Tele-MANAS "
    "on 14416 (free, 24×7, in your language) to talk to a counsellor right now. If you feel you might act on "
    "these thoughts, call 108 or ask someone near you to stay with you."
)
CRISIS_NOTE_HI = (
    "आपने बताया, इसके लिए शुक्रिया। आप बहुत मायने रखती हैं, और आपको यह अकेले नहीं सहना है। अभी Tele-MANAS "
    "14416 पर फ़ोन करें (मुफ़्त, 24 घंटे, आपकी भाषा में)। अगर खुद को नुकसान पहुँचाने का डर हो तो 108 पर कॉल करें "
    "या किसी अपने को पास बुला लें।"
)
DISCLAIMER_EN = "💛 This is friendly guidance, not a diagnosis. Your ASHA didi and doctor know you best."
DISCLAIMER_HI = "💛 यह प्यार भरी सलाह है, निदान नहीं। आपको आशा दीदी और डॉक्टर सबसे अच्छी तरह जानते हैं।"
DOSE_NOTE_EN = "Please take medicines only in the dose your doctor prescribed."
DOSE_NOTE_HI = "दवा उतनी ही लें जितनी डॉक्टर ने लिखी है।"


@dataclass
class SafetyCheck:
    danger_signs: list[dict] = field(default_factory=list)
    self_harm: bool = False

    @property
    def emergency(self) -> bool:
        return any(d["urgency"] == "immediate" for d in self.danger_signs)

    @property
    def flags(self) -> list[str]:
        out = [d["code"] for d in self.danger_signs]
        if self.self_harm:
            out.append("self_harm")
        return out


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def check_message(text: str) -> SafetyCheck:
    t = (text or "").lower()
    found = [
        {"code": code, "label": en, "label_hi": hi, "urgency": urgency}
        for code, (en, hi, urgency, pats) in DANGER_SIGNS.items() if _any(pats, t)
    ]
    # "spotting" or a light mention alone is not an emergency – downgrade to same-day.
    for d in found:
        if d["code"] == "heavy_bleeding" and re.search(r"\bspotting\b", t) and not re.search(r"bleed|khoon|खून", t):
            d["urgency"] = "same_day"
    return SafetyCheck(danger_signs=found, self_harm=_any(SELF_HARM_PATTERNS, t))


_SENTENCE = re.compile(r"(?<=[.!?।])\s+")


_BOILERPLATE = re.compile(r"\bas an ai\b|\bi am an ai\b|\blanguage model\b|\bi'?m not a (doctor|medical professional)\b|"
                         r"\bconsult (a|your) (qualified )?(healthcare|medical) (professional|provider)\b", re.I)


def scrub(text: str) -> str:
    """Drop sentences that state percentages, probabilities or confidence, or AI boilerplate; tidy whitespace."""
    kept_lines = []
    for line in text.splitlines():
        sentences = [x for x in _SENTENCE.split(line)
                     if not (_PERCENT.search(x) or _PROB_WORDS.search(x) or _BOILERPLATE.search(x))]
        if sentences or not line.strip():
            kept_lines.append(" ".join(sentences))
    out = "\n".join(kept_lines)
    out = _PERCENT.sub("", out)  # belt and braces
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", out)).strip()


def apply(reply: str, check: SafetyCheck, language: str = "en", *, disclaimer: bool = True) -> str:
    hi = language == "hi"
    parts: list[str] = []
    if check.self_harm:
        parts.append(CRISIS_NOTE_HI if hi else CRISIS_NOTE_EN)
    if check.emergency:
        parts.append(EMERGENCY_NOTE_HI if hi else EMERGENCY_NOTE_EN)
    elif check.danger_signs:
        parts.append(SAME_DAY_NOTE_HI if hi else SAME_DAY_NOTE_EN)
    body = scrub(reply)
    if body:
        parts.append(body)
    if _DOSE.search(body):
        parts.append(DOSE_NOTE_HI if hi else DOSE_NOTE_EN)
    if disclaimer:
        parts.append(DISCLAIMER_HI if hi else DISCLAIMER_EN)
    return "\n\n".join(parts)
