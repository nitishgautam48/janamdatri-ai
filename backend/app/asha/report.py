"""Monthly work report for an ASHA (to share with her ANM) and an estimated incentive tracker.

Counts come only from what was recorded in the app during the month: visits, ANC contacts, health checks,
referrals, emergencies, deliveries, IFA tablets given. Incentive amounts are ESTIMATES from central NHM norms
(states pay different amounts and add their own items); the ASHA should confirm them with her ANM / block office.
"""
from __future__ import annotations

import csv
import html
import io
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.asha.service import mother_row, mothers_for_asha_query
from app.core.enums import CareLevel
from app.database.models import AshaWorker, AssessmentResult, EmergencyEvent, Message, Referral, Visit

# (key, label, label_hi, rupees, rule) — central NHM norms as commonly published; states vary.
INCENTIVES = [
    ("jsy", "JSY: institutional delivery (rural)", "JSY: संस्थागत प्रसव (ग्रामीण)", 600,
     "per delivery of a mother she looks after, recorded this month"),
    ("hbnc", "HBNC: newborn home visits completed", "HBNC: नवजात गृह भेंट पूरी", 250,
     "per newborn with 6 or more postnatal home visits in the first 42 days, finished this month"),
]
NOTE = ("Estimated from central NHM norms. Your state may pay different amounts or more items; confirm with your "
        "ANM or block office.")
NOTE_HI = "केंद्रीय NHM नियमों के अनुसार अनुमान। आपका राज्य अलग राशि या और काम दे सकता है; ANM या ब्लॉक कार्यालय से पुष्टि करें।"


def month_bounds(month: str | None) -> tuple[date, date]:
    """'2026-10' → (2026-10-01, 2026-11-01); default the current month."""
    if month:
        y, m = (int(x) for x in month.split("-"))
        start = date(y, m, 1)
    else:
        start = date.today().replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def _dt(d: date) -> datetime:
    return datetime.combine(d, time.min).astimezone()


