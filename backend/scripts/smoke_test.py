"""End-to-end smoke test of the full workflow against a running server.

    python -m scripts.smoke_test http://localhost:8000 [http://localhost:8025]

OTPs are read from the Mailpit inbox (second argument), exactly as a person would read them.

Mother → Health Passport → Report OCR → Assessment → Health Assistant → Pregnancy Companion →
Emotional Companion → Assigned ASHA → Referral Summary → GIS Navigation → Emergency Escalation
"""
from __future__ import annotations

import io
import random
import sys
from datetime import date, timedelta

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/") + "/api/v1"
MAILPIT = (sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8025").rstrip("/")


def otp_from(resp: dict, phone_digits: str) -> str:
    """The OTP: echoed by the API in test mode, otherwise read from the Mailpit inbox."""
    if resp.get("dev_otp"):
        return resp["dev_otp"]
    import re
    import time

    for _ in range(20):
        r = httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f"to:{phone_digits}@", "limit": 1}, timeout=10)
        msgs = r.json().get("messages") or []
        m = re.search(r"\b(\d{6})\b", (msgs[0].get("Subject", "") + " " + msgs[0].get("Snippet", ""))) if msgs else None
        if m:
            return m.group(1)
        time.sleep(0.5)
    raise SystemExit("No OTP found in Mailpit")


def step(title: str) -> None:
    print(f"\n━━ {title}")


def cbc_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, line in enumerate(["City Diagnostics, Bareilly   Date: 07/10/2026", "COMPLETE BLOOD COUNT",
                              "Hemoglobin: 9.8 g/dL", "TLC: 8200 /cumm", "Platelet count: 2.4 lakh/cumm",
                              "Tab Ferrous + Folic acid 1 tab after lunch", "Tab Calcium 500 mg 2 tabs at night",
                              "Advice: repeat Hb after 4 weeks"]):
        c.drawString(50, 800 - i * 18, line)
    c.save()
    return buf.getvalue()


def main() -> None:
    c = httpx.Client(timeout=60)
    ok = lambda r: (r.raise_for_status(), r.json())[1]  # noqa: E731

    step("Guest mode")
    g = ok(c.post(f"{BASE}/auth/guest"))
    gh = {"Authorization": f"Bearer {g['access_token']}"}
    print("Week 24 baby size:", ok(c.get(f"{BASE}/guide/week?week=24", headers=gh))["baby"]["size"])

    step("Mother signs up with OTP")
    phone = f"9{random.randint(100000000, 999999999)}"
    otp = otp_from(ok(c.post(f"{BASE}/auth/otp/request", json={"phone": phone, "purpose": "mother_signup"})), "91" + phone)
    print("OTP read from the SMS inbox:", otp)
    t = ok(c.post(f"{BASE}/auth/mother/signup", json={"phone": phone, "otp": otp, "name": "Priya Kumari"}))
    h = {"Authorization": f"Bearer {t['access_token']}"}

    step("Health Passport (auto ASHA assignment)")
    lmp = (date.today() - timedelta(weeks=24, days=1)).isoformat()
    p = ok(c.post(f"{BASE}/passport/create", headers=h, json={
        "about": {"name": "Priya Kumari", "age": 23, "village": "Rampur", "pincode": "243001", "language": "en"},
        "pregnancy": {"lmp": lmp, "first_pregnancy": True},
        "medical_history": {"conditions": ["anemia"], "past_complications": False},
        "emergency_contacts": [{"name": "Ramesh", "relation": "husband", "phone": "9811122233"}]}))
    print(f"Week {p['pregnancy']['current_week']} · EDD {p['pregnancy']['edd']} · ASHA {p['assigned_asha']['name']} "
          f"(backup {p['backup_asha']['name']}) · facility {p['preferred_facility']['name']}")

    step("Upload CBC → OCR → findings")
    rep = ok(c.post(f"{BASE}/reports/upload", headers=h, data={"report_type": "cbc"},
                    files={"file": ("CBC.pdf", cbc_pdf(), "application/pdf")}))
    print([f"{f['label']}: {f['value']} ({f['flag']})" for f in rep["findings"] if f["kind"] == "lab_value"])

    step("Assessment")
    a = ok(c.post(f"{BASE}/assessment", headers=h, json={
        "vitals": {"bp_systolic": 118, "bp_diastolic": 76, "weight_kg": 58}, "symptoms": ["dizziness", "swelling_feet"],
        "history": {"taking_ifa": True, "bleeding_this_week": False, "baby_moving_normally": True}}))
    print("RESULT:", a["result"]["label"], "→", a["nearest_facility"]["name"], a["nearest_facility"]["display_meta"])
    for n in a["what_we_noticed"]:
        print("  •", n["text"])

    step("Health Assistant")
    r = ok(c.post(f"{BASE}/assistant/chat", headers=h, json={"message": "I feel dizzy since morning"}))
    print(r["reply"][:300], "…")

    step("Pregnancy Companion")
    plan = ok(c.get(f"{BASE}/companion/today", headers=h))
    print("Goals:", [g["title"] for g in plan["goals"]], "· tip:", plan["tip"])

    step("Emotional Companion")
    e = ok(c.post(f"{BASE}/emotional/chat", headers=h, json={"message": "Today was difficult. I feel lonely."}))
    print(e["reply"])

    step("ASHA worker logs in")
    v = ok(c.post(f"{BASE}/auth/asha/verify", json={"asha_reg_no": "UP-BRL-04417", "sub_centre_code": "SC-2208", "sector_code": "SEC-07"}))
    at = ok(c.post(f"{BASE}/auth/asha/login", json={"asha_reg_no": "UP-BRL-04417", "otp": otp_from(v, "919800044421")}))
    ah = {"Authorization": f"Bearer {at['access_token']}"}
    d = ok(c.get(f"{BASE}/asha/dashboard", headers=ah))
    print(d["greeting"], d["counts"])

    step("Referral summary")
    ref = ok(c.post(f"{BASE}/referral/generate", headers=ah, json={"mother_id": p["mother_id"]}))
    print(ref["referral_number"], ref["care_level"]["label"], "→", ref["facility"]["name"], "· share:", ref["share"]["url"])

    step("GIS severity routing")
    for lvl in ("routine_monitoring", "medical_review", "immediate_care"):
        f = ok(c.get(f"{BASE}/gis/recommendation?care_level={lvl}&pincode=243001&village=Rampur"))["facility"]
        print(f"  {lvl:20s} → {f['name']} ({f['display_meta']})")

    step("Emergency (no login)")
    ev = ok(c.post(f"{BASE}/emergency/alert", json={"pincode": "243001", "village": "Rampur", "emergency_type": "bleeding"}))
    for s in ev["live_status"]:
        print("  ✓" if s["done"] else "  …", s["text"])
    print("\nDone.")


if __name__ == "__main__":
    main()
