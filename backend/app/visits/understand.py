"""Understanding an ASHA's spoken or typed visit notes (English, Hindi, Hinglish) as visit-form fields.

"BP 130 by 85, Hb 9.5, weight 58, chakkar aur pair mein sujan, bleeding nahi, IFA 30 di, agli visit 3 din baad"
→ bp 130/85, hemoglobin 9.5, weight 58, symptoms dizziness + swelling_feet (bleeding denied), IFA 30 given,
  next visit in 3 days.

Rules first (always, explainable); the language model (when a key is set) reads what the rules missed, and its
answer is validated field by field. The ASHA sees everything understood and can correct it before saving.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

from app.assessment.symptoms import SYMPTOMS

log = logging.getLogger(__name__)

NUM = r"(\d{2,3}(?:\.\d)?)"
BP_RX = re.compile(r"(?:\bbp\b|बीपी|blood pressure|ब्लड प्रेशर)?\s*[:=-]?\s*(\d{2,3})\s*(?:/|by|over|बटा|बाय|upon|par)\s*(\d{2,3})", re.I)
WEIGHT_RX = re.compile(r"(?:weight|wazan|vajan|वज़न|वजन)\s*(?:is|hai|है|[:=-])?\s*(\d{2,3}(?:\.\d)?)|(\d{2,3}(?:\.\d)?)\s*(?:kg|kilo|किलो)", re.I)
HB_RX = re.compile(r"(?:\bhb\b|h\.b\.|ha?emoglobin|हीमोग्लोबिन|एचबी|khoon|खून)\s*(?:is|hai|है|level|[:=-])?\s*(\d{1,2}(?:\.\d)?)", re.I)
NEG = re.compile(r"\b(no|not|nahi|nahin|na|without|denies)\b|नहीं|नही|ना\b", re.I)

# symptom code → patterns (English, Hindi, Hinglish)
SYMPTOM_RX = {
    "dizziness": r"dizz|chakkar|चक्कर",
    "swelling_feet": (r"swell\w* (?:in |of )?(?:the )?(?:feet|legs|ankles)|(?:feet|leg|pair|pairon)\w* (?:me |mein "
                      r"|में )?(?:swell|sujan|soojan|सूजन)|पैरों? (?:में )?सूजन"),
    "swelling_face_hands": r"(?:face|hands?|chehre|haath)\w* (?:me |mein |par )?(?:swell|sujan|soojan)|चेहरे (?:पर )?सूजन|हाथों? (?:में )?सूजन",
    "severe_headache": r"severe headache|tez sir ?dard|bahut sir ?dard|तेज़? सिर ?दर्द",
    "headache": r"headache|sir ?dard|सिर ?दर्द",
    "blurred_vision": r"blur|dhundh?la|धुंधला",
    "tiredness": r"tired|thakan|thakaan|थकान|weakness|kamzori|कमज़ोरी|कमजोरी",
    "nausea_vomiting": r"vomit|nause|ulti|उल्टी|ji machal",
    "abdominal_pain": r"(?:abdominal|stomach|belly) pain|pet (?:me |mein )?dard|पेट (?:में )?दर्द",
    "bleeding": r"bleed|khoon aa|खून आ|blood (?:loss|discharge)",
    "spotting": r"spotting|halke dhabbe|धब्बे",
    "reduced_fetal_movement": r"(?:baby|bachcha|bacha|बच्चा)\w* (?:kam|less|कम) (?:hil|mov|हिल)|(?:less|reduced) (?:fetal )?movement|हलचल कम",
    "fever": r"fever|bukhar|बुखार",
    "breathlessness": r"breathless|short(?:ness)? of breath|saa?ns (?:phool|lene)|साँस|सांस",
    "chest_pain": r"chest pain|seene (?:me |mein )?dard|सीने (?:में )?दर्द",
    "convulsions": r"convuls|\bfits?\b|daure|दौरे",
    "fainting": r"faint|behosh|बेहोश",
    "leaking_fluid": r"leak|paani aa|पानी आ",
    "burning_urination": r"burning (?:urine|urination|while)|peshab (?:me |mein )?jalan|पेशाब में जलन",
    "back_pain": r"back ?pain|kamar (?:me |mein )?dard|कमर (?:में )?दर्द",
    "low_mood": r"\bsad\b|udaas|उदास|low mood|depress",
    "anxiety": r"anxious|worried|chinta|चिंता|tension",
    "pale_skin": r"\bpale\b|peela|पीला|पीली",
}
CHECKS = {
    "diet_counselling": r"diet|nutrition|khana|khane|खाने|पोषण|poshan",
    "danger_signs_explained": r"danger sign|khatre|खतरे",
    "birth_preparedness_plan": r"birth (?:plan|prepar)|delivery plan|prasav (?:yojana|ki taiyari)|प्रसव (?:योजना|की तैयारी)|hospital bag",
}
IFA_RX = re.compile(r"(?:ifa|iron|आयरन|folic)\w*\s*(?:tablets?|goli(?:yan|yaan)?|गोलियाँ|गोली)?\s*(?:given|di|dee|diye|दी"
                    r"|दिए)?\s*[:=-]?\s*(\d{1,3})|(\d{1,3})\s*(?:ifa|iron|आयरन)\w*\s*(?:tablets?|goli\w*|गोली\w*)?", re.I)
TAKING_IFA_NO = re.compile(r"(?:not|nahi|nahin|नहीं) (?:taking|le rahi|leti|ले रही|लेती)\s*(?:the )?(?:ifa|iron|goli|आयरन|गोली)?|(?:ifa|iron"
                           r"|goli|आयरन|गोली)\w* (?:nahi|nahin|नहीं) (?:le|ले)|stopped (?:ifa|iron)", re.I)
TAKING_IFA_YES = re.compile(r"(?:taking|le rahi|leti hai|ले रही|लेती है) (?:the )?(?:ifa|iron|goli|आयरन|गोली)|(?:ifa|iron|goli"
                            r"|आयरन|गोली)\w* (?:le rahi|regular|roz|रोज़|ले रही)", re.I)
BABY_OK = re.compile(r"(?:baby|bachcha|bacha|बच्चा)\w* (?:moving (?:well|normally|fine)|achhe se hil"
                     r"|theek se hil|हिल रहा)|movement (?:normal|good)", re.I)
ANC_DONE = re.compile(r"\banc\b\s*(?:done|completed|kiya|ki|हुई|किया)|anc check-?up (?:done|completed)|जाँच (?:हुई|कराई)", re.I)
POSTNATAL = re.compile(r"postnatal|\bpnc\b|after delivery|delivery ke baad|prasav ke baad|प्रसव के बाद|newborn|navjat|नवजात", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
NEXT_RX = re.compile(r"(?:next visit|agli visit|agla visit|फिर आऊँगी|अगली (?:विज़िट|भेंट)|come again|follow ?up)[^.,;]*", re.I)


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"[,.;।\n]|\band\b|\baur\b|और|\bbut\b|lekin|लेकिन", text) if c.strip()]


def _next_visit(text: str, today: date) -> date | None:
    m = NEXT_RX.search(text)
    if not m:
        return None
    s = m.group(0).lower()
    if re.search(r"\bkal\b|tomorrow|कल", s):
        return today + timedelta(days=1)
    if n := re.search(r"(\d{1,2})\s*(?:din|days?|दिन)", s):
        return today + timedelta(days=int(n.group(1)))
    if n := re.search(r"(\d{1,2})\s*(?:hafte|weeks?|हफ़्ते|हफ्ते)", s):
        return today + timedelta(weeks=int(n.group(1)))
    if re.search(r"next week|agle hafte|अगले हफ़्ते", s):
        return today + timedelta(days=7)
    if n := re.search(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*", s):
        day, mon = int(n.group(1)), MONTHS[n.group(2)]
        year = today.year + (1 if mon < today.month else 0)
        try:
            return date(year, mon, day)
        except ValueError:
            return None
    return None


def rules(text: str, today: date | None = None) -> dict:
    today = today or date.today()
    t = text or ""
    out: dict = {}
    if m := BP_RX.search(t):
        s, d = int(m.group(1)), int(m.group(2))
        if 60 <= s <= 260 and 30 <= d <= 180 and s > d:
            out["bp"] = f"{s}/{d}"
    if m := HB_RX.search(t):
        v = float(m.group(1))
        if 2 <= v <= 20:
            out["hemoglobin"] = v
    for m in WEIGHT_RX.finditer(t):
        v = float(m.group(1) or m.group(2))
        if 25 <= v <= 200:
            out["weight_kg"] = v
            break
    found, denied = [], []
    for clause in _clauses(t):
        cl = clause.lower()
        for code, rx in SYMPTOM_RX.items():
            if re.search(rx, cl):
                if code == "headache" and "severe_headache" in found + denied:
                    continue
                (denied if NEG.search(cl) else found).append(code)
    out["symptoms"] = [c for c in dict.fromkeys(found) if c in SYMPTOMS]
    out["denied"] = [c for c in dict.fromkeys(denied) if c in SYMPTOMS and c not in out["symptoms"]]
    checklist: dict = {}
    if m := IFA_RX.search(t):
        n = int(m.group(1) or m.group(2))
        if 1 <= n <= 180:
            checklist["ifa_tablets_given"] = n
    for key, rx in CHECKS.items():
        hit = next((c for c in _clauses(t) if re.search(rx, c.lower())), None)
        if hit and not NEG.search(hit.lower()):
            checklist[key] = True
    out["checklist"] = checklist
    if TAKING_IFA_NO.search(t):
        out["taking_ifa"] = False
    elif TAKING_IFA_YES.search(t):
        out["taking_ifa"] = True
    if "reduced_fetal_movement" in out["symptoms"]:
        out["baby_moving_normally"] = False
    elif BABY_OK.search(t):
        out["baby_moving_normally"] = True
    if ANC_DONE.search(t):
        out["anc_completed"] = True
    if POSTNATAL.search(t):
        out["visit_type"] = "postnatal"
    if nv := _next_visit(t, today):
        out["next_visit_date"] = nv.isoformat()
    return {k: v for k, v in out.items() if v not in ([], {}, None)}


LLM_SYSTEM = """You read an ASHA worker's home-visit notes about a pregnant or postnatal woman in India (English, Hindi or Hinglish)
and return ONLY a JSON object with what the notes say (omit anything not said):
{"bp": "120/80", "hemoglobin": 10.5, "weight_kg": 55,
 "symptoms": [codes from: %s],
 "denied": [symptom codes she said are NOT present],
 "checklist": {"ifa_tablets_given": 30, "diet_counselling": true, "danger_signs_explained": true, "birth_preparedness_plan": true},
 "taking_ifa": true, "baby_moving_normally": true, "anc_completed": true, "visit_type": "home|anc|postnatal|followup",
 "next_visit_in_days": 3}
