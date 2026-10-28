"""Links handed out by the app follow the address the visitor used (e.g. a Cloudflare Tunnel link)."""
from __future__ import annotations

from app.core import request_ctx
from app.core.config import settings
from tests.test_api_flows import priya, sunita  # noqa: F401  (fixtures)


def test_share_links_use_the_tunnel_address(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000")
    request_ctx.set_from_request({"x-forwarded-proto": "https", "x-forwarded-host": "calm-river-12.trycloudflare.com"},
                                 "http", "api:8000")
    assert request_ctx.public_base() == "https://calm-river-12.trycloudflare.com"
    request_ctx.set_from_request({}, "http", "localhost:8000")
    assert request_ctx.public_base() == "http://localhost:8000"


def test_a_configured_public_address_always_wins(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://janamdatri.example.org/")
    request_ctx.set_from_request({"x-forwarded-host": "other.trycloudflare.com"}, "https", None)
    assert request_ctx.public_base() == "https://janamdatri.example.org"


def test_referral_share_link_through_tunnel(client, priya, sunita):  # noqa: F811
    from tests.conftest import auth

    r = client.post("/api/v1/referral/generate", json={"mother_id": priya["mother_id"], "care_level": "medical_review",
                                                       "reason": "Low Hb, review"},
                    headers={**auth(sunita), "x-forwarded-proto": "https", "x-forwarded-host": "demo.trycloudflare.com"})
    assert r.status_code == 201, r.text
    assert r.json()["share"]["url"].startswith("https://demo.trycloudflare.com/api/v1/referral/shared/")
