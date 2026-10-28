"""Smart daily work list: tasks with reasons, urgent first, shortest way, remind and done."""
from __future__ import annotations

import uuid

from app.notifications.sms import outbox
from tests.conftest import asha_login, auth, lmp_for_week, signup_mother

API = "/api/v1"
BASE = (26.90, 75.80)  # the ASHA's area (Jaipur; other tests use Kota)


def _mother(client, name, week, dlat, dlon, **preg):
    token, phone = signup_mother(client, name=name)
    h = auth(token)
    r = client.post(f"{API}/passport/create", headers=h, json={
        "about": {"name": name, "age": 25, "village": "Sanganer", "pincode": "302029", "language": "en",
                  "latitude": BASE[0] + dlat, "longitude": BASE[1] + dlon},
        "pregnancy": {"lmp": lmp_for_week(week), "first_pregnancy": True, **preg}})
    assert r.status_code == 201, r.text
    return h, r.json()["mother_id"]


def test_worklist_orders_urgent_first_then_nearest(client, monkeypatch):
    from app.gis import osm

    monkeypatch.setattr(osm, "reverse", lambda lat, lon: None)
    # Geeta Kumari moves her area to Jaipur; mothers there are connected to her
    asha = auth(asha_login(client, reg="UP-BRL-04502", sc="SC-2215", sector="SEC-08"))
    far_h, far_id = _mother(client, "Far Routine", 38, 0.06, 0.0)       # week 38 → birth preparedness (priority 4)
    near_h, near_id = _mother(client, "Near Routine", 37, 0.01, 0.0)    # week 37 → birth preparedness (priority 4)
    sick_h, sick_id = _mother(client, "Sick Mother", 30, 0.09, 0.0)     # danger sign told to the app → urgent
    assert client.post(f"{API}/asha/me/location", json={"latitude": BASE[0], "longitude": BASE[1]}, headers=asha).json()["newly_connected"] == 3
    client.post(f"{API}/assistant/chat", json={"message": "I have bleeding since morning"}, headers=sick_h)

    wl = client.get(f"{API}/asha/worklist", headers=asha).json()
    names = [i["name"] for i in wl["items"]]
    # urgent first; then from her house (farthest out) the nearest next stop is "Far", then "Near"
    assert names[:3] == ["Sick Mother", "Far Routine", "Near Routine"]
    sick = wl["items"][0]
    assert sick["priority"] == 1 and sick["tasks"][0]["kind"] == "danger_sign" and "bleeding" in sick["tasks"][0]["title"]
    kinds = [t["kind"] for t in wl["items"][1]["tasks"]]
    assert kinds[0] == "anc" and "birth_prep" in kinds  # no ANC yet at week 38 comes before birth preparedness
    assert 0 < wl["items"][1]["leg_km"] < wl["items"][0]["leg_km"]
    assert wl["summary"]["stops"] == 3 and wl["summary"]["urgent"] == 1 and wl["summary"]["total_km"] > 0

    # Remind: in-app message + SMS, her number hidden from the ASHA
    r = client.post(f"{API}/asha/worklist/{near_id}/remind", json={"kind": "birth_prep"}, headers=asha).json()
    assert r["sent"] and r["sms"] and "102" in r["text"] and outbox[-1]["text"] == r["text"]
    hist = client.get(f"{API}/messages/history", headers=near_h).json()
    assert any("102" in (m.get("body") or "") for m in hist["messages"])

    # Done moves her to the bottom; a recorded visit does the same
    client.post(f"{API}/asha/worklist/{near_id}/done", json={"done": True}, headers=asha)
    client.post(f"{API}/visit/create", json={"mother_id": sick_id, "bp": "118/76", "notes": "Sent to CHC"}, headers=asha)
    wl = client.get(f"{API}/asha/worklist", headers=asha).json()
    assert [i["name"] for i in wl["items"] if not i["done"]] == ["Far Routine"]
    assert [i["name"] for i in wl["items"]][-2:] in (["Near Routine", "Sick Mother"], ["Sick Mother", "Near Routine"])
    assert wl["summary"]["done"] == 2


def test_cannot_touch_other_ashas_mothers(client):
    asha = auth(asha_login(client))
    assert client.post(f"{API}/asha/worklist/{uuid.uuid4()}/remind", json={}, headers=asha).status_code == 403
