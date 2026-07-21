"""Brand R&D audit — fetch the lead's key pages, run the Brand Analysis prompt (PRD 3B).

Fetches the homepage plus about / services / products pages (3-5 pages total),
strips them to plain text, and sends the combined content through ai_json with
the Brand Analysis prompt from PRD section 11 (copied verbatim; the
{website_content} placeholder is substituted via str.replace, NOT str.format,
because the JSON braces in the prompt would break format()).

Resilience contract: individual internal pages that fail are skipped; only a
total failure (no page fetched at all) or an AI failure raises. Token counts
are returned to the caller, which records them via record_ai_usage() so
usage_counters.ai_tokens stays accurate.
"""
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from services.ai.client import ai_json
from services.discovery import normalize_domain

logger = logging.getLogger("trax9.audit.brand_rnd")

FETCH_TIMEOUT_SECONDS = 15.0
PAGE_CHAR_LIMIT = 2000
# Homepage + up to this many internal pages = 3-5 pages total (PRD 3B).
MAX_INTERNAL_PAGES = 4

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Nav links worth following on a lead's site (matched against href and anchor text).
_NAV_PATTERN = re.compile(
    r"about|services|products|what-we-do|our-work|company|solutions|menu",
    re.IGNORECASE,
)

# Guessed paths used when the homepage yields no matching nav links (or failed).
_FALLBACK_PATHS = ("/about", "/about-us", "/services", "/products")

# PRD section 11 "Brand Analysis (System Prompt)" — verbatim.
BRAND_ANALYSIS_PROMPT = """You are a business analyst. Analyze the following business based on their
website content. Be specific and data-driven.

Extract and return JSON ONLY (no other text) with these EXACT fields:
- industry: string (e.g. "ecommerce - fashion", "local services - restaurant")
- target_audience: string (who they seem to be selling to)
- brand_positioning: string (what makes them unique, their tone)
- website_quality_score: integer (1-10)
- strengths: array of strings (3 things they do well online)
- weaknesses: array of strings (3 things they could improve)
- estimated_size: string ("small" / "medium" / "large enterprise")
- pain_points: array of strings (problems they likely have that Trax9 could solve)
- best_services: array of strings (which Trax9 services would help them most)
- competitor_notes: string (any visible competitors mentioned)

Website Content:
{website_content}

Return ONLY valid JSON. No markdown, no code fences."""


def _normalize_url(url: str) -> str:
    """'foo.com/' -> 'https://foo.com'. Adds a scheme when missing."""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    return url.rstrip("/")


def _html_to_text(html: str) -> str:
    """Strip tags/scripts, collapse whitespace, trim to PAGE_CHAR_LIMIT chars."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    return text[:PAGE_CHAR_LIMIT]


def _internal_links(homepage_url: str, html: str) -> list[str]:
    """Absolute same-site about/services-style URLs, deduped, order preserved."""
    home_domain = normalize_domain(homepage_url)
    home_normalized = _normalize_url(homepage_url)
    soup = BeautifulSoup(html, "html.parser")

    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        text = anchor.get_text(" ", strip=True)
        if not href or not (_NAV_PATTERN.search(href) or _NAV_PATTERN.search(text)):
            continue
        absolute = urljoin(f"{home_normalized}/", href).split("#")[0].rstrip("/")
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if normalize_domain(absolute) != home_domain:
            continue
        if absolute == home_normalized or absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
        if len(links) >= MAX_INTERNAL_PAGES:
            break
    return links


async def _fetch_brand_pages(base_url: str) -> dict[str, str]:
    """Fetch homepage + about/services pages -> {label_or_url: text}.

    Individual page failures are skipped; an empty dict means total failure
    (the caller raises).
    """
    homepage_url = _normalize_url(base_url)
    pages: dict[str, str] = {}
    homepage_html = ""

    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        try:
            response = await client.get(homepage_url)
            response.raise_for_status()
            homepage_html = response.text
            text = _html_to_text(homepage_html)
            if text:
                pages["homepage"] = text
        except httpx.HTTPError:
            logger.warning("analyze_brand: homepage fetch failed for %s", homepage_url, exc_info=True)

        if homepage_html:
            candidates = _internal_links(homepage_url, homepage_html)
        else:
            candidates = []
        if not candidates:
            candidates = [f"{homepage_url}{path}" for path in _FALLBACK_PATHS]

        for link in candidates[:MAX_INTERNAL_PAGES]:
            try:
                page_response = await client.get(link)
                page_response.raise_for_status()
            except httpx.HTTPError:
                continue  # partial content is still useful
            if "html" not in page_response.headers.get("content-type", ""):
                continue
            text = _html_to_text(page_response.text)
            if text:
                pages[link] = text

    return pages


async def analyze_brand(
    url: str,
    audit_data: dict,
    *,
    provider: str,
    api_key: str,
) -> tuple[dict, int]:
    """Run the Brand Analysis prompt over the lead's homepage + about/services pages.

    1. Resolve the base URL — prefers audit_data['website']['fetched_url'] (the
       post-redirect URL the website audit actually loaded) over the raw url.
    2. Fetch homepage plus up to MAX_INTERNAL_PAGES about/services-style pages
       (15s timeout each, failures skipped), each trimmed to PAGE_CHAR_LIMIT chars.
    3. Substitute the combined text into BRAND_ANALYSIS_PROMPT and call ai_json.

    Returns (parsed_dict, total_tokens). The parsed dict carries the PRD fields:
    industry, target_audience, brand_positioning, website_quality_score,
    strengths, weaknesses, estimated_size, pain_points, best_services,
    competitor_notes. The caller records total_tokens via record_ai_usage().

    Raises RuntimeError when no page could be fetched at all, and ValueError /
    httpx errors from ai_json on AI failure — the calling task catches and
    records the failure under audit_data['errors'].
    """
    website_audit = (audit_data or {}).get("website") or {}
    base_url = website_audit.get("fetched_url") or url

    pages = await _fetch_brand_pages(base_url)
    if not pages:
        raise RuntimeError(f"analyze_brand: could not fetch any page for {url!r}")

    combined = "\n\n".join(f"--- {label} ---\n{text}" for label, text in pages.items())
    prompt = BRAND_ANALYSIS_PROMPT.replace("{website_content}", combined)

    parsed, total_tokens = await ai_json(prompt, provider=provider, api_key=api_key)
    logger.info(
        "analyze_brand %s: %d pages analyzed, %d tokens", base_url, len(pages), total_tokens
    )
    return parsed, total_tokens
