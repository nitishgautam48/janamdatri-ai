"""End-to-end API flows following the product workflow."""
from __future__ import annotations

import json
import re

import pytest

from app.notifications.sms import outbox
from tests.conftest import (
    CBC_LINES,
    admin_login,
    asha_login,
    auth,
    create_passport,
    make_pdf,
    new_phone,
    signup_mother,
)

API = "/api/v1"
PCT = re.compile(r"\d\s*%|probabilit|confidence", re.I)


@pytest.fixture(scope="module")
def priya(client):
    token, phone = signup_mother(client)
    passport = create_passport(client, token, week=24, emergency_contacts=[{"name": "Ramesh", "relation": "husband", "phone": "9811122233"}])
    return {"token": token, "phone": phone, "mother_id": passport["mother_id"], "passport": passport}


@pytest.fixture(scope="module")
def sunita(client):
    return asha_login(client)


# --- system & auth ---------------------------------------------------------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["database"] is True


def test_mother_otp_flow_and_errors(client):
    phone = new_phone()
    r = client.post(f"{API}/auth/otp/request", json={"phone": phone, "purpose": "mother_signup"})
    otp = r.json()["dev_otp"]
    assert r.json()["phone_masked"].endswith(phone[-4:])
    bad = client.post(f"{API}/auth/mother/signup", json={"phone": phone, "otp": "000000" if otp != "000000" else "111111"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "otp_invalid"
    ok = client.post(f"{API}/auth/mother/signup", json={"phone": phone, "otp": otp, "name": "Rekha"})
    assert ok.status_code == 201 and ok.json()["is_new_user"] is True
    # Login with a fresh OTP; refresh rotates; logout revokes.
    otp2 = client.post(f"{API}/auth/otp/request", json={"phone": phone}).json()["dev_otp"]
    tokens = client.post(f"{API}/auth/mother/login", json={"phone": phone, "otp": otp2}).json()
    new = client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert new.status_code == 200
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    access = new.json()["access_token"]
    assert client.get(f"{API}/users/me", headers=auth(access)).status_code == 200
    client.post(f"{API}/auth/logout", json={}, headers=auth(access))
    assert client.get(f"{API}/users/me", headers=auth(access)).status_code == 401


def test_invalid_phone(client):
    r = client.post(f"{API}/auth/otp/request", json={"phone": "12345"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_phone"


def test_guest_mode(client):
    g = client.post(f"{API}/auth/guest").json()
    assert g["role"] == "guest" and "pregnancy_guide" in g["guest_features"]
    h = auth(g["access_token"])
    assert client.get(f"{API}/guide/week?week=20", headers=h).status_code == 200
    chat = client.post(f"{API}/assistant/chat", json={"message": "What should I eat for anaemia?"}, headers=h).json()
    assert chat["saved"] is False and chat["reply"]
    assert client.get(f"{API}/passport", headers=h).status_code == 403
    assert client.get(f"{API}/gis/nearby?pincode=243001", headers=h).status_code == 200


def test_family_phone_pin(client, priya):
    assert client.post(f"{API}/auth/pin/set", json={"pin": "4821"}, headers=auth(priya["token"])).status_code == 200
    r = client.post(f"{API}/auth/pin/login", json={"phone": priya["phone"], "pin": "4821"})
    assert r.status_code == 200
    assert client.post(f"{API}/auth/pin/login", json={"phone": priya["phone"], "pin": "0000"}).status_code == 401


def test_asha_verification(client):
    r = client.post(f"{API}/auth/asha/verify", json={"asha_reg_no": "UP-BRL-04417", "sub_centre_code": "SC-9999", "sector_code": "SEC-07"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "asha_mismatch"
    r = client.post(f"{API}/auth/asha/verify", json={"asha_reg_no": "UP-BRL-04417", "rch_id": "RCH-778812", "awc_code": "AWC-0912",
                                                     "sub_centre_code": "SC-2208", "sector_code": "SEC-07"})
    assert r.status_code == 200 and r.json()["in_registry"] and r.json()["phone_masked"].endswith("4421")
    login = client.post(f"{API}/auth/asha/login", json={"asha_reg_no": "UP-BRL-04417", "otp": r.json()["dev_otp"]}).json()
    assert login["user"]["verified_worker"] == "Sunita Devi · Rampur Sub Centre"
    # Prototype mock registry: well-formed new IDs are accepted after OTP.
    bad = client.post(f"{API}/auth/asha/verify", json={"asha_reg_no": "nonsense", "sub_centre_code": "x", "sector_code": "y"})
    assert bad.status_code == 422
    new = client.post(f"{API}/auth/asha/verify", json={"asha_reg_no": "UP-BRL-09001", "sub_centre_code": "SC-2208",
                                                       "sector_code": "SEC-07", "name": "Asha Test", "phone": "9822200011",
                                                       "village": "Kesarpur", "pincode": "243001"})
    assert new.status_code == 200 and new.json()["in_registry"] is False


def test_admin_login(client):
    assert client.post(f"{API}/auth/admin/login", json={"email": "admin@test.local", "password": "wrong-pass"}).status_code == 401
    admin_login(client)


# --- passport & ASHA mapping -----------------------------------------------
def test_passport_and_auto_asha(client, priya):
    p = priya["passport"]
    assert p["pregnancy"]["current_week"] == 24 and p["pregnancy"]["edd"]
    assert p["assigned_asha"]["name"] == "Sunita Devi"
    assert p["backup_asha"]["name"] == "Kamla Yadav"
    assert p["preferred_facility"]["name"] == "PHC Rampur"
    assert p["about"]["location"]["latitude"] is not None
    comp = client.get(f"{API}/passport/completion", headers=auth(priya["token"])).json()
    assert comp["profile_strength"] == 60 and comp["next_step"] == "medical_history"
    upd = client.put(f"{API}/passport/update", json={"medical_history": {"conditions": ["anemia"], "past_complications": False}},
                     headers=auth(priya["token"])).json()
    assert upd["medical_history"]["conditions"] == ["anemia"]
    assert upd["completion"]["profile_strength"] == 80
    assert client.post(f"{API}/passport/create", json={}, headers=auth(priya["token"])).status_code == 409


def test_assigned_asha_hides_phone(client, priya):
    r = client.get(f"{API}/asha/assigned", headers=auth(priya["token"])).json()
    assert r["assigned"]["name"] == "Sunita Devi" and r["assigned"]["phone"] == "hidden"
    assert "pincode 243001" in r["matched_by"] and "village" in r["matched_by"]
    assert "98000" not in json.dumps(r)


def test_passport_validation(client):
    token, _ = signup_mother(client, name="Test")
    r = client.post(f"{API}/passport/create", json={"pregnancy": {"lmp": "2999-01-01"}}, headers=auth(token))
    assert r.status_code == 422


# --- reports & OCR ----------------------------------------------------------
def test_report_upload_and_analysis(client, priya):
    pdf = make_pdf(CBC_LINES)
    r = client.post(f"{API}/reports/upload", headers=auth(priya["token"]), data={"report_type": "cbc"},
                    files={"file": ("CBC_7Oct.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "analyzed"
    f = {x["code"]: x for x in rep["findings"]}
    assert f["hemoglobin"]["value"].startswith("9.8") and f["hemoglobin"]["flag"] == "low"
    assert f["platelets"]["flag"] == "normal" and f["wbc"]["flag"] == "normal"
    assert "ifa" in f and "calcium" in f
    assert "Hemoglobin" in rep["explanation"]
    assert rep["report_date"] == "2026-10-07"
    trend = client.get(f"{API}/reports/trends?code=hemoglobin", headers=auth(priya["token"])).json()
    assert trend["points"][-1]["value"] == 9.8 and trend["normal_line"]["value"] == 11.0
    meds = client.get(f"{API}/reports/medications", headers=auth(priya["token"])).json()["medications"]
    assert {m["code"] for m in meds} >= {"ifa", "calcium"}
    assert client.get(f"{API}/reports/{rep['id']}/file", headers=auth(priya["token"])).status_code == 200
    comp = client.get(f"{API}/passport/completion", headers=auth(priya["token"])).json()
    assert comp["profile_strength"] == 100


def test_report_rejects_bad_files(client, priya):
    r = client.post(f"{API}/reports/upload", headers=auth(priya["token"]), data={"report_type": "cbc"},
                    files={"file": ("x.pdf", b"not a pdf at all", "application/pdf")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "file_type_mismatch"
    r = client.post(f"{API}/reports/upload", headers=auth(priya["token"]),
                    files={"file": ("x.exe", b"MZ....", "application/x-msdownload")})
    assert r.status_code == 422


def test_image_report_with_device_text(client, priya):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = client.post(f"{API}/reports/upload", headers=auth(priya["token"]),
                    data={"report_type": "prescription", "client_text": "Rx\nTab Albendazole 400 mg once\nAdvice: review after 2 weeks"},
                    files={"file": ("rx.png", png, "image/png")})
    assert r.status_code == 201
    codes = {f["code"] for f in r.json()["findings"]}
    assert "albendazole" in codes and "advice" in codes


# --- assessment -------------------------------------------------------------
def test_assessment_uses_report_and_never_shows_percentages(client, priya):
    body = {"vitals": {"bp_systolic": 118, "bp_diastolic": 76, "weight_kg": 58},
            "symptoms": ["dizziness", "swelling_feet"],
            "history": {"taking_ifa": True, "bleeding_this_week": False, "baby_moving_normally": True}}
    r = client.post(f"{API}/assessment", json=body, headers=auth(priya["token"]))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["result"]["key"] == "medical_review"
    assert out["result"]["label"] == "Medical Review Recommended"
    texts = [n["text"] for n in out["what_we_noticed"]]
    assert "Hemoglobin 9.8 g/dL, below normal" in texts and "Dizziness and swelling in feet" in texts and "BP 118/76, normal" in texts
    assert out["nearest_facility"]["name"] == "CHC Bhojipura"
    assert any("CHC Bhojipura" in rec["text"] for rec in out["recommendations"])
    assert out["inputs_used"]["hemoglobin_source"].startswith("report:")
    assert out["asha_informed"] is True
    assert not PCT.search(json.dumps(out, ensure_ascii=False))
    hist = client.get(f"{API}/assessment/history", headers=auth(priya["token"])).json()
    assert hist[0]["assessment_id"] == out["assessment_id"]


def test_immediate_care_alerts_asha_and_family(client):
    token, _ = signup_mother(client, name="Meena Kumari")
    create_passport(client, token, week=34, name="Meena Kumari", age=26,
                    emergency_contacts=[{"name": "Suresh", "phone": "9811100099"}])
    outbox.clear()
    r = client.post(f"{API}/assessment", json={"vitals": {"bp_systolic": 160, "bp_diastolic": 110},
                                               "symptoms": ["severe_headache", "blurred_vision"]}, headers=auth(token)).json()
    assert r["result"]["key"] == "immediate_care"
    assert r["nearest_facility"]["type"] == "DH" and r["nearest_facility"]["is_24x7"]
    assert any(m["to"].endswith("9811100099") for m in outbox)


# --- assistant & voice ------------------------------------------------------
def test_assistant_personalised_and_safe(client, priya):
    r = client.post(f"{API}/assistant/chat", json={"message": "I feel dizzy since morning"}, headers=auth(priya["token"])).json()
    assert "low hemoglobin (9.8)" in r["reply"]
    assert r["safety"]["emergency"] is False
    assert any(a["type"] == "tell_asha" for a in r["actions"])
    assert r["sources"]
    tid = r["thread_id"]
    r2 = client.post(f"{API}/assistant/chat", json={"message": "Explain my CBC report", "thread_id": tid}, headers=auth(priya["token"])).json()
    assert r2["generated_by"] == "report_analysis" and "Hemoglobin" in r2["reply"]
    hist = client.get(f"{API}/assistant/history", headers=auth(priya["token"])).json()
    assert hist["threads"][0]["title"] == "I feel dizzy since morning"
    msgs = client.get(f"{API}/assistant/history?thread_id={tid}", headers=auth(priya["token"])).json()["messages"]
    assert len(msgs) == 4
    assert client.post(f"{API}/assistant/tell-asha", json={"thread_id": tid}, headers=auth(priya["token"])).json()["sent"]


def test_assistant_danger_sign_hindi(client, priya, sunita):
    r = client.post(f"{API}/assistant/chat", json={"message": "मुझे खून आ रहा है और पेट में तेज़ दर्द है"},
                    headers=auth(priya["token"])).json()
    assert r["language"] == "hi" and r["safety"]["emergency"] and "108" in r["reply"]
    assert r["asha_notified"] is True and r["actions"][0]["type"] == "sos"
    notes = client.get(f"{API}/notifications", headers=auth(sunita)).json()
    assert any(n["title"] == "Danger sign reported" for n in notes["items"])


def test_voice_pipeline(client, priya, monkeypatch):
    monkeypatch.setenv("WHISPER_MOCK_TEXT", "Swelling in pregnancy")
    wav = b"RIFF" + b"\x00" * 40
    r = client.post(f"{API}/voice/transcribe", headers=auth(priya["token"]), files={"file": ("a.wav", wav, "audio/wav")})
    assert r.status_code == 200 and r.json()["text"] == "Swelling in pregnancy"
    r = client.post(f"{API}/assistant/voice", headers=auth(priya["token"]), files={"file": ("a.wav", wav, "audio/wav")}).json()
    assert r["transcript"]["text"] == "Swelling in pregnancy" and "swelling" in r["reply"].lower()


# --- knowledge & guide --------------------------------------------------------
def test_guide_and_knowledge(client, priya):
    g = client.get(f"{API}/guide/week", headers=auth(priya["token"])).json()
    assert g["week"] == 24 and g["baby"]["size"] == "corn cob (bhutta)"
    assert any("Glucose test due between week 24–28" == f["text"] for f in g["focus"])
    assert g["sections"]["schemes"][0]["title"].startswith("Pradhan Mantri Matru")
    q = client.post(f"{API}/knowledge/query", json={"question": "Which government scheme gives money for delivery in hospital?"}).json()
    assert q["results"] and q["results"][0]["category"] == "schemes"
    assert q["retrieval_backend"] in ("faiss", "numpy")
    q = client.post(f"{API}/knowledge/query", json={"question": "पैरों में सूजन", "categories": ["symptoms"]}).json()
    assert q["results"][0]["id"] == "sy-swelling"
    assert client.get(f"{API}/guide/topics?category=danger_signs").json()["items"]


# --- companion ----------------------------------------------------------------
def test_companion_plan(client, priya):
    h = auth(priya["token"])
    t = client.get(f"{API}/companion/today", headers=h).json()
    assert t["pregnancy_week"] == 24 and len(t["goals"]) == 5
    assert t["tip"] == "Take your iron tablet with lemon water, not tea."
    assert any(m["code"] == "ifa" for m in t["medications"]) and t["anc_preparation"]["bring"]
    assert any("Glucose test" in i["text"] for i in t["weekly_insights"])
    r = client.post(f"{API}/companion/hydration", json={"glasses": 3}, headers=h).json()
    assert r["hydration"]["count"] == 3
    for g in t["goals"]:
        r = client.patch(f"{API}/companion/today/tasks/{g['id']}", json={"done": True}, headers=h).json()
    assert r["progress"]["all_done"] and r["celebration"]["title"] == "Wonderful, Priya!"
    w = client.get(f"{API}/companion/week", headers=h).json()
    assert w["streak_days"] >= 1 and w["weekly_goal"]["completed_days"] >= 1
    regen = client.post(f"{API}/companion/plan", json={}, headers=h).json()
    assert regen["progress"]["all_done"]  # regenerating keeps what she already ticked


# --- emotional --------------------------------------------------------------
def test_emotional_companion(client, priya, sunita):
    h = auth(priya["token"])
    r = client.post(f"{API}/emotional/chat", json={"mood": "low"}, headers=h).json()
    assert "Priya" in r["reply"]
    r = client.post(f"{API}/emotional/chat", json={"message": "Today was difficult. I feel lonely.", "session_id": str(r["session_id"])},
                    headers=h).json()
    assert "lonely" in r["reply"].lower() and r["privacy"] == "Only you can see this conversation."
    assert not PCT.search(r["reply"])
    r = client.post(f"{API}/emotional/chat", json={"message": "Sometimes I feel I don't want to live", "session_id": str(r["session_id"])},
                    headers=h).json()
    assert "14416" in r["reply"] and r["asha_check_in_requested"] is True
    notes = client.get(f"{API}/notifications", headers=auth(sunita)).json()["items"]
    nudge = next(n for n in notes if n["type"] == "wellbeing")
    assert "lonely" not in nudge["body"] and "live" not in nudge["body"]  # content stays private
    hist = client.get(f"{API}/emotional/history", headers=h).json()
    assert hist["sessions"] and hist["trend"] in ("heavier", "steady", "brighter")
    assert client.get(f"{API}/emotional/history", headers=auth(sunita)).status_code == 403


# --- messaging ----------------------------------------------------------------
def test_mother_asha_messaging(client, priya, sunita):
    hm, ha = auth(priya["token"]), auth(sunita)
    r = client.post(f"{API}/messages/send", json={"body": "Didi, ANC kab hai?"}, headers=hm)
    assert r.status_code == 201
    client.post(f"{API}/messages/send", json={"quick_action": "request_home_visit"}, headers=hm)
    client.post(f"{API}/messages/location", json={"latitude": 28.262, "longitude": 79.35}, headers=hm)
    img = client.post(f"{API}/messages/image", headers=hm, files={"file": ("p.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, "image/png")})
    assert img.status_code == 201
    voice = client.post(f"{API}/messages/voice", headers=ha, data={"mother_id": priya["mother_id"], "duration_seconds": "14"},
                        files={"file": ("v.ogg", b"OggS" + b"\x00" * 30, "audio/ogg")})
    assert voice.status_code == 201 and voice.json()["duration_seconds"] == 14
    inbox = client.get(f"{API}/messages/conversations", headers=ha).json()["conversations"]
    assert inbox[0]["mother_id"] == priya["mother_id"]
    hist = client.get(f"{API}/messages/history?mother_id={priya['mother_id']}", headers=ha).json()
    assert len(hist["messages"]) >= 5 and hist["counterpart"]["phone"] == "hidden"
    assert any(m["type"] == "location" and m["location"]["maps_url"] for m in hist["messages"])
    att = next(m for m in hist["messages"] if m["type"] == "image")["attachment_url"]
    assert client.get(att.replace("/api/v1", API), headers=ha).status_code == 200
    blob = json.dumps(client.get(f"{API}/messages/history", headers=hm).json())
    assert "+9198" not in blob and priya["phone"] not in blob
    call = client.post(f"{API}/messages/call", json={}, headers=hm).json()
    assert call["relay_number"] and len(call["code"]) == 6


def test_other_asha_cannot_access(client, priya):
    geeta = asha_login(client, "UP-BRL-04502", "SC-2215", "SEC-08")
    h = auth(geeta)
    assert client.get(f"{API}/passport?mother_id={priya['mother_id']}", headers=h).status_code == 403
    assert client.get(f"{API}/messages/history?mother_id={priya['mother_id']}", headers=h).status_code == 403
    assert client.get(f"{API}/assessment/history?mother_id={priya['mother_id']}", headers=h).status_code == 403


# --- referral & GIS -------------------------------------------------------------
def test_gis(client):
    r = client.get(f"{API}/gis/nearby?pincode=243001&village=Rampur").json()
    names = [f["name"] for f in r["facilities"]]
    assert names[0] == "Rampur Sub Centre" and "PHC Rampur" in names
    assert r["facilities"][1]["directions_url"].startswith("https://www.google.com/maps/dir/")
    rec = lambda lvl: client.get(f"{API}/gis/recommendation?care_level={lvl}&lat=28.262&lon=79.35").json()["facility"]  # noqa: E731
    assert rec("routine_monitoring")["name"] == "PHC Rampur"
    assert rec("medical_review")["name"] == "CHC Bhojipura"
    assert rec("immediate_care")["type"] == "DH"
    assert client.get(f"{API}/gis/facilities?type=CHC").json()["count"] >= 3
    assert client.get(f"{API}/gis/geocode?pincode=243202").json()["village"] == "Bhojipura"
    assert client.get(f"{API}/gis/nearby").status_code == 422


def test_referral_by_asha(client, priya, sunita):
    r = client.post(f"{API}/referral/generate", json={"mother_id": priya["mother_id"],
                                                      "reason": "Low Hb (9.8), dizziness, pedal edema. Doctor review for anemia."},
                    headers=auth(sunita))
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["care_level"]["key"] == "medical_review" and ref["facility"]["name"] == "CHC Bhojipura"
    assert ref["patient"]["name"] == "Priya Kumari" and ref["pregnancy_week"] == 24
    assert ref["patient"]["asha"]["sub_centre_code"] == "SC-2208" and ref["status"] == "sent"
    assert ref["facility_notified"] is True
    pdf = client.get(f"{API}/referral/{ref['id']}/pdf", headers=auth(priya["token"]))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    html = client.get(f"{API}/referral/{ref['id']}/print", headers=auth(priya["token"]))
    assert "Referral Summary" in html.text
    token = ref["share"]["url"].rsplit("/", 1)[1]
    shared = client.get(f"{API}/referral/shared/{token}")
    assert shared.status_code == 200 and "share" not in shared.json()
    notes = client.get(f"{API}/notifications", headers=auth(priya["token"])).json()["items"]
    assert any(n["type"] == "referral_update" for n in notes)
    hist = client.get(f"{API}/referral/history", headers=auth(priya["token"])).json()
    assert hist["count"] >= 1
    assert client.patch(f"{API}/referral/{ref['id']}/status", json={"status": "acknowledged"},
                        headers=auth(priya["token"])).status_code == 200


# --- emergency ----------------------------------------------------------------
def test_emergency_without_login(client, sunita):
    outbox.clear()
    r = client.post(f"{API}/emergency/alert", json={"pincode": "243001", "village": "Rampur", "emergency_type": "bleeding"})
    assert r.status_code == 201, r.text
    ev = r.json()
    chain = {s["key"]: s for s in ev["escalation_chain"]}
    assert chain["assigned_asha"]["name"] == "Sunita Devi" and chain["assigned_asha"]["status"] == "notified"
    assert chain["backup_asha"]["name"] == "Kamla Yadav" and chain["emergency_services"]["status"] == "call_now"
    assert ev["nearest_facility"]["type"] == "DH"
    assert [n["number"] for n in ev["numbers"]][:3] == ["108", "102", "14416"]
    assert ev["live_status"][0]["text"].startswith("Location found")
    upd = client.post(f"{API}/emergency/location", json={"event_id": ev["id"], "public_token": ev["public_token"],
                                                         "latitude": 28.27, "longitude": 79.36})
    assert upd.status_code == 200
    assert client.post(f"{API}/emergency/location", json={"event_id": ev["id"], "latitude": 1, "longitude": 1}).status_code == 403
    dash = client.get(f"{API}/asha/dashboard", headers=auth(sunita)).json()
    assert dash["counts"]["emergencies"] >= 1
    ack = client.post(f"{API}/emergency/{ev['id']}/acknowledge", headers=auth(sunita)).json()
    assert ack["status"] == "acknowledged"
    assert next(s for s in ack["live_status"] if s["key"] == "asha")["text"] == "ASHA Sunita is responding"
    fac = client.get(f"{API}/emergency/facilities?pincode=243001").json()
    assert fac["facilities"][0]["has_emergency"]


def test_emergency_escalation_chain(client, priya):
    from app.database.models import EmergencyEvent
    from app.database.session import SessionLocal
    from app.emergency.service import escalate

    ev = client.post(f"{API}/emergency/alert", json={"emergency_type": "convulsions"}, headers=auth(priya["token"])).json()
    assert ev["location"]["text"].startswith("Rampur")  # from her passport
    with SessionLocal() as db:
        e = db.get(EmergencyEvent, __import__("uuid").UUID(ev["id"]))
        steps = []
        while escalate(db, e):
            steps.append(e.escalation_chain[e.escalation_level]["key"])
        db.commit()
    assert steps == ["backup_asha", "anm", "nearest_facility", "emergency_services"]


# --- ASHA tools ---------------------------------------------------------------
def test_asha_register_visit_dashboard(client, sunita):
    h = auth(sunita)
    phone = new_phone()
    r = client.post(f"{API}/asha/mothers/register", json={"name": "Rekha Yadav", "age": 21, "phone": phone, "village": "Rampur",
                                                          "pincode": "243001", "lmp": __import__("tests.conftest", fromlist=["x"]).lmp_for_week(16)},
                    headers=h)
    assert r.status_code == 201, r.text
    mid = r.json()["mother_id"]
    v = client.post(f"{API}/visit/create", json={"mother_id": mid, "bp": "118/76", "weight_kg": 50, "hemoglobin": 9.6,
                                                 "symptoms": ["dizziness"], "checklist": {"ifa_tablets_given": 30},
                                                 "notes": "Feeling dizzy. Advised CHC review on Friday."}, headers=h)
    assert v.status_code == 201, v.text
    assert v.json()["assessment"]["result"]["key"] == "medical_review" and v.json()["next_visit_date"]
    assert client.get(f"{API}/visit/history?mother_id={mid}", headers=h).json()["count"] == 1
    dash = client.get(f"{API}/asha/dashboard", headers=h).json()
    assert dash["counts"]["assigned_mothers"] >= 2 and dash["greeting"].startswith("नमस्ते, Sunita")
    assert any(row["subtitle"].startswith("Medical review · low Hb") for row in dash["todays_visits"])
    lst = client.get(f"{API}/asha/mothers?filter=follow_up", headers=h).json()
    assert lst["count"] >= 1
    detail = client.get(f"{API}/asha/mothers/{mid}", headers=h).json()
    assert detail["passport"]["about"]["name"] == "Rekha Yadav"
    # The mother can later claim her account with OTP.
    otp = client.post(f"{API}/auth/otp/request", json={"phone": phone, "purpose": "mother_signup"}).json()["dev_otp"]
    claim = client.post(f"{API}/auth/mother/signup", json={"phone": phone, "otp": otp})
    assert claim.status_code == 201 and claim.json()["is_new_user"] is False
    dup = client.post(f"{API}/asha/mothers/register", json={"name": "X", "phone": phone}, headers=h)
    assert dup.status_code == 409


# --- analytics & admin --------------------------------------------------------
def test_analytics(client, priya, sunita):
    admin = auth(admin_login(client))
    o = client.get(f"{API}/analytics/overview", headers=admin).json()
    assert o["total_mothers"] >= 3 and o["anemia"]["moderate"] >= 1
    assert sum(o["assessment_categories"]["counts"].values()) >= 2
    assert o["users_by_role"]["asha"] >= 2
    assert client.get(f"{API}/analytics/anemia-trends", headers=admin).json()["series"]
    assert client.get(f"{API}/analytics/emergencies", headers=admin).json()["total"] >= 1
    assert client.post(f"{API}/analytics/snapshot", headers=admin).status_code == 200
    mine = client.get(f"{API}/analytics/overview", headers=auth(sunita)).json()
    assert mine["scope"]["asha_id"] and "users_by_role" not in mine
    assert client.get(f"{API}/analytics/overview", headers=auth(priya["token"])).status_code == 403
    users = client.get(f"{API}/users?role=asha", headers=admin).json()["users"]
    assert users and all("•" in (u["phone"] or "•") for u in users)


def test_reminders(client, priya):
    from app.database.session import SessionLocal
    from app.notifications.reminders import anc_reminders, daily_wellness_reminders

    with SessionLocal() as db:
        n1 = daily_wellness_reminders(db, slot="midday")
        n2 = daily_wellness_reminders(db, slot="midday")  # idempotent
        anc_reminders(db)
        db.commit()
    assert n1 >= 1 and n2 == 0
    notes = client.get(f"{API}/notifications?unread_only=true", headers=auth(priya["token"])).json()
    assert any(n["type"] == "medication" for n in notes["items"])
    assert client.post(f"{API}/notifications/read-all", headers=auth(priya["token"])).json()["updated"] >= 1


def test_profile_and_erasure(client):
    token, phone = signup_mother(client, name="Delete Me")
    create_passport(client, token, week=10, name="Delete Me")
    me = client.get(f"{API}/users/me", headers=auth(token)).json()
    assert me["profile_strength"] == 40 and me["preferred_facility"] == "PHC Rampur"
    client.patch(f"{API}/users/me", json={"language": "hi", "voice_read_aloud": False}, headers=auth(token))
    assert client.get(f"{API}/users/me", headers=auth(token)).json()["language"] == "hi"
    assert client.delete(f"{API}/users/me", headers=auth(token)).status_code == 204
    assert client.get(f"{API}/users/me", headers=auth(token)).status_code == 401


def test_otp_request_fails_clearly_without_mailpit(client, monkeypatch):
    import smtplib

    from app.core.config import settings

    def refuse(*a, **k):
        raise ConnectionRefusedError("down")

    monkeypatch.setattr(settings, "sms_channel", "mailpit")
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    r = client.post(f"{API}/auth/otp/request", json={"phone": new_phone(), "purpose": "mother_signup"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "sms_undeliverable"


# --- role checks on the mother OTP and honest profile strength ---------------
def test_mother_login_otp_role_checks(client):
    r = client.post(f"{API}/auth/otp/request", json={"phone": new_phone(), "purpose": "mother_login"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "account_not_found"
    # an ASHA worker's registered number (seed: Sunita Devi) is sent to ASHA login, for login and signup
    for purpose in ("mother_login", "mother_signup"):
        r = client.post(f"{API}/auth/otp/request", json={"phone": "9800044421", "purpose": purpose})
        assert r.status_code == 409 and r.json()["error"]["code"] == "asha_account"


def test_skipped_steps_do_not_count_as_filled(client):
    token, _ = signup_mother(client, name="Skip Test")
    p = client.post(f"{API}/passport/create", headers=auth(token), json={
        "about": {"name": "Skip Test", "age": 22, "pincode": "243001"},
        "skip_steps": ["medical_history", "reports", "emergency_contacts"]}).json()
    comp = p["completion"]
    assert comp["profile_strength"] == 20
    assert [s["key"] for s in comp["steps"] if s["skipped"]] == ["medical_history", "reports", "emergency_contacts"]
    assert comp["next_step"] == "pregnancy_details"
