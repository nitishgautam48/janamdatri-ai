"""Report pipeline: OCR → structured extraction → validation → Gemma explanation."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import ChatMessage, get_llm
from app.ai.prompts import REPORT_SYSTEM
from app.ai.safety import SafetyCheck, apply
from app.core.enums import NotificationType, ReportStatus
from app.core.storage import absolute_path
from app.database.base import utcnow
from app.database.models import AshaWorker, Mother, Report, ReportFinding
from app.notifications.service import notify
from app.ocr.engine import extract_text
from app.ocr.extractor import extract, report_date
from app.ocr.validator import validate

log = logging.getLogger(__name__)

FLAG_WORDS = {"low": ("low", "कम"), "high": ("high", "ज़्यादा"), "critical": ("needs urgent attention", "तुरंत ध्यान दें"),
              "normal": ("normal", "सामान्य")}
EXPLAIN = {
    "hemoglobin": {
        "low": ("Your haemoglobin is below the pregnancy level of 11. This is anaemia (khoon ki kami), which can make you tired and dizzy. Take your iron tablet daily with lemon water and eat palak, chana and gud.",
                "आपका हीमोग्लोबिन 11 से कम है, यानी खून की कमी। इससे थकान और चक्कर आ सकते हैं। आयरन की गोली रोज़ नींबू पानी से लें, पालक, चना, गुड़ खाएँ।"),
        "critical": ("Your haemoglobin is very low. Please see a doctor at the CHC or district hospital within a day.",
                     "आपका हीमोग्लोबिन बहुत कम है। एक दिन के अंदर बड़े अस्पताल में डॉक्टर को दिखाएँ।"),
        "normal": ("Your haemoglobin is in the normal range. Keep taking your iron tablets.", "आपका हीमोग्लोबिन सामान्य है। आयरन की गोली जारी रखें।"),
    },
    "platelets": {"normal": ("Platelets are normal.", "प्लेटलेट्स सामान्य हैं।"),
                  "low": ("Platelets are a little low; your doctor should review this.", "प्लेटलेट्स थोड़े कम हैं; डॉक्टर को दिखाएँ।"),
                  "critical": ("Platelets are very low; see a doctor soon.", "प्लेटलेट्स बहुत कम हैं; जल्द डॉक्टर को दिखाएँ।")},
    "wbc": {"normal": ("WBC (infection-fighting cells) is normal.", "WBC सामान्य है।"),
            "high": ("WBC is raised, which can happen with an infection. Mention any fever to your doctor.", "WBC बढ़ा है, संक्रमण में ऐसा हो सकता है।")},
    "bp": {"high": ("Your BP is high for pregnancy. Get it rechecked and see a doctor within 2 days.", "BP ज़्यादा है। 2 दिन में डॉक्टर को दिखाएँ।"),
           "critical": ("Your BP is very high. Go to the hospital today.", "BP बहुत ज़्यादा है। आज ही अस्पताल जाएँ।"),
           "normal": ("Your BP is normal.", "आपका BP सामान्य है।")},
    "blood_sugar_fasting": {"high": ("Fasting sugar is above the pregnancy limit; a doctor should check for gestational diabetes.", "खाली पेट शुगर ज़्यादा है; डॉक्टर से जाँच कराएँ।")},
    "blood_sugar_ogtt": {"high": ("The 2-hour glucose test is 140 or more, which suggests gestational diabetes. See a doctor for a plan.", "2 घंटे की शुगर 140 या ज़्यादा है; डॉक्टर से योजना बनाएँ।"),
                         "normal": ("Your glucose test is normal.", "शुगर जाँच सामान्य है।")},
}


def _finding_line(f: ReportFinding) -> str:
    return f"{f.label}: {f.value_text or ''}" + (f" ({f.flag})" if f.flag else "")


def _template_explanation(findings: list[ReportFinding], lang: str) -> str:
    hi = lang == "hi"
    lines: list[str] = []
    labs = [f for f in findings if f.kind in ("lab_value", "vital", "ultrasound") and f.valid]
    if labs:
        found = ", ".join(f"{f.label} {f.value_text or ''} ({FLAG_WORDS.get(f.flag or 'normal', ('',''))[1 if hi else 0]})"
                          .replace("  ", " ") for f in labs[:6])
        lines.append(("मिला: " if hi else "Found: ") + found + ".")
    for f in labs:
        text = EXPLAIN.get(f.code, {}).get(f.flag or "")
        if text:
            lines.append(text[1] if hi else text[0])
    meds = [f for f in findings if f.kind == "medicine"]
    if meds:
        lines.append(("दवाइयाँ: " if hi else "Medicines: ") + "; ".join(
            f"{m.label}{' – ' + m.value_text if m.value_text else ''}" for m in meds))
    advice = [f.value_text for f in findings if f.kind == "recommendation"]
    if advice:
        lines.append(("डॉक्टर की सलाह: " if hi else "Doctor's advice: ") + "; ".join(advice[:3]))
    invalid = [f for f in findings if not f.valid]
    if invalid:
        lines.append("कुछ अंक साफ़ नहीं पढ़े गए; आशा दीदी से जाँच करवाएँ।" if hi else
                     "Some numbers could not be read clearly; please ask your ASHA to check them.")
    if not lines:
        lines.append("इस रिपोर्ट से कोई अंक नहीं पढ़ा जा सका। साफ़ रोशनी में दोबारा फ़ोटो लें।" if hi else
                     "We could not read values from this report. Please retake the photo in good light, flat and in focus.")
    return "\n".join(lines)


def analyze_report(db: Session, report: Report, client_text: str | None = None, language: str = "en") -> Report:
    report.status = ReportStatus.PROCESSING
    db.flush()
    text, engine = extract_text(absolute_path(report.file_path), report.content_type)
    if len(text.strip()) < 10 and client_text:
        text, engine = client_text, "client"
    report.ocr_text, report.ocr_engine = text[:20000], engine
    report.findings.clear()
    db.flush()
    report.report_date = report.report_date or report_date(text)
    observed = report.report_date or utcnow().date()
    findings: list[ReportFinding] = []
    for item in extract(text):
        v = validate(item)
        f = ReportFinding(report_id=report.id, mother_id=report.mother_id, kind=item.kind, code=item.code,
                          label=item.label, value_text=item.value_text, value_num=item.value_num,
                          value_num2=item.value_num2, unit=item.unit, flag=v.flag, reference_range=v.reference,
                          valid=v.valid, validation_note=v.note, observed_on=observed)
        report.findings.append(f)
        findings.append(f)
    # Explanation: Gemma, grounded only in validated values; template fallback.
    explanation = None
    llm = get_llm("report")
    if llm.available and findings:
        values = "\n".join(_finding_line(f) for f in findings if f.valid)
        explanation = llm.generate([
            ChatMessage("system", REPORT_SYSTEM.format(language="Hindi" if language == "hi" else "English")),
            ChatMessage("user", f"Report type: {report.report_type}\nExtracted values:\n{values}"),
        ])
    report.explanation = apply(explanation or _template_explanation(findings, language), SafetyCheck(), language)
    critical = [f for f in findings if f.flag == "critical" and f.valid]
    report.summary = {
        "engine": engine,
        "values_found": sum(1 for f in findings if f.kind in ("lab_value", "vital", "ultrasound")),
        "flags": [{"code": f.code, "label": f.label, "value": f.value_text, "flag": f.flag}
                  for f in findings if f.flag in ("low", "high", "critical")],
        "medicines": [f.label for f in findings if f.kind == "medicine"],
        "diagnoses": [f.label for f in findings if f.kind == "diagnosis"],
        "needs_attention": bool(critical),
        "unreadable": not findings,
    }
    report.status = ReportStatus.ANALYZED if findings or text.strip() else ReportStatus.FAILED
    report.error = None if report.status == ReportStatus.ANALYZED else "No text could be read from this file"
    report.analyzed_at = utcnow()
    if critical:
        mother = db.get(Mother, report.mother_id)
        asha = db.get(AshaWorker, mother.assigned_asha_id) if mother and mother.assigned_asha_id else None
        if asha and asha.user_id:
            notify(db, asha.user_id, NotificationType.SYSTEM, "Report needs attention",
                   f"{mother.name or 'A mother'}: " + ", ".join(f"{f.label} {f.value_text}" for f in critical),
                   data={"mother_id": str(mother.id), "report_id": str(report.id)}, priority="high")
    return report


def report_dict(r: Report, include_text: bool = False) -> dict:
    d = {
        "id": str(r.id), "mother_id": str(r.mother_id), "report_type": r.report_type, "title": r.title,
        "status": r.status, "uploaded_at": r.created_at, "report_date": r.report_date,
        "original_filename": r.original_filename, "content_type": r.content_type, "size_bytes": r.size_bytes,
        "file_url": f"/api/v1/reports/{r.id}/file", "explanation": r.explanation, "summary": r.summary,
        "error": r.error, "analyzed_at": r.analyzed_at,
        "findings": [{
            "kind": f.kind, "code": f.code, "label": f.label, "value": f.value_text, "unit": f.unit, "flag": f.flag,
            "reference_range": f.reference_range, "valid": f.valid, "note": f.validation_note,
        } for f in r.findings],
    }
    if include_text:
        d["ocr_text"] = r.ocr_text
        d["ocr_engine"] = r.ocr_engine
    return d


def medication_schedule(db: Session, mother_id) -> list[dict]:
    meds = db.scalars(select(ReportFinding).where(ReportFinding.mother_id == mother_id, ReportFinding.kind == "medicine")
                      .order_by(ReportFinding.created_at.desc())).all()
    seen, out = set(), []
    for m in meds:
        if m.code in seen:
            continue
        seen.add(m.code)
        out.append({"code": m.code, "name": m.label, "instructions": m.value_text, "source_report_id": str(m.report_id)})
    return out
