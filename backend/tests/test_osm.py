"""OpenStreetMap integration, tested with recorded responses (no internet needed)."""
from __future__ import annotations

import httpx
import pytest

from app.core.cache import get_store
from app.core.config import settings
from app.database.session import SessionLocal
from app.gis import osm, service

API = "/api/v1"

# A place outside the bundled Bareilly gazetteer (Varanasi).
LAT, LON = 25.3176, 82.9739
REVERSE = {"lat": str(LAT), "lon": str(LON), "display_name": "Lanka, Varanasi, Uttar Pradesh, 221005, India",
           "address": {"suburb": "Lanka", "city": "Varanasi", "state_district": "Varanasi", "state": "Uttar Pradesh",
                       "postcode": "221005", "country_code": "in"}}
SEARCH_PIN = [{"lat": "25.2820", "lon": "82.9563", "display_name": "221005, Varanasi",
               "address": {"postcode": "221005", "state_district": "Varanasi District", "state": "Uttar Pradesh"}}]
OVERPASS = {"elements": [
    {"type": "node", "id": 1, "lat": 25.2780, "lon": 82.9990,
     "tags": {"amenity": "hospital", "name": "Sir Sunderlal Hospital", "emergency": "yes", "phone": "+91 542 2309511"}},
    {"type": "way", "id": 2, "center": {"lat": 25.3300, "lon": 82.9800},
     "tags": {"amenity": "hospital", "name": "Shiv Prasad Gupta District Hospital"}},
    {"type": "node", "id": 3, "lat": 25.3250, "lon": 82.9700,
     "tags": {"amenity": "clinic", "name": "Primary Health Centre Chiraigaon", "opening_hours": "Mo-Sa 08:00-14:00"}},
    {"type": "node", "id": 4, "lat": 25.3000, "lon": 82.9600,
     "tags": {"amenity": "hospital", "name": "CHC Arajiline"}},
    {"type": "node", "id": 5, "lat": 25.3100, "lon": 82.9650, "tags": {"amenity": "clinic"}},  # unnamed: skipped
    {"type": "node", "id": 6, "lat": 25.3150, "lon": 82.9750, "tags": {"amenity": "pharmacy", "name": "Medicos"}},
]}
INDIA_POST = [{"Status": "Success", "PostOffice": [{"Name": "Chiraigaon", "District": "Varanasi",
                                                     "State": "Uttar Pradesh", "Block": "Chiraigaon"}]}]


class FakeClient:
    calls: list[str] = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _resp(self, url, body, status=200):
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    def get(self, url, params=None):
        FakeClient.calls.append(url)
        if url.endswith("/reverse"):
            return self._resp(url, REVERSE)
        if url.endswith("/search"):
            if (params or {}).get("postalcode") == "221005":
                return self._resp(url, SEARCH_PIN)
            if "Chiraigaon" in (params or {}).get("q", ""):
                return self._resp(url, [{"lat": "25.3400", "lon": "83.0900", "address": {"village": "Chiraigaon"}}])
            return self._resp(url, [])
        if "postalpincode" in url:
            return self._resp(url, INDIA_POST)
        return self._resp(url, {}, 404)

    def post(self, url, data=None, timeout=None):
        FakeClient.calls.append(url)
        return self._resp(url, OVERPASS)


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(settings, "osm_enabled", True)
    monkeypatch.setattr(osm, "_client", lambda: FakeClient())
    monkeypatch.setattr(osm, "_last_nominatim", 0.0)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    get_store().flush()
    FakeClient.calls = []
    yield


def test_classify_names_into_health_system_levels():
    assert osm.classify({"amenity": "hospital", "name": "District Women Hospital Bareilly"}) == "DH"
    assert osm.classify({"amenity": "clinic", "name": "CHC Bhojipura"}) == "CHC"
    assert osm.classify({"amenity": "clinic", "name": "प्राथमिक स्वास्थ्य केन्द्र रामपुर"}) == "PHC"
    assert osm.classify({"amenity": "clinic", "name": "Ayushman Arogya Mandir, Kesarpur"}) == "SC"
    assert osm.classify({"amenity": "hospital", "name": "City Care Hospital"}) == "HOSP"
    assert osm.classify({"amenity": "doctors", "name": "Dr Sharma Clinic"}) == "CLINIC"
    assert osm.classify({"amenity": "pharmacy", "name": "Medicos"}) is None


