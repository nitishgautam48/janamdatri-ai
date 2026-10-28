"""Real road distance, travel time and the route line to a facility.

Providers, in order: Azure Maps Route (``AZURE_MAPS_KEY``) → openrouteservice (``ORS_API_KEY``, free key) →
OSRM (free public demo router, no key) → the straight-line
estimate (haversine × road factor at ``RURAL_AVG_SPEED_KMPH``). Results are cached for a week, failures for five
minutes, so the map stays fast and inside the free limits. ``ROUTING_PROVIDER=off`` keeps the estimate only.
"""
from __future__ import annotations

import logging

import httpx

from app.core.cache import get_json, set_json
from app.core.config import settings

log = logging.getLogger(__name__)

TTL = 7 * 24 * 3600
FAIL_TTL = 300
MAX_POINTS = 400
TIMEOUT = 8.0


def provider() -> str:
    p = settings.routing_provider
    if p == "off":
        return "straight-line estimate"
    if p == "azure" or (p == "auto" and settings.azure_maps_key):
        return "azure"
    if p == "ors" or (p == "auto" and settings.ors_api_key):
        return "ors"
    return "osrm"


def _chain(prov: str) -> list[str]:
    """The provider, then the free ones after it, so a failing key still gives a road route."""
    order = ["azure", "ors", "osrm"]
    return [x for x in order[order.index(prov):] if x == prov or x == "osrm" or (x == "ors" and settings.ors_api_key)]


def _key(kind: str, prov: str, pts: list[tuple[float, float]]) -> str:
    return f"route:{kind}:{prov}:" + ";".join(f"{a:.4f},{b:.4f}" for a, b in pts)


def _thin(path: list[list[float]]) -> list[list[float]]:
    if len(path) <= MAX_POINTS:
        return path
    step = len(path) / MAX_POINTS
    return [path[int(i * step)] for i in range(MAX_POINTS)] + [path[-1]]


def _azure_route(a: tuple[float, float], b: tuple[float, float]) -> dict | None:
    r = httpx.get("https://atlas.microsoft.com/route/directions/json", timeout=TIMEOUT, params={
        "api-version": "1.0", "query": f"{a[0]},{a[1]}:{b[0]},{b[1]}", "travelMode": "car",
        "routeType": "fastest", "subscription-key": settings.azure_maps_key})
    r.raise_for_status()
    route = r.json()["routes"][0]
    s = route["summary"]
    path = [[p["latitude"], p["longitude"]] for leg in route.get("legs", []) for p in leg.get("points", [])]
    return {"distance_km": round(s["lengthInMeters"] / 1000, 1), "minutes": max(1, round(s["travelTimeInSeconds"] / 60)),
            "path": _thin(path), "provider": "azure"}


ORS = "https://api.openrouteservice.org/v2"


def _ors_route(a: tuple[float, float], b: tuple[float, float]) -> dict | None:
    r = httpx.post(f"{ORS}/directions/driving-car/geojson", timeout=TIMEOUT,
                   headers={"Authorization": settings.ors_api_key, "User-Agent": settings.osm_user_agent},
                   json={"coordinates": [[a[1], a[0]], [b[1], b[0]]]})
    r.raise_for_status()
    feat = r.json()["features"][0]
    s = feat["properties"]["summary"]
    path = [[lat, lon] for lon, lat in feat["geometry"]["coordinates"]]
    return {"distance_km": round(s.get("distance", 0) / 1000, 1), "minutes": max(1, round(s.get("duration", 0) / 60)),
            "path": _thin(path), "provider": "openrouteservice"}


