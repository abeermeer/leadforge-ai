"""Agency Profiling (Agency Brain) — PRD §3.0.

Input: the user's OWN agency website (e.g. trax9.com).
Output: structured understanding of what the agency sells and who it should target.
This drives (a) auto-generated discovery keywords and (b) email personalization
(matching an agency service to a lead's specific gap).

Run ONCE per agency, re-runnable. Cached (upserted) in agency_profiles.
"""
import re
import uuid
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from models import AgencyProfile
from services.ai.client import ai_json, record_ai_usage
from services.net.safe_http import UnsafeURLError, safe_get

FETCH_TIMEOUT_SECONDS = 15.0
MAX_INTERNAL_PAGES = 6
PAGE_CHAR_LIMIT = 2500

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Nav links worth following on an agency site (matched against the href).
_NAV_HREF_PATTERN = re.compile(
    r"services|about|portfolio|pricing|industries|work|contact", re.IGNORECASE
)

# PRD §11 "Agency Analysis (System Prompt)" — verbatim. {agency_content} is
# substituted with str.replace (NOT str.format — the JSON braces would break it).
AGENCY_ANALYSIS_PROMPT = """You are a B2B positioning analyst. Given the website content of a marketing/dev
agency, extract what they sell and who they should target for outreach.

Return ONLY valid JSON (no markdown, no fences):
{
  "company_name": string,
  "services": [{"name": string, "description": string}],
  "ideal_client": {
    "industries": [string],
    "company_size": string,
    "geos": [string],
    "buying_signals": [string]   // observable signs a business needs this agency
  },
  "positioning": string,
  "suggested_keywords": [string],   // 10-20 concrete search seeds to find such businesses
  "suggested_locations": [string]
}

Agency website content:
{agency_content}"""


def _normalize_url(url: str) -> str:
    """'trax9.com/' -> 'https://trax9.com'. Deterministic so upserts hit the same row."""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    return url.rstrip("/")


def _host(url: str) -> str:
    """Hostname without a leading 'www.' for same-site comparison."""
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _html_to_text(html: str) -> str:
    """Strip tags/scripts, collapse whitespace, trim to PAGE_CHAR_LIMIT chars."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    return text[:PAGE_CHAR_LIMIT]


def _internal_links(homepage_url: str, html: str) -> list[str]:
    """Absolute same-site URLs from nav-style anchors, deduped, order preserved."""
    home_host = _host(homepage_url)
    home_normalized = _normalize_url(homepage_url)
    soup = BeautifulSoup(html, "html.parser")

    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not _NAV_HREF_PATTERN.search(href):
            continue
        absolute = urljoin(f"{home_normalized}/", href).split("#")[0].rstrip("/")
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if _host(absolute) != home_host:
            continue
        if absolute == home_normalized or absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
        if len(links) >= MAX_INTERNAL_PAGES:
            break
    return links


async def fetch_agency_pages(url: str) -> dict[str, str]:
    """Fetch homepage + up to 6 internal pages (services/about/portfolio/...).

    Returns {url_or_label: text} with each page trimmed to PAGE_CHAR_LIMIT chars.
    Raises httpx errors if the homepage itself is unreachable; individual
    internal pages that fail are skipped.
    """
    homepage_url = _normalize_url(url)
    pages: dict[str, str] = {}

    # follow_redirects=False: safe_get follows + re-validates each hop itself (SSRF guard).
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=FETCH_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        response = await safe_get(homepage_url, client=client)
        response.raise_for_status()
        pages["homepage"] = _html_to_text(response.text)

        for link in _internal_links(homepage_url, response.text):
            try:
                page_response = await safe_get(link, client=client)
                page_response.raise_for_status()
            except (httpx.HTTPError, UnsafeURLError):
                continue  # partial content / blocked link is still fine to skip
            content_type = page_response.headers.get("content-type", "")
            if "html" not in content_type:
                continue
            text = _html_to_text(page_response.text)
            if text:
                pages[link] = text

    return pages


async def analyze_agency(
    url: str,
    user_id: uuid.UUID,
    db: Session,
    *,
    provider: str,
    api_key: str,
) -> AgencyProfile:
    """Scrape the agency site, run the Agency Analysis prompt, upsert agency_profiles.

    Raises httpx errors (site unreachable / AI API failure) and ValueError
    (bad provider / AI returned invalid JSON) — callers catch and map to HTTP.
    """
    pages = await fetch_agency_pages(url)
    combined = "\n\n".join(f"--- {label} ---\n{text}" for label, text in pages.items())

    prompt = AGENCY_ANALYSIS_PROMPT.replace("{agency_content}", combined)
    parsed, tokens = await ai_json(prompt, provider=provider, api_key=api_key)
    record_ai_usage(db, user_id, tokens)

    website = _normalize_url(url)
    row = (
        db.query(AgencyProfile)
        .filter(AgencyProfile.user_id == user_id, AgencyProfile.website == website)
        .first()
    )
    if row is None:
        row = AgencyProfile(user_id=user_id, website=website)
        db.add(row)

    company_name = parsed.get("company_name")
    row.company_name = company_name[:255] if isinstance(company_name, str) else None
    row.services = parsed.get("services")
    row.ideal_client = parsed.get("ideal_client")
    row.suggested_keywords = parsed.get("suggested_keywords")
    row.suggested_locations = parsed.get("suggested_locations")
    positioning = parsed.get("positioning")
    row.positioning = positioning if isinstance(positioning, str) else None
    row.raw_analysis = parsed

    db.commit()
    db.refresh(row)
    return row
