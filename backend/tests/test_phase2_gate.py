"""Phase 2 gate: agency analyze -> auto keywords -> discover -> leads in DB.

All external HTTP mocked with respx. No live API calls.
"""
import json

import respx
from fastapi.testclient import TestClient
from httpx import Response

from main import app
from services.discovery import normalize_domain

client = TestClient(app)

EMAIL = "gate2@trax9.com"
PASSWORD = "supersecret2"

AGENCY_JSON = {
    "company_name": "Trax9",
    "services": [{"name": "Web Development", "description": "Custom sites"}],
    "ideal_client": {
        "industries": ["ecommerce - fashion"],
        "company_size": "small-medium",
        "geos": ["Texas"],
        "buying_signals": ["runs ads but poor SEO"],
    },
    "positioning": "Full-stack agency for SMBs",
    "suggested_keywords": ["clothing brand", "shopify store"],
    "suggested_locations": ["Houston", "Austin"],
}


def _auth() -> dict:
    r = client.post(
        "/api/register", json={"email": EMAIL, "password": PASSWORD, "name": "Gate2"}
    )
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": EMAIL, "password": PASSWORD})
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _put_keys(auth: dict) -> None:
    r = client.put(
        "/api/settings",
        headers=auth,
        json={
            "keys": {
                "anthropic": "sk-ant-fake-key-123456",
                "google_places": "places-fake-key-123",
                "google_custom_search": "cse-fake-key-123",
                "google_custom_search_cx": "cx-fake-123",
            },
            "ai_provider": "anthropic",
        },
    )
    assert r.status_code == 200


def test_normalize_domain():
    assert normalize_domain("https://www.Foo.com/bar/") == "foo.com"
    assert normalize_domain("foo.com") == "foo.com"
    assert normalize_domain("http://foo.com:8080/x") == "foo.com"
    assert normalize_domain("") == ""


@respx.mock
def test_profile_analyze_and_campaign_prefill():
    auth = _auth()
    _put_keys(auth)

    # Agency site pages
    respx.get(url__regex=r"https://trax9\.example.*").mock(
        return_value=Response(
            200,
            text="<html><body><nav><a href='/services'>Services</a></nav>"
            "<h1>Trax9 web dev agency</h1></body></html>",
        )
    )
    # Anthropic messages API
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(
            200,
            json={
                "content": [{"type": "text", "text": json.dumps(AGENCY_JSON)}],
                "usage": {"input_tokens": 500, "output_tokens": 300},
            },
        )
    )

    r = client.post(
        "/api/profile/analyze", headers=auth, json={"website": "https://trax9.example"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggested_keywords"] == ["clothing brand", "shopify store"]
    assert body["company_name"] == "Trax9"

    # GET /profile returns it
    assert client.get("/api/profile", headers=auth).status_code == 200

    # Campaign with NO keywords -> prefilled from profile
    r = client.post("/api/campaigns", headers=auth, json={"name": "First Campaign"})
    assert r.status_code in (200, 201), r.text
    camp = r.json()
    assert camp["seed_keywords"] == ["clothing brand", "shopify store"]
    assert camp["target_locations"] == ["Houston", "Austin"]


@respx.mock
def test_discover_inline_fallback_saves_and_dedupes():
    auth = _auth()
    _put_keys(auth)

    # One keyword x one location keeps the call fan-out small
    r = client.post(
        "/api/campaigns",
        headers=auth,
        json={
            "name": "Disco",
            "seed_keywords": ["clothing brand"],
            "target_locations": ["Houston"],
        },
    )
    camp_id = r.json()["id"]

    # Places returns two businesses, one duplicated by Search below
    respx.post("https://places.googleapis.com/v1/places:searchText").mock(
        return_value=Response(
            200,
            json={
                "places": [
                    {
                        "displayName": {"text": "Acme Threads"},
                        "websiteUri": "https://www.acmethreads.com/",
                        "nationalPhoneNumber": "555-0100",
                        "formattedAddress": "1 Main St, Houston, TX",
                        "types": ["clothing_store"],
                        "addressComponents": [
                            {"types": ["locality"], "longText": "Houston"},
                            {"types": ["country"], "longText": "United States"},
                        ],
                    },
                    {
                        "displayName": {"text": "No Website LLC"},
                        "formattedAddress": "2 Main St",
                        "types": ["store"],
                    },
                ]
            },
        )
    )
    respx.get(url__regex=r"https://www\.googleapis\.com/customsearch/v1.*").mock(
        return_value=Response(
            200,
            json={
                "items": [
                    {"title": "Acme Threads | Home", "link": "https://acmethreads.com/shop"},
                    {"title": "Bayou Wear - Official Site", "link": "https://bayouwear.com"},
                    {"title": "Facebook", "link": "https://facebook.com/acme"},
                ]
            },
        )
    )

    # Redis is down locally -> router falls back to inline discovery
    r = client.post(f"/api/campaigns/{camp_id}/discover", headers=auth)
    assert r.status_code in (200, 202), r.text

    r = client.get(f"/api/campaigns/{camp_id}/leads", headers=auth)
    assert r.status_code == 200
    leads = r.json()["items"]
    domains = sorted(l["website"] for l in leads)
    # acmethreads deduped across sources; facebook excluded; no-website dropped
    assert "acmethreads.com" in domains
    assert "bayouwear.com" in domains
    assert not any("facebook" in d for d in domains)
    assert len([d for d in domains if d == "acmethreads.com"]) == 1

    # Task row completed
    r = client.get(f"/api/campaigns/{camp_id}/tasks", headers=auth)
    assert r.status_code == 200
    tasks = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    assert any(t["status"] == "completed" for t in tasks)

    # Re-discover: dedup -> no duplicate rows
    client.post(f"/api/campaigns/{camp_id}/discover", headers=auth)
    r = client.get(f"/api/campaigns/{camp_id}/leads", headers=auth)
    again = sorted(l["website"] for l in r.json()["items"])
    assert again == domains


@respx.mock
def test_email_finder_scrape_tier():
    import asyncio

    from services.discovery.email_finder import find_email

    respx.get(url__regex=r"https://acmethreads\.com.*").mock(
        return_value=Response(
            200,
            text="<html><body>Reach us: <a href='mailto:info@acmethreads.com'>email</a>"
            " or spam@gmail.com</body></html>",
        )
    )
    result = asyncio.run(find_email("Acme Threads", "acmethreads.com"))
    assert result["email"] == "info@acmethreads.com"
    assert result["source"] == "scraped"
    assert result["confidence"] >= 85


def test_cross_tenant_isolation():
    auth = _auth()
    # Second tenant sees nothing of tenant 1
    r = client.post(
        "/api/register",
        json={"email": "other2@trax9.com", "password": "password123", "name": "Other"},
    )
    if r.status_code == 409:
        r = client.post(
            "/api/login", json={"email": "other2@trax9.com", "password": "password123"}
        )
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}

    mine = client.get("/api/campaigns", headers=auth).json()
    theirs = client.get("/api/campaigns", headers=other).json()
    assert theirs["total"] == 0
    if mine["items"]:
        foreign_id = mine["items"][0]["id"]
        assert client.get(f"/api/campaigns/{foreign_id}", headers=other).status_code == 404