def _ors_matrix(origin: tuple[float, float], dests: list[tuple[float, float]]) -> list[dict | None]:
    r = httpx.post(f"{ORS}/matrix/driving-car", timeout=TIMEOUT,
                   headers={"Authorization": settings.ors_api_key, "User-Agent": settings.osm_user_agent},
                   json={"locations": [[lon, lat] for lat, lon in [origin, *dests]], "sources": [0],
                         "destinations": list(range(1, len(dests) + 1)), "metrics": ["distance", "duration"]})
    r.raise_for_status()
    j = r.json()
    rows: list[dict | None] = []
    for dist, dur in zip(j["distances"][0], j["durations"][0], strict=False):
        rows.append({"distance_km": round(dist / 1000, 1), "minutes": max(1, round(dur / 60))}
                    if dist is not None and dur is not None else None)
    return rows


def _osrm_route(a: tuple[float, float], b: tuple[float, float]) -> dict | None:
    r = httpx.get(f"{settings.osrm_url.rstrip('/')}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}", timeout=TIMEOUT,
                  params={"overview": "full", "geometries": "geojson"}, headers={"User-Agent": settings.osm_user_agent})
    r.raise_for_status()
    j = r.json()
    if j.get("code") != "Ok" or not j.get("routes"):
        return None
    route = j["routes"][0]
    path = [[lat, lon] for lon, lat in route["geometry"]["coordinates"]]
    return {"distance_km": round(route["distance"] / 1000, 1), "minutes": max(1, round(route["duration"] / 60)),
            "path": _thin(path), "provider": "osrm"}


def route(a: tuple[float, float], b: tuple[float, float]) -> dict | None:
    """Road route from a to b: {distance_km, minutes, path [[lat, lon], ...], provider} or None."""
    prov = provider()
    if prov == "straight-line estimate":
        return None
    key = _key("one", prov, [a, b])
    cached = get_json(key)
    if cached:
        return None if cached.get("failed") else cached
    out = None
    for name in _chain(prov):
        try:
            out = {"azure": _azure_route, "ors": _ors_route, "osrm": _osrm_route}[name](a, b)
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.info("Road routing (%s) failed: %s", name, exc)
            out = None
        if out:
            break
    set_json(key, out or {"failed": True}, ex=TTL if out else FAIL_TTL)
    return out


def matrix(origin: tuple[float, float], dests: list[tuple[float, float]]) -> list[dict | None]:
    """Road distance and time from one origin to several facilities: [{distance_km, minutes}, ...]."""
    prov = provider()
    if prov == "straight-line estimate" or not dests:
        return [None] * len(dests)
    key = _key("table", prov, [origin, *dests])
    cached = get_json(key)
    if cached:
        return [None] * len(dests) if cached.get("failed") else cached["rows"]
    rows: list[dict | None] = [None] * len(dests)
    try:
        if prov == "ors":
            try:
                rows = _ors_matrix(origin, dests)
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                log.info("Road distance table (openrouteservice) failed, using OSRM: %s", exc)
                prov = "osrm"
        if prov == "azure":
            for i, d in enumerate(dests):
                one = route(origin, d)
                rows[i] = {"distance_km": one["distance_km"], "minutes": one["minutes"]} if one else None
        elif prov == "osrm":
            coords = ";".join(f"{lon},{lat}" for lat, lon in [origin, *dests])
            r = httpx.get(f"{settings.osrm_url.rstrip('/')}/table/v1/driving/{coords}", timeout=TIMEOUT,
                          params={"sources": "0", "annotations": "distance,duration"},
                          headers={"User-Agent": settings.osm_user_agent})
            r.raise_for_status()
            j = r.json()
            if j.get("code") == "Ok":
                for i in range(len(dests)):
                    dist, dur = j["distances"][0][i + 1], j["durations"][0][i + 1]
                    if dist is not None and dur is not None:
                        rows[i] = {"distance_km": round(dist / 1000, 1), "minutes": max(1, round(dur / 60))}
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.info("Road distance table (%s) failed: %s", prov, exc)
    ok = any(rows)
    set_json(key, {"rows": rows} if ok else {"failed": True}, ex=TTL if ok else FAIL_TTL)
    return rows
