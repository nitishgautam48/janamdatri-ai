"""Structured extraction from OCR text: lab values, BP, sugar, medicines, diagnoses, advice."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

NUM = r"(\d{1,3}(?:[.,]\d{1,2})?)"


@dataclass
class Extracted:
    kind: str  # lab_value | vital | medicine | diagnosis | recommendation | ultrasound
    code: str
    label: str
    value_text: str | None = None
    value_num: float | None = None
    value_num2: float | None = None
    unit: str | None = None
    extra: dict = field(default_factory=dict)


LAB_PATTERNS: list[tuple[str, str, str, str]] = [
    # code, label, regex (group 1 = number), default unit
    ("hemoglobin", "Hemoglobin", r"\b(?:ha?emoglobin|hgb|hb)\b[^\d\n]{0,25}?" + NUM, "g/dL"),
    ("platelets", "Platelets", r"\bplatelets?(?:\s*count)?\b[^\d\n]{0,25}?(\d{1,7}(?:[.,]\d{1,3})*)", ""),
    ("wbc", "WBC", r"\b(?:wbc|tlc|total\s+leu[ck]ocyte\s+count|white\s+blood\s+cells?)(?:\s*count)?\b[^\d\n]{0,25}?(\d{1,6}(?:[.,]\d{1,3})*)", "/µL"),
    ("rbc", "RBC", r"\b(?:rbc|red\s+blood\s+cells?)(?:\s*count)?\b[^\d\n]{0,25}?(\d(?:\.\d{1,2})?)", "million/µL"),
    ("mcv", "MCV", r"\bmcv\b[^\d\n]{0,15}?" + NUM, "fL"),
    ("blood_sugar_fasting", "Blood sugar (fasting)", r"\b(?:fbs|fbg|fasting\s+(?:blood\s+)?(?:sugar|glucose|plasma\s+glucose))\b[^\d\n]{0,25}?(\d{2,3}(?:\.\d)?)", "mg/dL"),
    ("blood_sugar_ogtt", "Blood sugar (OGTT 2 hr)", r"\b(?:ogtt|2\s*-?\s*h(?:ou)?r?s?\s+(?:post\s*)?(?:glucose|sugar)?|pp\s*bs|ppbs|post\s*prandial(?:\s+(?:blood\s+)?(?:sugar|glucose))?)\b[^\d\n]{0,25}?(\d{2,3}(?:\.\d)?)", "mg/dL"),
    ("blood_sugar_random", "Blood sugar (random)", r"\b(?:rbs|random\s+(?:blood\s+)?(?:sugar|glucose))\b[^\d\n]{0,25}?(\d{2,3}(?:\.\d)?)", "mg/dL"),
    ("tsh", "TSH", r"\btsh\b[^\d\n]{0,25}?(\d{1,3}(?:\.\d{1,3})?)", "mIU/L"),
    ("fhr", "Fetal heart rate", r"\b(?:fhr|fetal\s+heart\s+(?:rate)?|fh\s*rate)\b[^\d\n]{0,20}?(\d{2,3})", "bpm"),
    ("afi", "Amniotic fluid index", r"\bafi\b[^\d\n]{0,20}?" + NUM, "cm"),
    ("efw", "Estimated fetal weight", r"\befw\b[^\d\n]{0,20}?(\d{3,4})", "g"),
]

BP_RE = re.compile(r"\b(?:bp|b\.p\.|blood\s+pressure)\b[^\d\n]{0,15}?(\d{2,3})\s*/\s*(\d{2,3})", re.I)
URINE_PROTEIN_RE = re.compile(r"\b(?:urine\s+)?(?:albumin|protein)\b[^\n]{0,20}?(nil|absent|trace|[1-4]\s*\+|\+{1,4})", re.I)
GA_RE = re.compile(r"\b(\d{1,2})\s*w(?:ee)?k?s?\s*(\d)\s*d(?:ays?)?\b", re.I)
PLACENTA_RE = re.compile(r"placenta[^\n]{0,40}?\b(low[\s-]*lying|previa|praevia|anterior|posterior|fundal)\b", re.I)
PRESENTATION_RE = re.compile(r"\b(cephalic|breech|transverse|vertex)\b", re.I)

MEDICINES = {
    "ifa": (r"\b(?:ifa|iron\s*(?:\+|and)?\s*folic|ferrous\s+\w+|iron\s+folic\s+acid|livogen|autrin|fefol|orofer)\b", "Iron + folic acid (IFA)"),
    "folic_acid": (r"\bfolic\s+acid\b|\bfolvite\b", "Folic acid"),
    "calcium": (r"\bcalcium\b|\bshelcal\b|\bcalcimax\b|\bcac?o3\b", "Calcium"),
    "albendazole": (r"\balbendazole\b|\bzentel\b", "Albendazole (deworming)"),
    "metformin": (r"\bmetformin\b", "Metformin"),
    "insulin": (r"\binsulin\b", "Insulin"),
    "labetalol": (r"\blabetalol\b", "Labetalol (BP)"),
    "nifedipine": (r"\bnifedipine\b", "Nifedipine (BP)"),
    "methyldopa": (r"\bmethyl\s*dopa\b|\balphadopa\b", "Methyldopa (BP)"),
    "aspirin": (r"\baspirin\b|\becosprin\b", "Aspirin (low dose)"),
    "levothyroxine": (r"\b(?:levo)?thyroxine\b|\bthyronorm\b|\beltroxin\b", "Thyroxine"),
    "paracetamol": (r"\bparacetamol\b|\bcrocin\b|\bdolo\b", "Paracetamol"),
    "doxylamine": (r"\bdoxylamine\b|\bdoxinate\b", "Doxylamine (nausea)"),
    "progesterone": (r"\bprogesterone\b|\bsusten\b|\bduphaston\b|\bdydrogesterone\b", "Progesterone"),
    "vitamin_d": (r"\bvitamin\s*d3?\b|\bcholecalciferol\b", "Vitamin D"),
    "antacid": (r"\bantacid\b|\bgelusil\b|\bdigene\b", "Antacid"),
}
FREQ = [
    (r"\b1\s*-\s*0\s*-\s*1\b|\bbd\b|\bbid\b|twice\s+(?:a\s+)?daily", "twice a day"),
    (r"\b1\s*-\s*1\s*-\s*1\b|\btds\b|\btid\b|thrice", "three times a day"),
    (r"\b0\s*-\s*0\s*-\s*1\b|\bhs\b|at\s+night|bed\s*time", "at night"),
    (r"\b0\s*-\s*1\s*-\s*0\b|after\s+lunch", "after lunch"),
    (r"\b1\s*-\s*0\s*-\s*0\b|\bod\b|once\s+(?:a\s+)?daily|daily", "once a day"),
    (r"\bsos\b|when\s+needed|if\s+needed", "only when needed"),
]
DOSE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|iu)\b", re.I)
COUNT_RE = re.compile(r"\b(\d)\s*(?:tab|tabs|tablets?|caps?)\b", re.I)

DIAGNOSES = {
    "anemia": (r"\ban(?:a)?emi[ac]\b|\ban(?:a)?emia\b", "Anaemia"),
    "gdm": (r"\bgdm\b|gestational\s+diabetes", "Gestational diabetes"),
    "pre_eclampsia": (r"pre[\s-]*eclampsia|\bpih\b|pregnancy[\s-]+induced\s+hypertension|gestational\s+hypertension", "High BP of pregnancy"),
    "hypothyroidism": (r"hypothyroid", "Hypothyroidism"),
    "uti": (r"\buti\b|urinary\s+tract\s+infection", "Urine infection"),
    "oligohydramnios": (r"oligohydramnios", "Low amniotic fluid"),
    "polyhydramnios": (r"polyhydramnios", "High amniotic fluid"),
    "placenta_previa": (r"placenta\s+pr(?:a)?evia|low[\s-]*lying\s+placenta", "Low-lying placenta"),
    "fgr": (r"\biugr\b|\bfgr\b|growth\s+restriction", "Baby smaller than expected (growth restriction)"),
    "breech": (r"\bbreech\b", "Baby in breech position"),
    "twins": (r"\btwins?\b|twin\s+gestation|dichorionic|monochorionic", "Twin pregnancy"),
    "thalassemia": (r"thalass(?:a)?emia", "Thalassaemia trait"),
}
ADVICE_RE = re.compile(r"^\s*(?:advice|advised|adv\.?|recommend(?:ed|ation)?s?|plan|follow[\s-]*up|f/u|review|repeat|rx\s*advice)\b[:\-\s]*(.+)$", re.I | re.M)
DATE_RES = [
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+(\d{4})\b", re.I), "dMy"),
]


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _parse_platelets(raw: str, context: str) -> float | None:
    """Normalise platelet counts to lakh/µL."""
    try:
        v = _num(raw)
    except ValueError:
        return None
    ctx = context.lower()
    if "lakh" in ctx or "lac" in ctx:
        return v
    if v >= 1000:  # e.g. 240000 /cumm
        return round(v / 100000, 2)
    if v >= 10:  # e.g. 240 x10^3/µL
        return round(v / 100, 2)
    return v


def _wbc(raw: str) -> float | None:
    try:
        v = _num(raw)
    except ValueError:
        return None
    return v * 1000 if v < 100 else v  # "8.2 x10^3" → 8200


def report_date(text: str) -> date | None:
    for rx, kind in DATE_RES:
        for m in rx.finditer(text):
            try:
                if kind == "dmy":
                    d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    y = y + 2000 if y < 100 else y
                    return date(y, mth, d)
                return datetime.strptime(f"{m.group(1)} {m.group(2)[:3]} {m.group(3)}", "%d %b %Y").date()
            except ValueError:
                continue
    return None


def extract(text: str) -> list[Extracted]:
    out: list[Extracted] = []
    t = text or ""
    for code, label, pattern, unit in LAB_PATTERNS:
        m = re.search(pattern, t, re.I)
        if not m:
            continue
        raw = m.group(1)
        context = t[m.start(): m.end() + 25]
        if code == "platelets":
            val, unit = _parse_platelets(raw, context), "lakh/µL"
        elif code == "wbc":
            val = _wbc(raw)
        else:
            try:
                val = _num(raw)
            except ValueError:
                continue
        if val is None:
            continue
        out.append(Extracted("ultrasound" if code in ("fhr", "afi", "efw") else "lab_value", code, label,
                             f"{val:g} {unit}".strip(), val, unit=unit or None))
    for m in BP_RE.finditer(t):
        out.append(Extracted("vital", "bp", "Blood pressure", f"{m.group(1)}/{m.group(2)} mmHg",
                             float(m.group(1)), float(m.group(2)), "mmHg"))
        break
    if m := URINE_PROTEIN_RE.search(t):
        raw = m.group(1).lower().replace(" ", "")
        norm = {"absent": "nil", "+": "1+", "++": "2+", "+++": "3+", "++++": "4+"}.get(raw, raw)
        out.append(Extracted("lab_value", "urine_protein", "Urine protein", norm))
    if m := GA_RE.search(t):
        out.append(Extracted("ultrasound", "gestational_age", "Gestational age (scan)", f"{m.group(1)} weeks {m.group(2)} days",
                             float(m.group(1)) + int(m.group(2)) / 7, unit="weeks"))
    if m := PLACENTA_RE.search(t):
        out.append(Extracted("ultrasound", "placenta", "Placenta", m.group(1).lower()))
    if m := PRESENTATION_RE.search(t):
        out.append(Extracted("ultrasound", "presentation", "Baby's position", m.group(1).lower()))
    # Medicines: look line by line so dose/frequency attach to the right drug.
    for line in t.splitlines():
        for code, (rx, name) in MEDICINES.items():
            if re.search(rx, line, re.I) and not any(e.kind == "medicine" and e.code == code for e in out):
                if code == "folic_acid" and re.search(MEDICINES["ifa"][0], line, re.I):
                    continue
                dose = DOSE_RE.search(line)
                count = COUNT_RE.search(line)
                freq = next((label for rx_f, label in FREQ if re.search(rx_f, line, re.I)), None)
                parts = [p for p in [f"{count.group(1)} tab" if count else None,
                                     f"{dose.group(1)} {dose.group(2)}" if dose else None, freq] if p]
                out.append(Extracted("medicine", code, name, " · ".join(parts) or None,
                                     extra={"frequency": freq, "line": line.strip()[:160]}))
    for code, (rx, name) in DIAGNOSES.items():
        if re.search(rx, t, re.I):
            out.append(Extracted("diagnosis", code, name, name))
    for m in ADVICE_RE.finditer(t):
        txt = m.group(1).strip()
        if 3 <= len(txt) <= 200:
            out.append(Extracted("recommendation", "advice", "Doctor's advice", txt))
    return out
