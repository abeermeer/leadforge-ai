"""Directory-based Lead Discovery (Yelp, Yellow Pages).

Playwright via services/scraping/browser.py — proxy + UA rotation REQUIRED,
these sites ban fast. Spec: PRD §3A.

Navigates a directory's search results, extracts business name/website/phone
per result card, and paginates via the next-page link up to max_pages.
Blocked (403/429) or timed-out pages feed the circuit breaker in
scraping.browser, which pauses the source after repeated blocks.
"""
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote_plus, urlparse

from services.discovery import normalize_domain
from services.scraping.browser import (
    get_page,
    is_paused,
    polite_delay,
    record_block,
    record_success,
)

if TYPE_CHECKING:  # playwright imports stay lazy at runtime (see browser.py)
    from playwright.async_api import ElementHandle, Page

logger = logging.getLogger("trax9.discovery.directory_scraper")

_NAV_TIMEOUT_MS = 30_000
_CARD_TIMEOUT_MS = 10_000
_BLOCK_STATUSES = {403, 429}

# Next-page link: aria-label first, generic class fallback.
_NEXT_SELECTORS = ("a[aria-label*='Next']", ".next")

# North-America style phone numbers as rendered on Yelp/Yellow Pages cards.
_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]{0,2}\d{3}[\s.-]?\d{4}")

DIRECTORIES = {
    "yelp": {
        "url_template": "https://www.yelp.com/search?find_desc={keyword}&find_loc={location}",
        "card_selector": "div[data-testid='search-results'] > div",
        "name_selector": "a[data-testid='business-name']",
        "website_selector": "a[data-testid='business-website']",
    },
    "yellowpages": {
        "url_template": "https://www.yellowpages.com/search?search_terms={keyword}&geo_location_terms={location}",
        "card_selector": "div.result",
        "name_selector": "a.business-name",
        "website_selector": "a[class*='website']",
    },
}


def _unwrap_redirect(href: str) -> str:
    """Resolve directory redirect links to the real destination URL.

    Yelp business-website links wrap the real URL in a ?url= redirect param
    ('/biz_redir?url=http%3A%2F%2Facme.com&...'). Links without a url query
    param are returned unchanged.
    """
    try:
        params = parse_qs(urlparse(href).query)
    except ValueError:
        return href
    wrapped = params.get("url")
    if wrapped and wrapped[0]:
        return wrapped[0]
    return href


async def _extract_card(card: "ElementHandle", config: dict, directory: str) -> dict | None:
    """Extract one lead dict from a result card, or None if unusable.

    Cards without a business name are filler/ad blocks; cards without a
    resolvable website cannot be deduped or audited downstream. Both -> None.
    """
    name_el = await card.query_selector(config["name_selector"])
    if name_el is None:
        return None
    company_name = (await name_el.inner_text()).strip()
    if not company_name:
        return None

    website = ""
    site_el = await card.query_selector(config["website_selector"])
    if site_el is not None:
        href = await site_el.get_attribute("href") or ""
        website = normalize_domain(_unwrap_redirect(href))
    if not website:
        return None

    phone = None
    match = _PHONE_RE.search(await card.inner_text())
    if match:
        phone = match.group(0).strip()

    return {
        "company_name": company_name,
        "website": website,
        "phone": phone,
        "source": directory,
    }


async def _click_next(page: "Page") -> bool:
    """Click the next-page link if present. False when there is no next page."""
    from playwright.async_api import Error as PlaywrightError

    for selector in _NEXT_SELECTORS:
        link = await page.query_selector(selector)
        if link is not None:
            try:
                await link.click(timeout=_CARD_TIMEOUT_MS)
            except PlaywrightError:
                return False
            return True
    return False


async def scrape_directory(
    directory: str,
    keyword: str,
    location: str,
    max_pages: int = 3,
) -> list[dict]:
    """Discover businesses by scraping a web directory's search results.

    Scrapes up to max_pages result pages for keyword/location on the given
    directory (a DIRECTORIES key: 'yelp' or 'yellowpages') through the shared
    proxied browser, with a politeness delay before every page. Results are
    deduplicated on normalized domain within the call.

    Returns lead dicts: company_name, website (normalized domain), phone,
    source=<directory>. Returns [] immediately while the circuit breaker has
    the source paused. A blocked (403/429) or timed-out page records a block
    and stops pagination — partial results are still returned.

    Raises ValueError for an unknown directory name.
    """
    try:
        config = DIRECTORIES[directory]
    except KeyError:
        raise ValueError(
            f"Unknown directory {directory!r}; expected one of {sorted(DIRECTORIES)}"
        ) from None

    if is_paused(directory):
        logger.warning("scrape_directory skipped: %s paused by circuit breaker", directory)
        return []

    # Lazy import so the module loads on machines without playwright installed.
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    search_url = config["url_template"].format(
        keyword=quote_plus(keyword), location=quote_plus(location)
    )
    site_domain = normalize_domain(config["url_template"])
    results: list[dict] = []
    seen_domains: set[str] = set()

    async with get_page() as page:
        for page_index in range(max_pages):
            await polite_delay(site_domain)
            try:
                if page_index == 0:
                    response = await page.goto(search_url, timeout=_NAV_TIMEOUT_MS)
                    if response is not None and response.status in _BLOCK_STATUSES:
                        record_block(directory)
                        logger.warning(
                            "%s blocked search page (HTTP %d)", directory, response.status
                        )
                        break
                elif not await _click_next(page):
                    break  # no next-page link — end of results
                await page.wait_for_selector(
                    config["card_selector"], timeout=_CARD_TIMEOUT_MS
                )
            except PlaywrightTimeout:
                record_block(directory)
                logger.warning(
                    "%s timed out on page %d (likely blocked)", directory, page_index + 1
                )
                break
            record_success(directory)

            for card in await page.query_selector_all(config["card_selector"]):
                try:
                    lead = await _extract_card(card, config, directory)
                except PlaywrightError:
                    continue  # card detached mid-extraction
                if lead is None or lead["website"] in seen_domains:
                    continue
                seen_domains.add(lead["website"])
                results.append(lead)

    logger.info(
        "scrape_directory %s '%s' in '%s': %d unique leads",
        directory,
        keyword,
        location,
        len(results),
    )
    return results
