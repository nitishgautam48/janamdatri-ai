"""Map pictures (tiles) served by our own server.

The browser asks /api/v1/gis/tiles/{z}/{x}/{y}.png; the server fetches the tile once from OpenStreetMap with an
identifying User-Agent (as the OSM tile policy requires), keeps it on disk for 30 days and serves the copy. If
OpenStreetMap does not answer, the next source in MAP_TILE_SOURCES is tried. No API key, no browser referer rules,
and the same address works on localhost and through the Cloudflare link.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

SOURCES = {
    "maptiler": "https://api.maptiler.com/maps/streets-v2/256/{z}/{x}/{y}.png?key={key}",  # needs MAPTILER_KEY
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "osmfr": "https://a.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",  # OpenStreetMap France, humanitarian style
    "esri": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
}
MAX_AGE = 30 * 86400
_down_until: dict[str, float] = {}
_lock = threading.Lock()


def valid(z: int, x: int, y: int) -> bool:
    return 0 <= z <= 19 and 0 <= x < 2 ** z and 0 <= y < 2 ** z


def _cache_path(z: int, x: int, y: int) -> Path:
    return settings.media_root / "tiles" / str(z) / str(x) / f"{y}.img"


def _fetch(name: str, z: int, x: int, y: int) -> bytes | None:
    url = SOURCES[name].format(z=z, x=x, y=y, key=settings.maptiler_key or "")
    try:
        r = httpx.get(url, timeout=settings.osm_timeout_seconds, headers={"User-Agent": settings.osm_user_agent})
    except httpx.HTTPError as exc:
        log.info("Map tiles from %s unavailable: %s", name, type(exc).__name__)  # never log the URL (it has the key)
        _down_until[name] = time.monotonic() + 60
        return None
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") and len(r.content) > 100:
        return r.content
    log.info("Map tiles from %s answered %s", name, r.status_code)
    if r.status_code in (401, 403, 429) or r.status_code >= 500:  # bad key / blocked: skip it for 10 minutes
        _down_until[name] = time.monotonic() + (600 if r.status_code in (401, 403) else 60)
    return None


def source() -> str:
    names = [n.strip() for n in settings.map_tile_sources.split(",") if n.strip() in SOURCES
             and (n.strip() != "maptiler" or settings.maptiler_key)]
    return names[0] if names else "none"


def media_type(data: bytes) -> str:
    return "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png"


def get(z: int, x: int, y: int) -> bytes | None:
    """The tile image, from the disk cache or the first source that answers; None if none does."""
    path = _cache_path(z, x, y)
    if path.exists() and time.time() - path.stat().st_mtime < MAX_AGE:
        return path.read_bytes()
    if not settings.osm_enabled:
        return path.read_bytes() if path.exists() else None
    names = [n.strip() for n in settings.map_tile_sources.split(",") if n.strip() in SOURCES
             and (n.strip() != "maptiler" or settings.maptiler_key)]
    for name in names:
        if _down_until.get(name, 0) > time.monotonic():
            continue
        data = _fetch(name, z, x, y)
        if data:
            with _lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)
            return data
    return path.read_bytes() if path.exists() else None  # an old copy is better than no map
