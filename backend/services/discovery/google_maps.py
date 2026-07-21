"""Google Maps/Places API Lead Discovery.

API: Google Places API (New) — Text Search (httpx, NOT a browser).
Free tier: $200/month credit (~40K calls). Spec: PRD §3A.

Text Search returns website/phone/address in one call via the field mask,
so no separate Place Details round-trip is needed. Only entries WITH a
website are returned — a lead we cannot audit is not a lead.
"""
import asyncio
import logging

import httpx

from services.discovery import normalize_domain

logger = logging.getLogger("trax9.discovery.google_maps")

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = ",".join(
    [
        "places.displayName",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.formattedAddress",
        "places.types",
        "places.addressComponents",
        "nextPageToken",
    ]
)
_PAGE_SIZE = 20
_PAGE_DELAY_SECONDS = 2.0
_REQUEST_TIMEOUT = 20.0
# Hard ceiling on pagination requests so a token loop can never spin forever
# (the API itself caps Text Search at 60 results / 3 pages today).
_MAX_PAGES = 10


def _extract_city_country(components: list[dict]) -> tuple[str, str]:
    """Pull city (type 'locality') and country (type 'country') from addressComponents."""
    city = ""
    country = ""
    for comp in components:
        types = comp.get("types") or []
        text = (comp.get("longText") or comp.get("shortText") or "").strip()
        if not text:
            continue
        if not city and "locality" in types:
            city = text
        elif not country and "country" in types:
            country = text
    return city, country


def _place_to_lead(place: dict) -> dict | None:
    """Map a Places API result to a lead dict; None when it has no website."""
    website = normalize_domain(place.get("websiteUri") or "")
    if not website:
        return None
    types = place.get("types") or []
    city, country = _extract_city_country(place.get("addressComponents") or [])
    company_name = ((place.get("displayName") or {}).get("text") or "").strip()
    return {
        "company_name": company_name or "Unknown",
        "website": website,
        "phone": (place.get("nationalPhoneNumber") or "").strip() or None,
        "address": (place.get("formattedAddress") or "").strip() or None,
        "city": city or None,
        "country": country or None,
        "category": types[0] if types else "",
        "source": "google_maps",
    }


async def search_places(
    keyword: str,
    location: str,
    radius: int = 50000,
    max_results: int = 60,
    api_key: str | None = None,
) -> list[dict]:
    """Discover businesses via Places Text Search for '{keyword} in {location}'.

    Paginates via nextPageToken (2s delay between pages, per API requirement)
    up to max_results. Deduplicates on normalized domain within the call.
    `radius` is kept for interface parity with the PRD; Text Search biases by
    the location embedded in the text query, so it is not sent to the API.

    Returns lead dicts: company_name, website, phone, address, city, country,
    category, source='google_maps'. Missing api_key -> [] (warn, no crash).
    Network/HTTP failures raise httpx errors for the calling task to catch.
    """
    if not api_key:
        logger.warning("search_places skipped: no Google Places API key configured")
        return []

    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": _FIELD_MASK,
        "Content-Type": "application/json",
    }
    text_query = f"{keyword} in {location}"
    body: dict = {"textQuery": text_query, "pageSize": _PAGE_SIZE}

    results: list[dict] = []
    seen_domains: set[str] = set()

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        for page in range(_MAX_PAGES):
            resp = await client.post(TEXT_SEARCH_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

            for place in data.get("places") or []:
                lead = _place_to_lead(place)
                if lead is None or lead["website"] in seen_domains:
                    continue
                seen_domains.add(lead["website"])
                results.append(lead)
                if len(results) >= max_results:
                    break

            token = data.get("nextPageToken")
            if not token or len(results) >= max_results:
                break
            body = {"textQuery": text_query, "pageSize": _PAGE_SIZE, "pageToken": token}
            await asyncio.sleep(_PAGE_DELAY_SECONDS)

    logger.info(
        "search_places '%s' in '%s': %d leads with websites", keyword, location, len(results)
    )
    return results