No text outside the JSON."""


def _valid(data: dict, today: date) -> dict:
    out: dict = {}
    if isinstance(data.get("bp"), str) and (m := re.match(r"^(\d{2,3})/(\d{2,3})$", data["bp"].strip())):
        s, d = int(m.group(1)), int(m.group(2))
        if 60 <= s <= 260 and 30 <= d <= 180 and s > d:
            out["bp"] = f"{s}/{d}"
    for k, lo, hi in (("hemoglobin", 2, 20), ("weight_kg", 25, 200)):
        v = data.get(k)
        if isinstance(v, int | float) and lo <= v <= hi:
            out[k] = float(v)
    for k in ("symptoms", "denied"):
        codes = [c for c in data.get(k) or [] if isinstance(c, str) and c in SYMPTOMS]
        if codes:
            out[k] = codes
    cl = data.get("checklist") or {}
    checklist = {}
    if isinstance(cl.get("ifa_tablets_given"), int) and 1 <= cl["ifa_tablets_given"] <= 180:
        checklist["ifa_tablets_given"] = cl["ifa_tablets_given"]
    for k in CHECKS:
        if cl.get(k) is True:
            checklist[k] = True
    if checklist:
        out["checklist"] = checklist
    for k in ("taking_ifa", "baby_moving_normally"):
        if isinstance(data.get(k), bool):
            out[k] = data[k]
    if data.get("anc_completed") is True:
        out["anc_completed"] = True
    if data.get("visit_type") in ("home", "anc", "postnatal", "followup"):
        out["visit_type"] = data["visit_type"]
    days = data.get("next_visit_in_days")
    if isinstance(days, int) and 1 <= days <= 90:
        out["next_visit_date"] = (today + timedelta(days=days)).isoformat()
    return out


def with_model(text: str, llm, today: date) -> dict:
    if not llm or not llm.available:
        return {}
    from app.ai.llm import ChatMessage

    raw = llm.generate([ChatMessage("system", LLM_SYSTEM % ", ".join(SYMPTOMS)), ChatMessage("user", text)],
                       temperature=0, max_tokens=400)
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        return _valid(json.loads(m.group(0)), today) if m else {}
    except (ValueError, TypeError):
        return {}


def understand(text: str, llm=None, today: date | None = None) -> dict:
    """Rules first; the model adds fields the rules did not find (it never overrides a number the rules read)."""
    today = today or date.today()
    found = rules(text, today)
    model = with_model(text, llm, today) if len(text.split()) > 4 else {}
    for k, v in model.items():
        if k in ("symptoms", "denied"):
            found[k] = list(dict.fromkeys(found.get(k, []) + v))
        elif k == "checklist":
            found["checklist"] = {**v, **found.get("checklist", {})}
        elif k not in found:
            found[k] = v
    if found.get("denied"):
        found["symptoms"] = [s for s in found.get("symptoms", []) if s not in found["denied"]] or found.get("symptoms", [])
        found["symptoms"] = [s for s in found["symptoms"] if s not in found["denied"]]
    found["_model"] = bool(model)
    return found


def summary(found: dict, hi: bool = False) -> str:
    from app.assessment.symptoms import label

    parts = []
    if found.get("bp"):
        parts.append(f"BP {found['bp']}")
    if found.get("hemoglobin"):
        parts.append(f"Hb {found['hemoglobin']:g}")
    if found.get("weight_kg"):
        parts.append(f"{'वज़न' if hi else 'Weight'} {found['weight_kg']:g} kg")
    lang = "hi" if hi else "en"
    if found.get("symptoms"):
        parts.append(", ".join(label(s, lang) for s in found["symptoms"]))
    if found.get("denied"):
        parts.append(("नहीं: " if hi else "No ") + ", ".join(label(s, lang).lower() for s in found["denied"]))
    c = found.get("checklist") or {}
    if c.get("ifa_tablets_given"):
        parts.append(f"IFA {c['ifa_tablets_given']}")
    for k, en, hin in (("diet_counselling", "diet counselling", "खान-पान सलाह"),
                       ("danger_signs_explained", "danger signs explained", "खतरे के संकेत बताए"),
                       ("birth_preparedness_plan", "birth plan", "प्रसव योजना")):
        if c.get(k):
            parts.append(hin if hi else en)
    if found.get("taking_ifa") is False:
        parts.append("आयरन नहीं ले रही" if hi else "not taking IFA")
    if found.get("anc_completed"):
        parts.append("ANC " + ("हुई" if hi else "done"))
    if found.get("next_visit_date"):
        parts.append(("अगली भेंट " if hi else "next visit ") + f"{date.fromisoformat(found['next_visit_date']):%d %b}")
    return " · ".join(parts)