def build(db: Session, asha: AshaWorker, month: str | None = None) -> dict:
    start, end = month_bounds(month)
    s, e = _dt(start), _dt(end)
    mothers = db.scalars(mothers_for_asha_query(asha)).all()
    ids = [m.id for m in mothers]
    visits = db.scalars(select(Visit).where(Visit.asha_id == asha.id, Visit.visit_date >= s, Visit.visit_date < e)).all()
    results = db.scalars(select(AssessmentResult).where(AssessmentResult.mother_id.in_(ids), AssessmentResult.created_at >= s,
                                                        AssessmentResult.created_at < e)).all() if ids else []
    referrals = db.scalar(select(func.count()).select_from(Referral).where(
        Referral.mother_id.in_(ids), Referral.created_at >= s, Referral.created_at < e)) if ids else 0
    emergencies = db.scalars(select(EmergencyEvent).where(EmergencyEvent.mother_id.in_(ids), EmergencyEvent.created_at >= s,
                                                          EmergencyEvent.created_at < e)).all() if ids else []
    reminders = db.scalar(select(func.count()).select_from(Message).where(
        Message.asha_id == asha.id, Message.sender_role == "asha", Message.created_at >= s, Message.created_at < e)) or 0
    new_regs = sum(1 for m in mothers if m.created_at and s <= (m.created_at if m.created_at.tzinfo else m.created_at.astimezone()) < e)

    def ifa(v: Visit) -> int:
        n = (v.checklist or {}).get("ifa_tablets_given")
        return n if isinstance(n, int) else (30 if n is True else 0)

    deliveries = [m for m in mothers if m.pregnancy and m.pregnancy.delivery_date and start <= m.pregnancy.delivery_date < end]
    hbnc_done = []
    for m in mothers:
        dd = m.pregnancy.delivery_date if m.pregnancy else None
        if dd and start <= dd + timedelta(days=42) < end:
            pnc = db.scalar(select(func.count()).select_from(Visit).where(
                Visit.mother_id == m.id, Visit.visit_type == "postnatal", Visit.visit_date >= _dt(dd),
                Visit.visit_date < _dt(dd + timedelta(days=43))))
            if pnc >= 6:
                hbnc_done.append(m)
    high_risk_ids = {r.mother_id for r in results if r.level >= CareLevel.MEDICAL_REVIEW}
    anaemia = sum(1 for v in visits if v.hemoglobin is not None and v.hemoglobin < 11)
    counts = {
        "mothers": len(mothers), "pregnant": sum(1 for m in mothers if m.status == "pregnant"),
        "postnatal": sum(1 for m in mothers if m.status == "postnatal"), "new_registrations": new_regs,
        "home_visits": len(visits), "anc_visits": sum(1 for v in visits if v.visit_type == "anc"),
        "postnatal_visits": sum(1 for v in visits if v.visit_type == "postnatal"),
        "mothers_visited": len({v.mother_id for v in visits}), "health_checks": len(results),
        "high_risk_found": len(high_risk_ids), "anaemia_found": anaemia, "referrals": referrals or 0,
        "emergencies": len(emergencies), "deliveries": len(deliveries),
        "ifa_tablets_given": sum(ifa(v) for v in visits),
        "diet_counselling": sum(1 for v in visits if (v.checklist or {}).get("diet_counselling")),
        "danger_signs_explained": sum(1 for v in visits if (v.checklist or {}).get("danger_signs_explained")),
        "reminders_sent": reminders,
    }
    qty = {"jsy": len(deliveries), "hbnc": len(hbnc_done)}
    incentives = [{"key": k, "label": lab, "label_hi": lab_hi, "rate": rate, "count": qty[k], "amount": rate * qty[k],
                   "rule": rule} for k, lab, lab_hi, rate, rule in INCENTIVES]
    high_risk = [mother_row(db, m, asha) for m in mothers if m.id in high_risk_ids]
    by_id = {m.id: m for m in mothers}
    due_next = []
    for m in mothers:
        nd = m.pregnancy.next_anc_date if m.pregnancy else None
        if nd and end <= nd < (end + timedelta(days=32)).replace(day=1):
            due_next.append({"name": m.name, "village": m.village, "date": nd})
    return {
        "asha": {"name": asha.name, "asha_reg_no": asha.asha_reg_no, "sub_centre": asha.sub_centre_name or asha.sub_centre_code,
                 "village": asha.village, "district": asha.district, "anm_name": asha.anm_name},
        "month": start.strftime("%Y-%m"), "month_label": start.strftime("%B %Y"), "from": start, "to": end - timedelta(days=1),
        "counts": counts, "incentives": incentives, "incentive_total": sum(i["amount"] for i in incentives),
        "incentive_note": NOTE, "incentive_note_hi": NOTE_HI,
        "high_risk": high_risk, "anc_due_next_month": sorted(due_next, key=lambda r: r["date"]),
        "deliveries": [{"name": m.name, "date": m.pregnancy.delivery_date} for m in deliveries],
        "_mothers": by_id,
    }


LABELS = [("mothers", "Mothers looked after"), ("pregnant", "Pregnant"), ("postnatal", "Postnatal"),
          ("new_registrations", "New registrations"), ("home_visits", "Home visits"), ("mothers_visited", "Mothers visited"),
          ("anc_visits", "ANC visits"), ("postnatal_visits", "Postnatal visits"), ("health_checks", "Health checks"),
          ("high_risk_found", "High-risk found"), ("anaemia_found", "Visits with Hb below 11"), ("referrals", "Referrals"),
          ("emergencies", "Emergencies (SOS)"), ("deliveries", "Deliveries"), ("ifa_tablets_given", "IFA tablets given"),
          ("diet_counselling", "Diet counselling"), ("danger_signs_explained", "Danger signs explained"),
          ("reminders_sent", "Messages / reminders sent")]


