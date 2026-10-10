"""Validation rules: plausibility checks and pregnancy reference ranges for extracted values."""
from __future__ import annotations

from dataclasses import dataclass

from app.ocr.extractor import Extracted

# code: (plausible_min, plausible_max)
PLAUSIBLE = {
    "hemoglobin": (3, 20), "platelets": (0.1, 10), "wbc": (500, 60000), "rbc": (1, 8), "mcv": (40, 130),
    "blood_sugar_fasting": (30, 500), "blood_sugar_random": (30, 600), "blood_sugar_ogtt": (30, 600),
    "tsh": (0.005, 150), "fhr": (60, 230), "afi": (0, 50), "efw": (100, 6000),
}
# code: (low_below, high_at_or_above, critical_low_below, critical_high_at_or_above, reference text)
RANGES = {
    "hemoglobin": (11, None, 7, None, "≥ 11 g/dL in pregnancy"),
    "platelets": (1.5, 4.5, 1.0, None, "1.5–4.5 lakh/µL"),
    "wbc": (4000, 15000, 2000, 25000, "4,000–15,000 /µL in pregnancy"),
    "mcv": (80, 100, None, None, "80–100 fL"),
    "blood_sugar_fasting": (60, 92, None, 126, "< 92 mg/dL"),
    "blood_sugar_ogtt": (None, 140, None, 200, "< 140 mg/dL at 2 hours"),
    "blood_sugar_random": (60, 140, None, 200, "< 140 mg/dL"),
    "tsh": (0.1, 4.0, None, 10, "about 0.1–4.0 mIU/L (varies by trimester)"),
    "fhr": (110, 161, 100, 180, "110–160 bpm"),
    "afi": (5, 25, 5, None, "5–25 cm"),
}


@dataclass
class Validated:
    item: Extracted
    valid: bool
    flag: str | None
    reference: str | None
    note: str | None


def validate(item: Extracted) -> Validated:
    code, v = item.code, item.value_num
    if item.kind == "vital" and code == "bp":
        sbp, dbp = v, item.value_num2
        if not (70 <= sbp <= 250 and 40 <= dbp <= 150 and dbp < sbp):
            return Validated(item, False, None, "< 140/90 mmHg", "BP reading looks implausible; please re-check")
        flag = "critical" if sbp >= 160 or dbp >= 110 else "high" if sbp >= 140 or dbp >= 90 else \
            "low" if sbp < 90 or dbp < 60 else "normal"
        return Validated(item, True, flag, "< 140/90 mmHg", None)
    if code == "urine_protein":
        flag = {"nil": "normal", "trace": "normal", "1+": "high"}.get(item.value_text or "", "critical")
        return Validated(item, True, flag, "nil", None)
    if v is None or code not in PLAUSIBLE:
        return Validated(item, True, None, None, None)
    lo_p, hi_p = PLAUSIBLE[code]
    if not (lo_p <= v <= hi_p):
        return Validated(item, False, None, None, f"Value {v:g} is outside the plausible range; OCR may have misread it")
    if code not in RANGES:
        return Validated(item, True, "normal", None, None)
    low, high, crit_low, crit_high, ref = RANGES[code]
    if crit_low is not None and v < crit_low:
        flag = "critical"
    elif crit_high is not None and v >= crit_high:
        flag = "critical"
    elif low is not None and v < low:
        flag = "low"
    elif high is not None and v >= high:
        flag = "high"
    else:
        flag = "normal"
    return Validated(item, True, flag, ref, None)
