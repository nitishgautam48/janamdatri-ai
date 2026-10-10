"""Maternal assessment engine.

Components (each emits *signals*; the worst signal wins):

1. Danger ladder     – symptoms that need care now (bleeding, fits, no movement, …)
2. Clinical rules    – BP, blood sugar, temperature, pulse, urine protein, fetal heart rate
3. Anaemia rules     – WHO pregnancy haemoglobin bands
4. EPDS              – Edinburgh Postnatal Depression Scale (incl. item 10 self-harm)
5. History engine    – pre-existing conditions, age, past complications, trends, missed ANC
6. Pattern model     – gradient boosting; may raise the level by one step, never above
                       Medical Review (see ``app.ai.risk_model``)

Outputs one of four care levels with plain-language findings. It never outputs a
probability, percentage or confidence value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.assessment.symptoms import join_labels
from app.core.enums import CareLevel

R, NA, MR, IC = (CareLevel.ROUTINE_MONITORING, CareLevel.NEEDS_ATTENTION, CareLevel.MEDICAL_REVIEW,
                 CareLevel.IMMEDIATE_CARE)


@dataclass
class Signal:
    source: str
    code: str
    level: CareLevel
    reason: str
    reason_hi: str = ""

    def as_dict(self) -> dict:
        return {"source": self.source, "code": self.code, "level": self.level.key, "reason": self.reason,
                "reason_hi": self.reason_hi}


@dataclass
class EngineInput:
    week: int | None = None
    age: int | None = None
    bp_systolic: int | None = None
    bp_diastolic: int | None = None
    weight_kg: float | None = None
    hemoglobin: float | None = None
    hemoglobin_source: str | None = None
    sugar_fasting: float | None = None
    sugar_random: float | None = None
    sugar_ogtt_2h: float | None = None
    temperature_c: float | None = None
    pulse: int | None = None
    urine_protein: str | None = None  # nil | trace | 1+ | 2+ | 3+
    fetal_heart_rate: int | None = None
    symptoms: list[str] = field(default_factory=list)
    taking_ifa: bool | None = None
    bleeding_this_week: bool | None = None
    baby_moving_normally: bool | None = None
    epds: list[int] | None = None
    conditions: list[str] = field(default_factory=list)
    past_complications: bool | None = None
    previous_c_section: bool = False
    multiple_pregnancy: bool = False
    first_pregnancy: bool | None = None
    hb_history: list[tuple[date, float]] = field(default_factory=list)  # oldest → newest
    previous_weight: tuple[date, float] | None = None
    next_anc_date: date | None = None
    anc_visits_completed: int = 0
    today: date = field(default_factory=date.today)


@dataclass
class EngineResult:
    level: CareLevel
    signals: list[Signal]
    noticed: list[dict]
    concerns: list[dict]
    recommendations: list[dict]
    follow_up: str
    follow_up_hi: str
    referral_suggestion: str | None
    model_level: CareLevel | None = None


# ---------------------------------------------------------------------------
def danger_ladder(x: EngineInput) -> list[Signal]:
    s, sym, wk = [], set(x.symptoms), x.week or 0
    if "bleeding" in sym or x.bleeding_this_week:
        s.append(Signal("danger_ladder", "bleeding", IC, "Bleeding in pregnancy", "गर्भावस्था में खून आना"))
    if "convulsions" in sym:
        s.append(Signal("danger_ladder", "convulsions", IC, "Fits / convulsions", "दौरे पड़ना"))
    if "fainting" in sym:
        s.append(Signal("danger_ladder", "fainting", IC, "Fainting", "बेहोशी"))
    if "no_fetal_movement" in sym or ("reduced_fetal_movement" in sym or x.baby_moving_normally is False):
        if wk >= 28 or "no_fetal_movement" in sym:
            s.append(Signal("danger_ladder", "fetal_movement", IC, "Baby moving less than usual", "बच्चे की हलचल कम"))
        elif wk >= 20:
            s.append(Signal("danger_ladder", "fetal_movement", MR, "Baby moving less than usual", "बच्चे की हलचल कम"))
    if "breathlessness" in sym or "chest_pain" in sym:
        s.append(Signal("danger_ladder", "breathing", IC, "Difficulty breathing or chest pain",
                        "साँस में तकलीफ़ या सीने में दर्द"))
    if "severe_abdominal_pain" in sym:
        s.append(Signal("danger_ladder", "abdominal_pain", IC, "Severe belly pain", "पेट में तेज़ दर्द"))
    if "leaking_fluid" in sym:
        s.append(Signal("danger_ladder", "leaking_fluid", IC, "Water leaking", "पानी आना"))
    if "severe_headache" in sym and "blurred_vision" in sym:
        s.append(Signal("danger_ladder", "headache_vision", IC, "Severe headache with blurred vision",
                        "तेज़ सिरदर्द के साथ धुंधला दिखना"))
    else:
        for code in ("severe_headache", "blurred_vision"):
            if code in sym:
                s.append(Signal("danger_ladder", code, MR, join_labels([code]), join_labels([code], "hi")))
    for code in ("swelling_face_hands", "severe_vomiting", "fever", "burning_urination", "foul_discharge",
                 "palpitations", "spotting"):
        if code in sym:
            s.append(Signal("danger_ladder", code, MR, join_labels([code]), join_labels([code], "hi")))
    minor = [c for c in ("dizziness", "swelling_feet", "headache", "tiredness", "nausea_vomiting", "abdominal_pain",
                         "back_pain", "pale_skin") if c in sym]
    if minor:
        s.append(Signal("danger_ladder", "minor_symptoms", NA, join_labels(minor), join_labels(minor, "hi")))
    return s


def clinical_rules(x: EngineInput) -> list[Signal]:
    s, sym = [], set(x.symptoms)
    sbp, dbp = x.bp_systolic, x.bp_diastolic
    if sbp is not None and dbp is not None:
        bp = f"BP {sbp}/{dbp}"
        pe_features = sym & {"severe_headache", "headache", "blurred_vision", "swelling_face_hands"}
        if sbp >= 160 or dbp >= 110:
            s.append(Signal("clinical_rules", "bp_severe", IC, f"{bp}, very high", f"{bp}, बहुत ज़्यादा"))
        elif sbp >= 140 or dbp >= 90:
            lvl = IC if pe_features & {"severe_headache", "blurred_vision"} else MR
            s.append(Signal("clinical_rules", "bp_high", lvl, f"{bp}, high", f"{bp}, ज़्यादा"))
        elif sbp >= 130 or dbp >= 85:
            s.append(Signal("clinical_rules", "bp_borderline", NA, f"{bp}, slightly high", f"{bp}, थोड़ा ज़्यादा"))
        elif sbp < 90 or dbp < 60:
            lvl = MR if sym & {"dizziness", "fainting"} else NA
            s.append(Signal("clinical_rules", "bp_low", lvl, f"{bp}, low", f"{bp}, कम"))
    fasting, rnd, ogtt = x.sugar_fasting, x.sugar_random, x.sugar_ogtt_2h
    if (fasting is not None and fasting >= 126) or (rnd is not None and rnd >= 200) or (ogtt is not None and ogtt >= 200):
        s.append(Signal("clinical_rules", "sugar_very_high", MR, "Blood sugar very high", "शुगर बहुत ज़्यादा"))
    elif (ogtt is not None and ogtt >= 140) or (fasting is not None and fasting >= 92):
        s.append(Signal("clinical_rules", "sugar_high", MR, "Blood sugar above the pregnancy limit",
                        "शुगर गर्भावस्था की सीमा से ज़्यादा"))
    elif rnd is not None and rnd >= 140:
        s.append(Signal("clinical_rules", "sugar_borderline", NA, "Blood sugar slightly high, needs a glucose test",
                        "शुगर थोड़ी ज़्यादा, ग्लूकोज़ जाँच ज़रूरी"))
    low = min(v for v in (fasting, rnd, ogtt) if v is not None) if any(v is not None for v in (fasting, rnd, ogtt)) else None
    if low is not None and low < 60:
        s.append(Signal("clinical_rules", "sugar_low", NA, "Blood sugar low", "शुगर कम"))
    if x.temperature_c is not None:
        if x.temperature_c >= 40 or (x.temperature_c >= 38 and "breathlessness" in sym):
            s.append(Signal("clinical_rules", "fever_high", IC, f"Temperature {x.temperature_c}°C", f"बुखार {x.temperature_c}°C"))
        elif x.temperature_c >= 38:
            s.append(Signal("clinical_rules", "fever", MR, f"Temperature {x.temperature_c}°C", f"बुखार {x.temperature_c}°C"))
    if x.pulse is not None:
        if x.pulse > 120:
            s.append(Signal("clinical_rules", "pulse_high", MR, f"Pulse {x.pulse}/min, fast", f"नब्ज़ {x.pulse}, तेज़"))
        elif x.pulse > 100:
            s.append(Signal("clinical_rules", "pulse_raised", NA, f"Pulse {x.pulse}/min, slightly fast", f"नब्ज़ {x.pulse}"))
    up = (x.urine_protein or "").lower().replace(" ", "")
    if up in ("2+", "3+", "4+", "++", "+++"):
        hyper = sbp is not None and (sbp >= 140 or (dbp or 0) >= 90)
        s.append(Signal("clinical_rules", "proteinuria", MR,
                        "Protein in urine" + (" with high BP" if hyper else ""), "पेशाब में प्रोटीन"))
    elif up in ("1+", "+", "trace"):
        s.append(Signal("clinical_rules", "proteinuria_trace", NA, "Traces of protein in urine", "पेशाब में थोड़ा प्रोटीन"))
    if x.fetal_heart_rate is not None and not (110 <= x.fetal_heart_rate <= 160):
        s.append(Signal("clinical_rules", "fhr", MR, f"Baby's heart rate {x.fetal_heart_rate}/min, outside normal",
                        f"बच्चे की धड़कन {x.fetal_heart_rate}, सामान्य से अलग"))
    if x.weight_kg is not None and x.weight_kg < 40:
        s.append(Signal("clinical_rules", "low_weight", NA, f"Weight {x.weight_kg} kg, low", f"वज़न {x.weight_kg} किलो, कम"))
    if x.weight_kg is not None and x.previous_weight:
        prev_date, prev_w = x.previous_weight
        days = max((x.today - prev_date).days, 1)
        if days <= 14 and x.weight_kg - prev_w >= 3:
            s.append(Signal("clinical_rules", "rapid_weight_gain", MR, "Sudden weight gain (possible fluid retention)",
                            "अचानक वज़न बढ़ना"))
    return s


def anemia_rules(x: EngineInput) -> list[Signal]:
    hb = x.hemoglobin
    if hb is None:
        return []
    txt = f"Hemoglobin {hb:g} g/dL"
    if hb < 7:
        return [Signal("anemia_rules", "anemia_severe", IC, f"{txt}, very low (severe anaemia)", f"खून (Hb) {hb:g}, बहुत कम")]
    if hb < 10:
        return [Signal("anemia_rules", "anemia_moderate", MR, f"{txt}, below normal", f"खून (Hb) {hb:g}, सामान्य से कम")]
    if hb < 11:
        return [Signal("anemia_rules", "anemia_mild", NA, f"Hb {hb:g}, slightly low", f"खून (Hb) {hb:g}, थोड़ा कम")]
    return []


def epds_rules(x: EngineInput) -> list[Signal]:
    if not x.epds:
        return []
    total = sum(x.epds)
    s = []
    if len(x.epds) >= 10 and x.epds[9] > 0:
        s.append(Signal("epds", "self_harm", IC, "Thoughts of self-harm shared", "खुद को नुकसान पहुँचाने के विचार"))
    if total >= 13:
        s.append(Signal("epds", "epds_high", MR, "Carrying a lot emotionally lately", "मन पर बहुत बोझ"))
    elif total >= 10:
        s.append(Signal("epds", "epds_moderate", NA, "Some emotional strain lately", "मन पर कुछ बोझ"))
    return s


HIGH_RISK_CONDITIONS = {"heart_disease", "kidney_disease", "epilepsy", "sickle_cell"}
WATCH_CONDITIONS = {"hypertension": "Known high BP", "diabetes": "Known diabetes", "thyroid": "Thyroid condition",
                    "hiv": "HIV – needs PPTCT care", "hepatitis_b": "Hepatitis B – baby needs vaccine at birth",
                    "tuberculosis": "TB history", "asthma": "Asthma"}


def history_engine(x: EngineInput) -> list[Signal]:
    s = []
    for c in x.conditions:
        if c in HIGH_RISK_CONDITIONS:
            s.append(Signal("history", f"hx_{c}", MR, f"{c.replace('_', ' ').capitalize()} needs a doctor's follow-up",
                            "डॉक्टर की नियमित जाँच ज़रूरी"))
        elif c in WATCH_CONDITIONS:
            s.append(Signal("history", f"hx_{c}", NA, WATCH_CONDITIONS[c], "पहले की बीमारी, नियमित जाँच ज़रूरी"))
    if x.past_complications:
        s.append(Signal("history", "hx_complications", NA, "Problems in a past pregnancy", "पिछली गर्भावस्था में परेशानी"))
    if x.previous_c_section and (x.week or 0) >= 28:
        s.append(Signal("history", "hx_csection", NA, "Previous caesarean: plan delivery at a CHC/district hospital",
                        "पहले ऑपरेशन से डिलीवरी: बड़े अस्पताल में प्रसव की योजना"))
    if x.multiple_pregnancy:
        s.append(Signal("history", "twins", MR, "Twins or more need closer doctor follow-up", "जुड़वाँ बच्चे"))
    if x.age is not None and (x.age < 18 or x.age > 35):
        s.append(Signal("history", "age", NA, "Age needs a little extra care in pregnancy", "उम्र के कारण थोड़ी ज़्यादा देखभाल"))
    if x.taking_ifa is False and (x.week or 0) >= 14:
        s.append(Signal("history", "no_ifa", NA, "Not taking iron tablets daily", "रोज़ आयरन की गोली नहीं ले रहीं"))
    if len(x.hb_history) >= 2:
        first, last = x.hb_history[0][1], x.hb_history[-1][1]
        if first - last >= 1.0 and last < 11:
            s.append(Signal("history", "hb_falling", NA, f"Hemoglobin falling ({first:g} → {last:g})",
                            f"खून (Hb) घट रहा है ({first:g} → {last:g})"))
    if x.next_anc_date and (x.today - x.next_anc_date).days > 14:
        s.append(Signal("history", "anc_overdue", NA, "ANC check-up overdue", "ANC जाँच बाकी है"))
    elif x.anc_visits_completed == 0 and (x.week or 0) > 14:
        s.append(Signal("history", "anc_missing", NA, "No ANC check-up recorded yet", "अभी तक कोई ANC जाँच दर्ज नहीं"))
    return s


def model_features(x: EngineInput) -> dict:
    sym = set(x.symptoms)
    return {
        "age": x.age, "week": x.week, "sbp": x.bp_systolic, "dbp": x.bp_diastolic, "hb": x.hemoglobin,
        "sugar_fasting": x.sugar_fasting, "sugar_random": x.sugar_random, "epds": sum(x.epds) if x.epds else None,
        "headache": "headache" in sym or "severe_headache" in sym, "blurred_vision": "blurred_vision" in sym,
        "swelling": bool(sym & {"swelling_feet", "swelling_face_hands"}),
        "bleeding": "bleeding" in sym or bool(x.bleeding_this_week),
        "reduced_movement": bool(sym & {"reduced_fetal_movement", "no_fetal_movement"}) or x.baby_moving_normally is False,
        "dizziness": "dizziness" in sym, "fever": "fever" in sym, "breathlessness": "breathlessness" in sym,
        "convulsions": "convulsions" in sym, "abdominal_pain": bool(sym & {"abdominal_pain", "severe_abdominal_pain"}),
        "tiredness": "tiredness" in sym, "hx_hypertension": "hypertension" in x.conditions,
        "hx_diabetes": "diabetes" in x.conditions, "hx_complications": bool(x.past_complications),
        "hx_anemia": "anemia" in x.conditions, "first_pregnancy": bool(x.first_pregnancy),
        "no_ifa": x.taking_ifa is False,
    }


# ---------------------------------------------------------------------------
# Result interpretation
# ---------------------------------------------------------------------------
CONCERNS = {
    "anemia_severe": ("Severe anaemia (very low haemoglobin) needs treatment in hospital", "गंभीर खून की कमी, अस्पताल में इलाज ज़रूरी"),
    "anemia_moderate": ("Anaemia (low haemoglobin), which can cause tiredness and dizziness", "खून की कमी, जिससे थकान और चक्कर हो सकते हैं"),
    "anemia_mild": ("Mild anaemia", "हल्की खून की कमी"),
    "bp_severe": ("Possible severe high blood pressure of pregnancy (pre-eclampsia)", "गर्भावस्था में बहुत हाई BP (प्री-एक्लेम्पसिया) हो सकता है"),
    "bp_high": ("Possible high blood pressure of pregnancy", "गर्भावस्था में हाई BP हो सकता है"),
    "headache_vision": ("Signs that can come with dangerously high BP", "खतरनाक हाई BP के संकेत हो सकते हैं"),
    "proteinuria": ("Protein in urine can be linked to pre-eclampsia", "पेशाब में प्रोटीन प्री-एक्लेम्पसिया से जुड़ा हो सकता है"),
    "sugar_high": ("Possible gestational diabetes", "गर्भावधि मधुमेह (शुगर) हो सकती है"),
    "sugar_very_high": ("Possible diabetes in pregnancy", "गर्भावस्था में मधुमेह हो सकता है"),
    "bleeding": ("Bleeding can be a sign of a placenta or pregnancy problem", "खून आना प्लेसेंटा या गर्भ की परेशानी का संकेत हो सकता है"),
    "fetal_movement": ("Baby may need to be checked", "बच्चे की जाँच ज़रूरी है"),
    "fever": ("Possible infection", "संक्रमण हो सकता है"),
    "fever_high": ("Possible serious infection", "गंभीर संक्रमण हो सकता है"),
    "burning_urination": ("Possible urine infection", "पेशाब का संक्रमण हो सकता है"),
    "foul_discharge": ("Possible infection", "संक्रमण हो सकता है"),
    "epds_high": ("You may be going through a hard time emotionally", "आप मन से मुश्किल समय से गुज़र रही हो सकती हैं"),
    "epds_moderate": ("Some emotional strain", "मन पर कुछ बोझ"),
    "self_harm": ("You deserve support right now", "आपको अभी सहारे की ज़रूरत है"),
    "rapid_weight_gain": ("Fluid retention, which can come with high BP", "शरीर में पानी रुकना, हाई BP के साथ हो सकता है"),
    "breathing": ("Breathing problems need urgent care", "साँस की तकलीफ़ में तुरंत इलाज ज़रूरी"),
    "hb_falling": ("Haemoglobin dropping over time", "समय के साथ खून (Hb) घट रहा है"),
}

RECS = {
    "anemia": [("Take your iron tablet daily with lemon water, not tea.", "आयरन की गोली रोज़ नींबू पानी से लें, चाय से नहीं।"),
               ("Eat iron-rich food: palak, chana, gud, til, and an egg if you eat it.", "आयरन वाला खाना: पालक, चना, गुड़, तिल, और अंडा।")],
    "anemia_doctor": [("A doctor may advise extra iron. Recheck Hb in 4 weeks.", "डॉक्टर ज़्यादा आयरन दे सकते हैं। 4 हफ़्ते में Hb दोबारा जाँचें।")],
    "bp": [("Rest, lie on your left side and avoid salty pickles and papad.", "आराम करें, बाईं करवट लेटें, अचार-पापड़ न खाएँ।"),
           ("Get your BP rechecked at the PHC.", "PHC पर BP दोबारा जँचवाएँ।")],
    "sugar": [("Avoid sweets and sugary drinks; walk for 15 minutes after meals.", "मिठाई और मीठे पेय न लें; खाने के बाद 15 मिनट टहलें।"),
              ("A doctor will confirm with a glucose test (OGTT).", "डॉक्टर ग्लूकोज़ जाँच (OGTT) से पुष्टि करेंगे।")],
    "ifa": [("Start taking one iron–folic acid tablet every day after a meal.", "खाने के बाद रोज़ एक आयरन-फ़ोलिक एसिड गोली लें।")],
    "emotional": [("Talk To Me is here any time you want to share. You can also call Tele-MANAS 14416 (free, 24×7).", "'मुझसे बात करें' हमेशा आपके लिए है। Tele-MANAS 14416 पर भी बात कर सकती हैं।")],
    "self_harm": [("Please call Tele-MANAS 14416 now, or 108 if you feel unsafe. Ask someone you trust to stay with you.", "अभी 14416 पर कॉल करें, या असुरक्षित लगे तो 108। किसी अपने को पास बुलाएँ।")],
    "dizziness": [("Sit or lie down on your left side; drink water and eat something.", "बाईं करवट लेटें; पानी पिएँ और कुछ खाएँ।")],
    "swelling": [("Rest with your feet raised; report any swelling of the face or hands.", "पैर ऊँचे रखकर आराम करें; चेहरे/हाथों पर सूजन हो तो बताएँ।")],
    "anc": [("Book your ANC check-up with your ASHA.", "आशा दीदी के साथ ANC जाँच तय करें।")],
}

LEVEL_RECS = {
    IC: [("Go to the {facility} now. Call 108 for a free ambulance.", "अभी {facility} जाएँ। मुफ़्त एम्बुलेंस के लिए 108 पर कॉल करें।"),
         ("Your ASHA and family contact have been alerted.", "आपकी आशा दीदी और परिवार को सूचना दे दी गई है।")],
    MR: [("Visit {facility} for a doctor review within 2 days.", "2 दिन के अंदर {facility} में डॉक्टर को दिखाएँ।"),
         ("Your ASHA has been informed.", "आपकी आशा दीदी को बता दिया गया है।")],
    NA: [("Your ASHA will check in with you this week.", "आपकी आशा दीदी इस हफ़्ते आपसे मिलेंगी।")],
    R: [("Continue your iron tablets and daily plan.", "आयरन की गोली और रोज़ की योजना जारी रखें।")],
}

FOLLOW_UP = {IC: ("Now", "अभी"), MR: ("Within 2 days", "2 दिन के अंदर"), NA: ("Recheck in a week", "एक हफ़्ते में दोबारा जाँच"),
             R: ("At your next ANC check-up", "अगली ANC जाँच पर")}


def _noticed(x: EngineInput, signals: list[Signal]) -> list[dict]:
    items: list[dict] = []
    by_code = {s.code: s for s in signals}
    abnormal_bp = next((s for s in signals if s.code.startswith("bp_")), None)
    anemia = next((s for s in signals if s.code.startswith("anemia_")), None)
    if anemia:
        items.append({"text": anemia.reason, "text_hi": anemia.reason_hi, "level": anemia.level.key})
    if abnormal_bp:
        items.append({"text": abnormal_bp.reason, "text_hi": abnormal_bp.reason_hi, "level": abnormal_bp.level.key})
    symptom_sigs = [s for s in signals if s.source == "danger_ladder"]
    for s in sorted(symptom_sigs, key=lambda s: -s.level):
        items.append({"text": s.reason, "text_hi": s.reason_hi, "level": s.level.key})
    for s in signals:
        if s.source in ("clinical_rules", "epds", "history") and not s.code.startswith("bp_") and s.code not in ("self_harm",):
            items.append({"text": s.reason, "text_hi": s.reason_hi, "level": s.level.key})
    if "self_harm" in by_code:
        items.insert(0, {"text": by_code["self_harm"].reason, "text_hi": by_code["self_harm"].reason_hi,
                         "level": IC.key})
    # Reassuring normals, as in "BP 118/76, normal".
    bp_ok = x.bp_systolic is not None and not abnormal_bp
    hb_ok = x.hemoglobin is not None and not anemia
    if bp_ok and hb_ok and not symptom_sigs:
        items.append({"text": "BP and Hb in normal range", "text_hi": "BP और Hb सामान्य", "level": R.key})
    else:
        if bp_ok:
            items.append({"text": f"BP {x.bp_systolic}/{x.bp_diastolic}, normal",
                          "text_hi": f"BP {x.bp_systolic}/{x.bp_diastolic}, सामान्य", "level": R.key})
        if hb_ok:
            items.append({"text": f"Hemoglobin {x.hemoglobin:g} g/dL, normal",
                          "text_hi": f"खून (Hb) {x.hemoglobin:g}, सामान्य", "level": R.key})
    if not symptom_sigs and x.symptoms is not None:
        items.append({"text": "No warning symptoms", "text_hi": "कोई खतरे का लक्षण नहीं", "level": R.key})
    return items


def _concerns(signals: list[Signal]) -> list[dict]:
    seen, out = set(), []
    for s in sorted(signals, key=lambda s: -s.level):
        if s.code in CONCERNS and s.code not in seen:
            seen.add(s.code)
            en, hi = CONCERNS[s.code]
            out.append({"text": en, "text_hi": hi, "level": s.level.key})
    return out


def _recommendations(level: CareLevel, signals: list[Signal], x: EngineInput, facility_name: str) -> list[dict]:
    codes = {s.code for s in signals}
    picks: list[tuple[str, str]] = [(en.format(facility=facility_name), hi.format(facility=facility_name))
                                    for en, hi in LEVEL_RECS[level]]
    if "self_harm" in codes:
        picks = RECS["self_harm"] + picks
    if codes & {"anemia_moderate", "anemia_mild", "hb_falling"} or "anemia_severe" in codes:
        picks += RECS["anemia"]
        if codes & {"anemia_moderate", "anemia_severe"} and level < IC:
            picks += RECS["anemia_doctor"]
    if codes & {"bp_high", "bp_borderline", "proteinuria"} and level < IC:
        picks += RECS["bp"]
    if codes & {"sugar_high", "sugar_very_high", "sugar_borderline"}:
        picks += RECS["sugar"]
    if "no_ifa" in codes:
        picks += RECS["ifa"]
    if codes & {"epds_high", "epds_moderate"} or {"low_mood", "anxiety"} & set(x.symptoms):
        picks += RECS["emotional"]
    if "dizziness" in x.symptoms and level < IC:
        picks += RECS["dizziness"]
    if "swelling_feet" in x.symptoms and level < IC:
        picks += RECS["swelling"]
    if codes & {"anc_overdue", "anc_missing"}:
        picks += RECS["anc"]
    if level == R and x.next_anc_date:
        picks.append((f"Next ANC on {x.next_anc_date:%d %b}.", f"अगली ANC जाँच {x.next_anc_date:%d %b} को।"))
    seen, out = set(), []
    for en, hi in picks:
        if en not in seen:
            seen.add(en)
            out.append({"text": en, "text_hi": hi})
    return out


def evaluate(x: EngineInput, *, model=None, facility_name: str | None = None) -> EngineResult:
    signals = danger_ladder(x) + clinical_rules(x) + anemia_rules(x) + epds_rules(x) + history_engine(x)
    rule_level = max((s.level for s in signals), default=R)
    model_level = None
    if model is not None:
        model_level = model.predict_level(model_features(x))
        # The model may lift the level by at most one step, and never to Immediate Care.
        if model_level is not None and model_level > rule_level:
            lifted = CareLevel(min(int(model_level), int(rule_level) + 1, int(MR)))
            if lifted > rule_level:
                signals.append(Signal("pattern_model", "combined_pattern", lifted,
                                      "Several findings together need a closer look",
                                      "कई बातें मिलकर ध्यान माँगती हैं"))
    level = max((s.level for s in signals), default=R)  # worst signal wins
    fac = facility_name or {IC: "district hospital", MR: "the CHC", NA: "the PHC", R: "the PHC"}[level]
    referral = None
    if level >= MR:
        referral = ("Emergency referral to a district hospital / FRU" if level == IC
                    else "Doctor review at a CHC")
    elif level == NA:
        referral = "ASHA follow-up visit this week; PHC check if not improving"
    fu_en, fu_hi = FOLLOW_UP[level]
    return EngineResult(level=level, signals=signals, noticed=_noticed(x, signals), concerns=_concerns(signals),
                        recommendations=_recommendations(level, signals, x, fac), follow_up=fu_en,
                        follow_up_hi=fu_hi, referral_suggestion=referral, model_level=model_level)