def to_csv(r: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    a = r["asha"]
    w.writerow(["Janamdatri-AI monthly ASHA report", r["month_label"]])
    w.writerow(["ASHA", a["name"], a["asha_reg_no"], a["sub_centre"] or "", a["district"] or ""])
    w.writerow([])
    w.writerow(["Indicator", "Count"])
    for k, lab in LABELS:
        w.writerow([lab, r["counts"][k]])
    w.writerow([])
    w.writerow(["Incentive (estimate)", "Count", "Rate (Rs)", "Amount (Rs)"])
    for i in r["incentives"]:
        w.writerow([i["label"], i["count"], i["rate"], i["amount"]])
    w.writerow(["Total (estimate)", "", "", r["incentive_total"]])
    w.writerow([NOTE])
    w.writerow([])
    w.writerow(["High-risk mothers", "Week", "Care level", "Village", "Last visit"])
    for m in r["high_risk"]:
        w.writerow([m["name"], m["week"] or "", m["care_level"] or "", m["village"] or "", m["last_visit_at"] or ""])
    return buf.getvalue()


def to_html(r: dict) -> str:
    esc = html.escape
    a = r["asha"]
    rows = "".join(f"<tr><td>{esc(lab)}</td><td class=n>{r['counts'][k]}</td></tr>" for k, lab in LABELS)
    inc = "".join(f"<tr><td>{esc(i['label'])}<br><small>{esc(i['rule'])}</small></td><td class=n>{i['count']}</td>"
                  f"<td class=n>₹{i['rate']}</td><td class=n>₹{i['amount']:,}</td></tr>" for i in r["incentives"])
    hr = "".join(f"<tr><td>{esc(m['name'] or '')}</td><td>{m['week'] or ''}</td><td>{esc(m['subtitle'] or '')}</td>"
                 f"<td>{esc(m['village'] or '')}</td></tr>" for m in r["high_risk"]) or "<tr><td colspan=4>None</td></tr>"
    due = "".join(f"<tr><td>{esc(d['name'] or '')}</td><td>{esc(d['village'] or '')}</td><td>{d['date']:%d %b}</td></tr>"
                  for d in r["anc_due_next_month"]) or "<tr><td colspan=3>None</td></tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>ASHA report {r['month_label']}</title>
<style>body{{font:14px/1.45 system-ui,sans-serif;color:#111;margin:24px;max-width:820px}} h1{{font-size:20px;margin:0}}
h2{{font-size:15px;margin:22px 0 6px;color:#0f766e}} table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:5px 8px;text-align:left;vertical-align:top}}
th{{background:#e6f4f1}} .n{{text-align:right;white-space:nowrap}} small{{color:#555}} .meta{{color:#444;margin:4px 0 0}}
.note{{font-size:12px;color:#555;margin-top:6px}} @media print{{button{{display:none}}}}</style></head><body>
<button onclick="print()" style="float:right;padding:8px 14px">Print / Save as PDF</button>
<h1>Monthly ASHA work report · {esc(r['month_label'])}</h1>
<p class=meta>{esc(a['name'])} · {esc(a['asha_reg_no'])} · {esc(a['sub_centre'] or '')} · {esc(a['district'] or '')}
{(' · ANM: ' + esc(a['anm_name'])) if a.get('anm_name') else ''}</p>
<h2>Work done</h2><table><tr><th>Indicator</th><th class=n>Count</th></tr>{rows}</table>
<h2>Incentives (estimate)</h2><table><tr><th>Item</th><th class=n>Count</th><th class=n>Rate</th><th class=n>Amount</th></tr>{inc}
<tr><th>Total (estimate)</th><th></th><th></th><th class=n>₹{r['incentive_total']:,}</th></tr></table><p class=note>{esc(NOTE)}</p>
<h2>High-risk mothers this month</h2><table><tr><th>Name</th><th>Week</th><th>Status</th><th>Village</th></tr>{hr}</table>
<h2>ANC due next month</h2><table><tr><th>Name</th><th>Village</th><th>Date</th></tr>{due}</table>
<p class=note>Generated by Janamdatri-AI from records entered in the app.</p></body></html>"""


def sms_summary(r: dict) -> str:
    c = r["counts"]
    return (f"ASHA report {r['month_label']} - {r['asha']['name']} ({r['asha']['asha_reg_no']}): {c['home_visits']} home visits, "
            f"{c['anc_visits']} ANC, {c['high_risk_found']} high-risk, {c['referrals']} referrals, {c['deliveries']} deliveries, "
            f"{c['ifa_tablets_given']} IFA given. High-risk: "
            + (", ".join((m['name'] or '').split(' ')[0] for m in r["high_risk"][:5]) or "none") + ".")
