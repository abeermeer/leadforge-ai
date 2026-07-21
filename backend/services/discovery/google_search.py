"""Google Custom Search Lead Discovery.

API: Google Custom Search JSON API. Free: 100 queries/day. Paid: $5 per 1K.
Spec: PRD §3A.

Runs the four PRD query templates, 10 results per page, skips social/platform
domains that can never be business leads, and dedupes on normalized domain
within the call.
"""
import logging
import re

import httpx

from services.discovery import normalize_domain

logger = logging.getLogger("trax9.discovery.google_search")

SEARCH_URL = "https://www.googleapis.com/customsearch/v1"
_RESULTS_PER_PAGE = 10
_REQUEST_TIMEOUT = 15.0

# Never return these as leads
EXCLUDE_DOMAINS = [
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "yelp.com", "yellowpages.com", "tripadvisor.com", "pinterest.com",
    "youtube.com", "wikipedia.org", "amazon.com", "reddit.com",
    # platform/CMS homepages, not businesses
    "wix.com", "squarespace.com", "shopify.com", "wordpress.com", "godaddy.com",
]

_QUERY_TEMPLATES = [
    "{keyword} {location}",
    "{keyword} website {location}",
    "best {keyword} in {location}",
    "{keyword} online store {location}",
]

# Trailing '| Home' / '- Official Site' style suffixes to strip from page
# titles. Separator must be whitespace-bounded so 'E-Commerce' survives.
_TITLE_SUFFIX_RE = re.compile(
    r"\s+[|–—-]\s+"
    r"(?:home|home\s*page|welcome|website|official|official\s+site|official\s+website)\s*$",
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    """Clean a result title into a company_name hint.

    'Acme Plumbing | Home' -> 'Acme Plumbing'. Strips generic suffixes
    repeatedly ('Acme - Official Site | Home' -> 'Acme').
    """
    name = (title or "").strip()
    while True:
        stripped = _TITLE_SUFFIX_RE.sub("", name).strip()
        if stripped == name:
            break
        name = stripped
    return name or "Unknown"


def _is_excluded(domain: str) -> bool:
    """True when domain is (or is a subdomain of) an EXCLUDE_DOMAINS entry."""
    return any(domain == bad or domain.endswith("." + bad) for bad in EXCLUDE_DOMAINS)


async def search_google(
    keyword: str,
    location: str,
    pages: int = 3,
    api_key: str | None = None,
    cx: str | None = None,
) -> list[dict]:
    """Discover businesses via Google Custom Search.

    Runs the four PRD query templates for the keyword/location, paginating up
    to `pages` pages of 10 results each per query. Excluded domains are
    skipped and results are deduplicated on normalized domain across all
    queries in this call.

    Returns lead dicts: company_name, website, source='google_search',
    search_query. Missing api_key or cx -> [] (warn, no crash).
    Network/HTTP failures raise httpx errors for the calling task to catch.
    """
    if not api_key or not cx:
        logger.warning("search_google skipped: missing Custom Search api_key or cx")
        return []

    results: list[dict] = []
    seen_domains: set[str] = set()

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        for template in _QUERY_TEMPLATES:
            query = template.format(keyword=keyword, location=location)
            for page in range(pages):
                params = {
                    "key": api_key,
                    "cx": cx,
                    "q": query,
                    "num": _RESULTS_PER_PAGE,
                    "start": page * _RESULTS_PER_PAGE + 1,
                }
                resp = await client.get(SEARCH_URL, params=params)
                resp.raise_for_status()
                items = resp.json().get("items") or []

                for item in items:
                    domain = normalize_domain(item.get("link") or "")
                    if not domain or domain in seen_domains or _is_excluded(domain):
                        continue
                    seen_domains.add(domain)
                    results.append(
                        {
                            "company_name": _clean_title(item.get("title") or ""),
                            "website": domain,
                            "source": "google_search",
                            "search_query": query,
                        }
                    )

                if len(items) < _RESULTS_PER_PAGE:
                    break  # last page for this query

    logger.info(
        "search_google '%s' in '%s': %d unique leads", keyword, location, len(results)
    )
    return results
