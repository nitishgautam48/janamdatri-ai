"""ASHA working area from GPS: unmatched mothers nearby are connected, and her mothers appear on the map."""
from __future__ import annotations

from tests.conftest import asha_login, auth, lmp_for_week, signup_mother

API = "/api/v1"
KOTA = (25.18, 75.83)


def test_mother_far_from_every_asha_is_connected_when_an_asha_sets_her_area(client, monkeypatch):
    from app.gis import osm

    monkeypatch.setattr(osm, "reverse", lambda lat, lon: None)
    token, _ = signup_mother(client, name="Kota Mother")
    # Passport with GPS in Kota: no ASHA within 25 km yet
    m = auth(token)
    r = client.post(f"{API}/passport/create", headers=m, json={
        "about": {"name": "Kota Mother", "age": 24, "village": "Talwandi", "pincode": "324005", "language": "en",
                  "latitude": KOTA[0] + 0.02, "longitude": KOTA[1] + 0.01},
        "pregnancy": {"lmp": lmp_for_week(26), "first_pregnancy": True}})
    assert r.status_code == 201, r.text
    r = client.get(f"{API}/asha/assigned", headers=m).json()
    assert r["assigned"] is None and "automatically" in r["message"]

    asha = auth(asha_login(client, reg="UP-BRL-04610", sc="SC-2230", sector="SEC-09"))  # Rekha Singh, seeded in Bareilly
    out = client.post(f"{API}/asha/me/location", json={"latitude": KOTA[0], "longitude": KOTA[1]}, headers=asha).json()
    assert out["area"]["has_location"] and out["newly_connected"] == 1 and out["names"] == ["Kota Mother"]
    assert out["area"]["village"] != "Vigyan Nagar"  # the old Bareilly names are not kept after moving to Kota

    r = client.get(f"{API}/asha/assigned", headers=m).json()
    assert r["assigned"]["name"] == "Rekha Singh"
    mp = client.get(f"{API}/asha/map", headers=asha).json()
    pin = next(x for x in mp["mothers"] if x["name"] == "Kota Mother")
    assert pin["latitude"] and pin["distance_km"] < 5 and pin["week"] == 26
    dash = client.get(f"{API}/asha/dashboard", headers=asha).json()
    assert dash["area"]["has_location"] and dash["counts"]["assigned_mothers"] >= 1


def test_location_must_be_valid(client):
    asha = auth(asha_login(client))
    assert client.post(f"{API}/asha/me/location", json={"latitude": 123, "longitude": 75}, headers=asha).status_code == 422
