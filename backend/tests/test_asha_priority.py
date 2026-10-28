"""Priority engine: emergencies first (by score), then clinical risk and follow-up gaps; every point explained."""
from __future__ import annotations

from tests.conftest import auth, lmp_for_week, new_phone, signup_mother

API = "/api/v1"
BHOPAL = (23.25, 77.41)  # a fresh area for this test (others use Bareilly, Kota and Jaipur)


def _new_asha(client) -> dict:
    phone = new_phone()
    r = client.post(f"{API}/auth/asha/verify", json={"asha_reg_no": "MP-BPL-07001", "sub_centre_code": "SC-7001",
                                                     "sector_code": "SEC-70", "name": "Lata Verma", "phone": phone, "village": "Kolar"})
    assert r.status_code == 200, r.text
    t = client.post(f"{API}/auth/asha/login", json={"asha_reg_no": "MP-BPL-07001", "otp": r.json()["dev_otp"]}).json()
    return auth(t["access_token"])


def _mother(client, name, week, dlat, **about):
    token, _ = signup_mother(client, name=name)
    h = auth(token)
    r = client.post(f"{API}/passport/create", headers=h, json={
        "about": {"name": name, "age": about.pop("age", 26), "village": "Kolar", "pincode": "462042", "language": "en",
                  "latitude": BHOPAL[0] + dlat, "longitude": BHOPAL[1]}, "pregnancy": {"lmp": lmp_for_week(week)}, **about})
    assert r.status_code == 201, r.text
    return h, r.json()["mother_id"]


def test_priority_engine_orders_and_explains(client, monkeypatch):
    from app.gis import osm

    monkeypatch.setattr(osm, "reverse", lambda lat, lon: None)
    asha = _new_asha(client)
    sos_h, sos_id = _mother(client, "Sos Mother", 34, 0.08)              # farthest away, but SOS
    dang_h, dang_id = _mother(client, "Danger Mother", 30, 0.05)         # told the app about fits
    clin_h, clin_id = _mother(client, "Clinical Mother", 32, 0.01)        # severe BP + Hb found at a visit
    rout_h, rout_id = _mother(client, "Routine Mother", 20, 0.002, age=24)
    out = client.post(f"{API}/asha/me/location", json={"latitude": BHOPAL[0], "longitude": BHOPAL[1]}, headers=asha).json()
    assert out["newly_connected"] == 4

    client.post(f"{API}/emergency/alert", headers=sos_h, json={"latitude": BHOPAL[0] + 0.08, "longitude": BHOPAL[1], "emergency_type": "bleeding"})
    client.post(f"{API}/assistant/chat", headers=dang_h, json={"message": "I had fits this morning"})
    v = client.post(f"{API}/visit/create", headers=asha, json={"mother_id": clin_id, "bp": "165/112", "hemoglobin": 6.5})
    assert v.status_code == 201, v.text

    wl = client.get(f"{API}/asha/worklist", headers=asha).json()
    by = {i["name"]: i for i in wl["items"]}
    assert [i["name"] for i in wl["items"]][:2] == ["Sos Mother", "Danger Mother"]  # critical: highest score first
    assert by["Sos Mother"]["priority_score"] == 100 and by["Sos Mother"]["priority_band"] == "critical"
    assert by["Danger Mother"]["priority_score"] == 95 and "fits" in by["Danger Mother"]["priority_reasons"][0]["text"]
    clin = by["Clinical Mother"]
    texts = [r["text"] for r in clin["priority_reasons"]]
    assert clin["priority_band"] == "high" and 60 <= clin["priority_score"] <= 89
    assert "Severe high BP (165/112)" in texts and "Severe anaemia (Hb 6.5)" in texts
    assert not any("not yet followed up" in t for t in texts)  # her own visit produced the result: already followed up
    assert by["Routine Mother"]["priority_band"] == "routine" and by["Routine Mother"]["priority_score"] < 30
    assert wl["summary"]["bands"]["critical"] == 2 and clin["done"]  # visited today: done, not a remaining stop

    mothers = client.get(f"{API}/asha/mothers", headers=asha).json()["mothers"]
    assert [m["name"] for m in mothers] == ["Sos Mother", "Danger Mother", "Clinical Mother", "Routine Mother"]
    assert mothers[2]["priority_reasons"][0]["points"] == 40


def test_bands_and_cap():
    from app.asha.priority import band_for

    assert [band_for(s)[0] for s in (100, 90, 89, 60, 59, 30, 29, 0)] == \
        ["critical", "critical", "high", "high", "medium", "medium", "routine", "routine"]
