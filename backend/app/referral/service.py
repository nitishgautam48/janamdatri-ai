"""Referral summaries: generated from the passport + an assessment, routed to a facility by severity."""
from __future__ import annotations

import html
import io
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.core.config import settings
from app.core.enums import CareLevel, MessageType, NotificationType, ReferralStatus, Role
from app.core.exceptions import NotFound, ValidationFailed
from app.core.pregnancy import gestational_week
from app.core.request_ctx import public_base
from app.database.base import utcnow
from app.database.models import AshaWorker, Assessment, Facility, Message, Mother, Referral
from app.gis.service import facility_dict, mother_location, recommend_facility
from app.notifications.service import notify
from app.notifications.sms import send_sms


def _number() -> str:
    return f"JMD-R-{utcnow():%y%m}-{secrets.token_hex(3).upper()}"


def generate(db: Session, p: Principal, mother: Mother, *, assessment_id=None, care_level: str | None = None,
             facility_id=None, reason: str | None = None, send_to_mother: bool = True) -> Referral:
    assessment = None
    if assessment_id:
        assessment = db.get(Assessment, assessment_id)
        if not assessment or assessment.mother_id != mother.id:
            raise NotFound("Assessment not found")
    else:
        assessment = db.scalar(select(Assessment).where(Assessment.mother_id == mother.id)
                               .order_by(Assessment.created_at.desc()))
    result = assessment.result if assessment else None
    if care_level:
        try:
            level = CareLevel.from_key(care_level)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
    elif result:
        level = CareLevel(result.level)
    else:
        raise ValidationFailed("Run an assessment first or choose a care level", code="care_level_required")
    loc = mother_location(db, mother)
    if facility_id:
        f = db.get(Facility, facility_id)
        if not f:
            raise NotFound("Facility not found")
        facility = facility_dict(f, loc)
    else:
        facility = recommend_facility(db, loc, level) if loc else None
    preg, mh = mother.pregnancy, mother.medical_history
    asha = db.get(AshaWorker, mother.assigned_asha_id) if mother.assigned_asha_id else None
    week = gestational_week(preg.lmp) if preg else None
    findings = [n["text"] for n in (result.what_we_noticed if result else [])]
    if assessment and result:
        vit = assessment.inputs or {}
        if vit.get("bp_systolic") and not any(t.startswith("BP") for t in findings):
            findings.append(f"BP {vit['bp_systolic']}/{vit['bp_diastolic']}")
    recs = [r["text"] for r in (result.recommendations if result else [])]
    ref = Referral(
        mother_id=mother.id, assessment_id=assessment.id if assessment else None, created_by_user_id=p.id,
        referral_number=_number(), care_level=level.key, facility_id=uuid.UUID(facility["id"]) if facility else None,
        facility_snapshot=facility or {}, pregnancy_week=week, findings=findings, recommendations=recs,
        reason=reason or (result.referral_suggestion if result else None),
        patient_snapshot={
            "name": mother.name, "age": mother.age, "village": mother.village, "district": mother.district,
            "pincode": mother.pincode, "rch_id": mother.rch_id,
            "passport_number": mother.passport.passport_number if mother.passport else None,
            "pregnancy_week": week, "edd": preg.edd.isoformat() if preg and preg.edd else None,
            "first_pregnancy": preg.first_pregnancy if preg else None,
            "conditions": list(mh.conditions or []) if mh else [],
            "past_complications": mh.past_complications if mh else None,
            "blood_group": mother.passport.blood_group if mother.passport else None,
            "asha": {"name": asha.name, "sub_centre_code": asha.sub_centre_code, "reg_no": asha.asha_reg_no} if asha else None,
        },
        status=ReferralStatus.GENERATED, share_token=secrets.token_urlsafe(24),
        share_expires_at=utcnow() + timedelta(hours=settings.referral_share_hours),
    )
    db.add(ref)
    db.flush()
    if p.role == Role.ASHA and send_to_mother and asha:
        sender_asha = db.scalar(select(AshaWorker).where(AshaWorker.user_id == p.id)) or asha
        db.add(Message(mother_id=mother.id, asha_id=sender_asha.id, sender_user_id=p.id, sender_role=Role.ASHA,
                       message_type=MessageType.REFERRAL, body=f"Referral to {facility['name'] if facility else 'facility'}",
                       referral_id=ref.id))
        notify(db, mother.user_id, NotificationType.REFERRAL_UPDATE, "New referral from your ASHA",
               f"{level.label}: {facility['name'] if facility else 'see details'}.",
               data={"referral_id": str(ref.id)}, priority="high")
        ref.status = ReferralStatus.SENT
    elif asha and asha.user_id:
        notify(db, asha.user_id, NotificationType.REFERRAL_UPDATE, "Referral generated",
               f"{mother.name or 'A mother'}: {level.label} → {facility['name'] if facility else 'facility'}.",
               data={"referral_id": str(ref.id), "mother_id": str(mother.id)})
    if facility and facility.get("phone") and level >= CareLevel.MEDICAL_REVIEW:
        send_sms(facility["phone"], f"Janamdatri referral {ref.referral_number}: {mother.name or 'patient'}, "
                                     f"wk {week or '-'}, {level.label}. {public_base()}{settings.api_prefix}/referral/shared/"
                                     f"{ref.share_token}?format=html")
        ref.facility_notified = True
    return ref


