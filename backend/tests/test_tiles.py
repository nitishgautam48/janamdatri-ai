"""Map pictures come through our own server: fetched once (OSM first, then backups), cached, served to the app."""
from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.gis import tiles

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200


class FakeResponse:
    def __init__(self, status, content=b"", ctype="image/png"):
        self.status_code, self.content, self.headers = status, content, {"content-type": ctype}


@pytest.fixture
def online(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "osm_enabled", True)
    monkeypatch.setattr(settings, "media_root", tmp_path)
    tiles._down_until.clear()
    calls = []
    yield calls
    tiles._down_until.clear()


def test_tile_is_fetched_once_with_our_user_agent_and_cached(client, online, monkeypatch):
    def fake_get(url, timeout, headers):
        online.append((url, headers["User-Agent"]))
        return FakeResponse(200, PNG)
    monkeypatch.setattr(httpx, "get", fake_get)
    for _ in range(2):
        r = client.get("/api/v1/gis/tiles/12/2910/1751.png")
        assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert len(online) == 1 and online[0][0] == "https://tile.openstreetmap.org/12/2910/1751.png"
    assert online[0][1].startswith("Janamdatri-AI")
    assert "max-age" in r.headers["cache-control"]


def test_blocked_source_falls_back_to_the_next(client, online, monkeypatch):
    def fake_get(url, timeout, headers):
        online.append(url)
        if "openstreetmap.org" in url:
            return FakeResponse(403, b"Access blocked", "text/html")
        return FakeResponse(200, PNG)
    monkeypatch.setattr(httpx, "get", fake_get)
    assert client.get("/api/v1/gis/tiles/10/700/430.png").status_code == 200
    assert "openstreetmap.fr" in online[1]
    client.get("/api/v1/gis/tiles/10/701/430.png")
    assert not any("tile.openstreetmap.org/10/701" in u for u in online)  # a blocked source is skipped for a while


def test_no_source_and_bad_tiles(client, online, monkeypatch):
    def fake_get(url, timeout, headers):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx, "get", fake_get)
    assert client.get("/api/v1/gis/tiles/5/10/10.png").status_code == 503
    assert client.get("/api/v1/gis/tiles/3/9/1.png").status_code == 404  # x outside the 8×8 grid at zoom 3
    assert client.get("/api/v1/gis/tiles/20/1/1.png").status_code == 404


def test_map_config_points_at_our_tiles(client):
    cfg = client.get("/api/v1/gis/map-config").json()
    assert cfg["tile_url"] == "/api/v1/gis/tiles/{z}/{x}/{y}.png" and "OpenStreetMap" in cfg["attribution"]


def test_maptiler_key_is_used_first_and_stays_on_the_server(client, online, monkeypatch):
    monkeypatch.setattr(settings, "maptiler_key", "MT-secret")
    def fake_get(url, timeout, headers):
        online.append(url)
        return FakeResponse(200, PNG)
    monkeypatch.setattr(httpx, "get", fake_get)
    assert client.get("/api/v1/gis/tiles/8/180/110.png").status_code == 200
    assert online == ["https://api.maptiler.com/maps/streets-v2/256/8/180/110.png?key=MT-secret"]
    cfg = client.get("/api/v1/gis/map-config").json()
    assert cfg["tile_source"] == "maptiler" and "MapTiler" in cfg["attribution"] and "MT-secret" not in str(cfg)


def test_wrong_maptiler_key_falls_back_to_openstreetmap(client, online, monkeypatch):
    monkeypatch.setattr(settings, "maptiler_key", "wrong")
    def fake_get(url, timeout, headers):
        online.append(url)
        return FakeResponse(403, b'{"message":"Invalid key"}', "application/json") if "maptiler" in url else FakeResponse(200, PNG)
    monkeypatch.setattr(httpx, "get", fake_get)
    assert client.get("/api/v1/gis/tiles/8/181/110.png").status_code == 200
    assert "tile.openstreetmap.org" in online[-1]


def test_openrouteservice_routes_and_distances(client, monkeypatch):
    from app.gis import routing

    monkeypatch.setattr(settings, "routing_provider", "auto")
    monkeypatch.setattr(settings, "ors_api_key", "ORS-key")
    sent = []

    class R:
        def __init__(self, j): self._j = j
        def raise_for_status(self): pass
        def json(self): return self._j

    def fake_post(url, timeout, headers, json):
        sent.append((url, headers["Authorization"], json))
        if "matrix" in url:
            return R({"distances": [[8400.0, None]], "durations": [[1020.0, None]]})
        return R({"features": [{"properties": {"summary": {"distance": 8400.0, "duration": 1020.0}},
                                "geometry": {"coordinates": [[75.84, 25.18], [75.85, 25.19], [75.86, 25.2]]}}]})
    monkeypatch.setattr(routing.httpx, "post", fake_post)
    assert routing.provider() == "ors"
    one = routing.route((25.181, 75.841), (25.201, 75.861))
    assert one["distance_km"] == 8.4 and one["minutes"] == 17 and one["path"][0] == [25.18, 75.84]
    rows = routing.matrix((25.181, 75.841), [(25.2, 75.86), (25.3, 75.9)])
    assert rows[0] == {"distance_km": 8.4, "minutes": 17} and rows[1] is None
    assert sent[0][1] == "ORS-key" and sent[1][2]["sources"] == [0] and sent[1][2]["destinations"] == [1, 2]
