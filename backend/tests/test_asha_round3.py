"""Voice visit notes, monthly ANM report with incentives, and automatic ASHA alerts."""
from __future__ import annotations

from datetime import date, timedelta

from app.notifications.sms import outbox
from tests.conftest import asha_login, auth, create_passport, signup_mother

API = "/api/v1"


def test_visit_notes_fill_the_form(client):
    asha = auth(asha_login(client))
    r = client.post(f"{API}/visit/understand", headers=asha, json={
        "text": "BP 130 by 85, Hb 9.5, weight 58, chakkar aur pair mein sujan, bleeding nahi, IFA 30 di, "
                "khane ke baare me bataya, agli visit 3 din baad"}).json()
    f = r["fields"]
    assert f["bp"] == "130/85" and f["hemoglobin"] == 9.5 and f["weight_kg"] == 58
    assert f["symptoms"] == ["dizziness", "swelling_feet"] and f["denied"] == ["bleeding"]
    assert f["checklist"] == {"ifa_tablets_given": 30, "diet_counselling": True}
    assert f["next_visit_date"] == (date.today() + timedelta(days=3)).isoformat()
    assert "BP 130/85" in r["summary"] and "No bleeding" in r["summary"] and r["generated_by"] == "rules"
    hi = client.post(f"{API}/visit/understand", headers=asha, json={
        "text": "बीपी 140/90, सिर दर्द और धुंधला दिख रहा है, बुखार नहीं, आयरन की गोली नहीं ले रही", "language": "hi"}).json()["fields"]
    assert hi["bp"] == "140/90" and set(hi["symptoms"]) == {"headache", "blurred_vision"} and hi["denied"] == ["fever"]
    assert hi["taking_ifa"] is False


def test_visit_notes_model_fills_gaps_but_never_overrides_numbers(client):
    from app.ai import llm as llm_mod

    class Fake:
        available, model = True, "fake"

        def generate(self, messages, **kw):
            return '{"bp": "150/100", "hemoglobin": 7.0, "symptoms": ["tiredness", "nonsense"], "next_visit_in_days": 7}'

    llm_mod.set_llm(Fake())
    try:
        asha = auth(asha_login(client))
        r = client.post(f"{API}/visit/understand", headers=asha, json={"text": "BP 120/80, she says she feels very low on energy lately"}).json()
        f = r["fields"]
        assert f["bp"] == "120/80" and f["hemoglobin"] == 7.0 and f["symptoms"] == ["tiredness"]
        assert r["generated_by"] == "rules+ai"
    finally:
        llm_mod._client = None


def test_monthly_report_counts_incentives_and_share(client):
    asha_t = asha_login(client, reg="UP-BRL-04418", sc="SC-2208", sector="SEC-07")  # Kamla Yadav
    asha = auth(asha_t)
    reg = client.post(f"{API}/asha/mothers/register", headers=asha, json={
        "name": "Report Mother", "phone": "9876500011", "village": "Mohanpur", "pincode": "243001", "lmp": (date.today() - timedelta(weeks=20)).isoformat()})
    assert reg.status_code == 201, reg.text
    mid = reg.json()["mother_id"]
    client.post(f"{API}/visit/create", headers=asha, json={"mother_id": mid, "visit_type": "anc", "bp": "150/100", "hemoglobin": 9.0,
                                                          "checklist": {"ifa_tablets_given": 30, "diet_counselling": True}})
    r = client.get(f"{API}/asha/report", headers=asha).json()
    c = r["counts"]
    assert c["home_visits"] >= 1 and c["anc_visits"] >= 1 and c["ifa_tablets_given"] >= 30 and c["anaemia_found"] >= 1
    assert c["new_registrations"] >= 1 and c["high_risk_found"] >= 1 and any(m["name"] == "Report Mother" for m in r["high_risk"])
    assert {i["key"] for i in r["incentives"]} == {"jsy", "hbnc"} and "confirm" in r["incentive_note"]
    csv = client.get(f"{API}/asha/report?format=csv", headers=asha)
    assert csv.headers["content-type"].startswith("text/csv") and "IFA tablets given" in csv.text
    page = client.get(f"{API}/asha/report?format=html", headers=asha)
    assert "Monthly ASHA work report" in page.text and "Report Mother" in page.text
    s = client.post(f"{API}/asha/report/share", headers=asha).json()
    assert s["sent"] and outbox[-1]["to"].endswith("55501") and "home visits" in outbox[-1]["text"]
    assert client.get(f"{API}/asha/report?month=2026-13", headers=asha).status_code == 422


def test_automatic_alerts(client):
    from app.database.models import Visit
    from app.database.session import SessionLocal
    from app.notifications import reminders

    asha = auth(asha_login(client))  # Sunita
    token, _ = signup_mother(client, name="Alert Mother")
    mid = create_passport(client, token, week=24, name="Alert Mother")["mother_id"]
    client.post(f"{API}/assistant/chat", headers=auth(token), json={"message": "I fainted this morning"})
    with SessionLocal() as db:
        assert reminders.asha_morning_summary(db) >= 1
        db.commit()
    n = client.get(f"{API}/notifications?unread_only=true", headers=asha).json()
    top = next(i for i in n["items"] if i["title"].startswith("Today:"))
    assert "urgent" in top["title"] and "Alert" in top["body"]
    v = client.post(f"{API}/visit/create", headers=asha, json={"mother_id": mid, "bp": "118/76",
                                                              "next_visit_date": (date.today() + timedelta(days=1)).isoformat()})
    assert v.status_code == 201
    with SessionLocal() as db:
        assert reminders.visit_tomorrow_reminders(db) >= 1
        db.commit()
        assert reminders.visit_tomorrow_reminders(db) == 0  # once only
        assert db.get(Visit, __import__("uuid").UUID(v.json()["id"])).next_visit_date == date.today() + timedelta(days=1)
    mine = client.get(f"{API}/notifications", headers=auth(token)).json()["items"]
    assert any("tomorrow" in i["title"] for i in mine)
    with SessionLocal() as db:
        assert reminders.quiet_mother_alerts(db, today=date.today() + timedelta(days=10)) >= 1