def referral_dict(r: Referral, *, include_share: bool = True) -> dict:
    lvl = CareLevel.from_key(r.care_level)
    d = {
        "id": str(r.id), "referral_number": r.referral_number, "created_at": r.created_at, "status": r.status,
        "care_level": {"key": lvl.key, "label": lvl.label, "label_hi": lvl.label_hi},
        "patient": r.patient_snapshot, "pregnancy_week": r.pregnancy_week, "assessment_id": str(r.assessment_id) if r.assessment_id else None,
        "findings": r.findings, "recommendations": r.recommendations, "reason": r.reason,
        "facility": r.facility_snapshot or None, "facility_notified": r.facility_notified,
        "timestamp": r.created_at,
        "downloads": {"pdf": f"/api/v1/referral/{r.id}/pdf", "print": f"/api/v1/referral/{r.id}/print"},
    }
    if include_share:
        d["share"] = {"url": f"{public_base()}{settings.api_prefix}/referral/shared/{r.share_token}",
                      "expires_at": r.share_expires_at}
    return d


def by_token(db: Session, token: str) -> Referral:
    r = db.scalar(select(Referral).where(Referral.share_token == token))
    if not r:
        raise NotFound("Referral not found")
    exp = r.share_expires_at if r.share_expires_at.tzinfo else r.share_expires_at.replace(tzinfo=utcnow().tzinfo)
    if exp < utcnow():
        raise NotFound("This referral link has expired", code="link_expired")
    return r


def _rows(r: Referral) -> list[tuple[str, str]]:
    p, f = r.patient_snapshot or {}, r.facility_snapshot or {}
    lvl = CareLevel.from_key(r.care_level)
    asha = p.get("asha") or {}
    return [
        ("Patient", f"{p.get('name') or '-'}, {p.get('age') or '-'}"),
        ("Address", ", ".join(x for x in [p.get("village"), p.get("district"), p.get("pincode")] if x) or "-"),
        ("RCH ID / Passport", f"{p.get('rch_id') or '-'} / {p.get('passport_number') or '-'}"),
        ("Pregnancy week", f"{r.pregnancy_week} weeks" if r.pregnancy_week is not None else "-"),
        ("EDD", p.get("edd") or "-"),
        ("Care level", lvl.label),
        ("Assessment date", f"{r.created_at:%d %b %Y, %H:%M}"),
        ("Findings", "; ".join(r.findings) or "-"),
        ("History", ", ".join(p.get("conditions") or []) or "None recorded"),
        ("Recommendations", " ".join(r.recommendations) or "-"),
        ("Reason", r.reason or "-"),
        ("Facility recommended", f"{f.get('name', '-')}" + (f" · {f['distance_km']} km" if f.get("distance_km") else "")),
        ("ASHA", f"{asha.get('name', '-')} · {asha.get('sub_centre_code', '')}" if asha else "-"),
        ("Referral no.", r.referral_number),
    ]


def render_html(r: Referral) -> str:
    rows = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in _rows(r))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Referral {html.escape(r.referral_number)}</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#222}}h1{{font-size:20px;margin:0 0 4px}}
.sub{{color:#666;margin-bottom:16px}}table{{border-collapse:collapse;width:100%;max-width:720px}}
th,td{{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid #ddd}}th{{width:180px;color:#555}}
.note{{margin-top:16px;font-size:12px;color:#666}}@media print{{button{{display:none}}}}</style></head>
<body><h1>Referral Summary · रेफ़रल पर्ची</h1><div class="sub">Janamdatri-AI · generated {r.created_at:%d %b %Y %H:%M} UTC</div>
<table>{rows}</table><p class="note">Generated by Janamdatri-AI to support, not replace, clinical judgement.</p>
<button onclick="window.print()">Print</button></body></html>"""


def render_pdf(r: Referral) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                            title=f"Referral {r.referral_number}")
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    story = [Paragraph("Referral Summary", styles["Title"]),
             Paragraph(f"Janamdatri-AI · {r.created_at:%d %b %Y %H:%M} UTC", styles["Normal"]), Spacer(1, 8)]
    data = [[Paragraph(f"<b>{html.escape(k)}</b>", body), Paragraph(html.escape(str(v)), body)] for k, v in _rows(r)]
    t = Table(data, colWidths=[45 * mm, 125 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.grey),
                           ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke)]))
    story += [t, Spacer(1, 10), Paragraph("Generated to support, not replace, clinical judgement.", styles["Italic"])]
    doc.build(story)
    return buf.getvalue()