def test_gps_outside_gazetteer_is_reverse_geocoded(online, client):
    r = client.get(f"{API}/gis/geocode", params={"lat": LAT, "lon": LON})
    assert r.status_code == 200, r.text
    loc = r.json()
    assert loc["village"] == "Lanka" and loc["district"] == "Varanasi" and loc["pincode"] == "221005"
    assert loc["source"] == "gps"


def test_pincode_outside_gazetteer(online, client):
    loc = client.get(f"{API}/gis/geocode", params={"pincode": "221005"}).json()
    assert loc["district"] == "Varanasi" and abs(loc["latitude"] - 25.282) < 0.01


def test_pincode_falls_back_to_india_post(online):
    place = osm.geocode_pincode("221104")  # Nominatim has nothing for it; India Post knows the post office
    assert place and place.village == "Chiraigaon" and place.district == "Varanasi" and place.pincode == "221104"


def test_nearby_downloads_real_facilities_and_routes(online, client):
    r = client.get(f"{API}/gis/nearby", params={"lat": LAT, "lon": LON, "limit": 10})
    assert r.status_code == 200, r.text
    names = {f["name"]: f for f in r.json()["facilities"]}
    assert set(names) == {"Sir Sunderlal Hospital", "Shiv Prasad Gupta District Hospital",
                          "Primary Health Centre Chiraigaon", "CHC Arajiline"}
    assert names["Shiv Prasad Gupta District Hospital"]["type"] == "DH"
    assert names["Sir Sunderlal Hospital"]["type"] == "HOSP" and names["Sir Sunderlal Hospital"]["is_24x7"]
    assert names["Primary Health Centre Chiraigaon"]["source"] == "osm"
    overpass_calls = sum(1 for c in FakeClient.calls if "interpreter" in c)
    client.get(f"{API}/gis/nearby", params={"lat": LAT + 0.001, "lon": LON})
    assert sum(1 for c in FakeClient.calls if "interpreter" in c) == overpass_calls  # area cached
    rec = client.get(f"{API}/gis/recommendation", params={"lat": LAT, "lon": LON, "care_level": "medical_review"}).json()
    assert rec["facility"]["name"] == "CHC Arajiline"
    rec = client.get(f"{API}/gis/recommendation", params={"lat": LAT, "lon": LON, "care_level": "immediate_care"}).json()
    assert rec["facility"]["type"] in ("DH", "HOSP")
    with SessionLocal() as db:  # stored once, updated (not duplicated) on a forced refresh
        before = len(service.nearby_facilities(db, service.Location(LAT, LON, "gps"), limit=50))
        osm.load_area(db, LAT, LON, force=True)
        assert len(service.nearby_facilities(db, service.Location(LAT, LON, "gps"), limit=50)) == before


def test_offline_keeps_working(monkeypatch, client):
    class Down(FakeClient):
        def get(self, url, params=None):
            raise httpx.ConnectError("no internet")

        def post(self, url, data=None, timeout=None):
            raise httpx.ConnectError("no internet")

    monkeypatch.setattr(settings, "osm_enabled", True)
    monkeypatch.setattr(osm, "_client", lambda: Down())
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    r = client.get(f"{API}/gis/geocode", params={"lat": 19.07, "lon": 72.87})  # Mumbai, nothing bundled
    assert r.status_code == 200 and r.json()["latitude"] == 19.07  # coordinates kept, names unknown
    r = client.get(f"{API}/gis/nearby", params={"lat": 19.07, "lon": 72.87})
    assert r.status_code == 200 and r.json()["count"] == 0
    # the bundled district still works as before
    r = client.get(f"{API}/gis/nearby", params={"pincode": "243001"})
    assert r.json()["count"] >= 3
