"""Symptom catalogue used by the assessment, visits and danger ladder."""
from __future__ import annotations

# code: (English, Hindi)
SYMPTOMS: dict[str, tuple[str, str]] = {
    "dizziness": ("Dizziness", "चक्कर आना"),
    "swelling_feet": ("Swelling in feet", "पैरों में सूजन"),
    "swelling_face_hands": ("Swelling of face or hands", "चेहरे या हाथों पर सूजन"),
    "headache": ("Headache", "सिरदर्द"),
    "severe_headache": ("Severe headache", "तेज़ सिरदर्द"),
    "blurred_vision": ("Blurred vision", "धुंधला दिखना"),
    "tiredness": ("Tiredness", "थकान"),
    "nausea_vomiting": ("Nausea or vomiting", "उल्टी या मतली"),
    "severe_vomiting": ("Vomiting, can't keep food down", "बहुत उल्टी, कुछ नहीं टिकता"),
    "abdominal_pain": ("Belly pain", "पेट दर्द"),
    "severe_abdominal_pain": ("Severe belly pain", "पेट में तेज़ दर्द"),
    "bleeding": ("Bleeding from vagina", "योनि से खून आना"),
    "spotting": ("Light spotting", "हल्के धब्बे"),
    "reduced_fetal_movement": ("Baby moving less", "बच्चे की हलचल कम"),
    "no_fetal_movement": ("Baby not moving", "बच्चा नहीं हिल रहा"),
    "fever": ("Fever", "बुखार"),
    "breathlessness": ("Difficulty breathing", "साँस लेने में तकलीफ़"),
    "chest_pain": ("Chest pain", "सीने में दर्द"),
    "convulsions": ("Fits / convulsions", "दौरे पड़ना"),
    "fainting": ("Fainting", "बेहोशी"),
    "leaking_fluid": ("Water leaking", "पानी आना"),
    "burning_urination": ("Burning while passing urine", "पेशाब में जलन"),
    "foul_discharge": ("Foul-smelling discharge", "बदबूदार स्राव"),
    "palpitations": ("Racing heartbeat", "दिल की धड़कन तेज़"),
    "back_pain": ("Back pain", "कमर दर्द"),
    "low_mood": ("Feeling low", "मन उदास रहना"),
    "anxiety": ("Feeling very worried", "बहुत चिंता"),
    "pale_skin": ("Pale skin or nails", "त्वचा या नाखून पीले"),
}


def label(code: str, lang: str = "en") -> str:
    en, hi = SYMPTOMS.get(code, (code.replace("_", " ").capitalize(), code))
    return hi if lang == "hi" else en


def join_labels(codes: list[str], lang: str = "en") -> str:
    names = [label(c, lang) for c in codes]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    joiner = " और " if lang == "hi" else " and "
    first = ", ".join(names[:-1])
    return first + joiner + (names[-1] if lang == "hi" else names[-1].lower())
