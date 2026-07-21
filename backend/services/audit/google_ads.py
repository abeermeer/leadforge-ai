"""Google Ads Transparency Center audit (PRD section 3B, NEW file block).

Scrapes the public Ads Transparency Center. The page is JS-rendered (an
Angular SPA), so it goes through the shared Playwright launcher in
services/scraping/browser.py (proxy + UA rotation) with per-domain politeness
delays and the per-source circuit breaker.

URL: https://adstransparency.google.com/?region=anywhere&query={advertiser}
Rate: ~10 searches/min per IP — proxies rotate per search (fresh browser per
term) and every search is throttled via polite_delay().

Search strategy: company name first; if that finds nothing, the domain
without its TLD. The query URL surfaces advertiser suggestions; when only
suggestions render, the first one is clicked to open the advertiser's
creatives grid.

Returns the dict stored under audit_data["google_ads"]. This module NEVER
raises: any Playwright failure — playwright not installed, browsers missing,
navigation timeout, 403/429 block — degrades to the empty result shape so the
audit chain survives on machines without a browser.
"""
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from services.discovery import normalize_domain
from services.scraping.browser import (
    get_page,
    is_paused,
    polite_delay,
    record_block,
    record_success,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger("trax9.audit.google_ads")

_SOURCE = "google_ads"
_DOMAIN = "adstransparency.google.com"

TRANSPARENCY_URL = "https://adstransparency.google.com/?region=anywhere&query={query}"

# Navigation gets a little headroom; element/content waits are capped at 10s (PRD).
_NAV_TIMEOUT_MS = 20_000
_WAIT_TIMEOUT_MS = 10_000
_MAX_SAMPLE_ADS = 5
_MAX_REGIONS = 10

# "1,234 ads" / "~500 ads"
_AD_COUNT_RE = re.compile(r"([\d][\d,.\s]*)\s*ads?\b", re.IGNORECASE)
_NO_ADS_RE = re.compile(
    r"no ads|hasn't shown ads|has not shown ads|no results", re.IGNORECASE
)
_REGIONS_RE = re.compile(r"regions?:\s*([^\n]+)", re.IGNORECASE)

_KNOWN_FORMATS = ("text", "image", "video")

# ---------------------------------------------------------------- page-side scripts

# The SPA is "ready" once it shows creatives, an ad count, an explicit empty
# state, or advertiser suggestions. A block page shows none of these, so the
# wait times out and the caller records a block.
_SEARCH_READY_JS = r"""
() => {
  if (document.querySelector(
    "creative-preview, [class*='creative-preview'], priority-creative-grid"
  )) return true;
  const text = (document.body && document.body.innerText) || "";
  if (/\d[\d,.]*\s*ads?\b/i.test(text)) return true;
  if (/no ads|hasn't shown ads|has not shown ads|no results/i.test(text)) return true;
  return document.querySelector("[role='option'], [class*='suggestion']") !== null;
}
"""

# When the query only surfaced advertiser suggestions, open the first one.
_CLICK_FIRST_SUGGESTION_JS = r"""
() => {
  if (document.querySelector("creative-preview, [class*='creative-preview']")) {
    return false;
  }
  const option = document.querySelector("[role='option'], [class*='suggestion']");
  if (!option) return false;
  try { option.click(); return true; } catch (err) { return false; }
}
"""

# After clicking a suggestion, wait for the advertiser page to settle.
_CREATIVES_READY_JS = r"""
() => {
  if (document.querySelector("creative-preview, [class*='creative-preview']")) {
    return true;
  }
  const text = (document.body && document.body.innerText) || "";
  return /\d[\d,.]*\s*ads?\b/i.test(text)
    || /no ads|hasn't shown ads|has not shown ads/i.test(text);
}
"""

# Creatives often render inside cross-origin iframes, so text/link extraction
# is best effort; format detection falls back to 'text' when nothing is
# visible in the top document. Every card is wrapped in try/catch.
_EXTRACT_CREATIVES_JS = r"""
() => {
  const cards = [];
  const nodes = Array.from(document.querySelectorAll(
    "creative-preview, [class*='creative-preview']"
  )).slice(0, 12);
  for (const node of nodes) {
    try {
      let format = "text";
      if (node.querySelector("video")) format = "video";
      else if (node.querySelector("img")) format = "image";

      const text = (node.innerText || "").trim();
      const lastMatch = text.match(/last shown[:\s]*([^\n]+)/i);

      let landing = "";
      const link = Array.from(node.querySelectorAll("a[href^='http']"))
        .find((a) => !a.href.includes("google.com"));
      if (link) landing = link.href;

      const lines = text.split("\n")
        .map((s) => s.trim())
        .filter((s) => s && !/^last shown/i.test(s));
      let preview = "";
      for (const line of lines) if (line.length > preview.length) preview = line;

      cards.push({
        format: format,
        preview_text: preview,
        last_shown: lastMatch ? lastMatch[1].trim() : "",
        landing_url: landing,
      });
    } catch (err) {}
  }
  return cards;
}
"""

# ---------------------------------------------------------------- helpers


def _search_url(term: str) -> str:
    """Transparency Center search URL for a term."""
    return TRANSPARENCY_URL.format(query=quote_plus(term))


def _empty_result(transparency_url: str = "") -> dict:
    """The not-an-advertiser result shape (also returned on pause/failure paths)."""
    return {
        "is_advertiser": False,
        "total_ads": 0,
        "formats": [],
        "sample_ads": [],
        "regions": [],
        "transparency_url": transparency_url,
    }


def _candidate_terms(company_name: str, website: str | None) -> list[str]:
    """Search terms in priority order: company name, then domain without TLD."""
    terms: list[str] = []
    seen: set[str] = set()

    def _add(term: str) -> None:
        cleaned = term.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            terms.append(cleaned)

    _add(company_name or "")
    if website:
        domain = normalize_domain(website)
        if domain:
            _add(domain.split(".", 1)[0])
    return terms


def _parse_ad_count(body_text: str) -> int:
    """Parse the 'N ads' banner; 0 when absent or unreadable."""
    match = _AD_COUNT_RE.search(body_text)
    if not match:
        return 0
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else 0


def _parse_regions(body_text: str) -> list[str]:
    """Parse a 'Region(s): ...' line into a list; [] when not shown."""
    match = _REGIONS_RE.search(body_text)
    if not match:
        return []
    parts = [part.strip() for part in re.split(r"[,;]", match.group(1)) if part.strip()]
    return parts[:_MAX_REGIONS]


def _clean_sample(raw: dict) -> dict:
    """Coerce a page-side creative dict into the contract shape (all-string, capped)."""

    def _text(key: str, limit: int) -> str:
        value = raw.get(key)
        return str(value).strip()[:limit] if value else ""

    ad_format = _text("format", 20) or "text"
    if ad_format not in _KNOWN_FORMATS:
        ad_format = "text"
    return {
        "format": ad_format,
        "preview_text": _text("preview_text", 500),
        "last_shown": _text("last_shown", 50),
        "landing_url": _text("landing_url", 500),
    }


async def _extract_samples(page: "Page") -> list[dict]:
    """Pull up to _MAX_SAMPLE_ADS creatives from the current page. Never raises."""
    try:
        raw_cards = await page.evaluate(_EXTRACT_CREATIVES_JS)
    except Exception as exc:
        logger.debug("google ads creative extraction failed: %s", exc)
        return []
    if not isinstance(raw_cards, list):
        return []
    return [
        _clean_sample(card)
        for card in raw_cards[:_MAX_SAMPLE_ADS]
        if isinstance(card, dict)
    ]


async def _run_search(page: "Page", url: str) -> dict:
    """Run one Transparency Center search on an already-acquired page.

    Returns the full result dict for a COMPLETED search (advertiser or not).
    Raises on block (403/429) or timeout — the caller records the block.
    """
    response = await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
    if response is not None and response.status in (403, 429):
        raise RuntimeError(f"blocked by google with HTTP {response.status}")
    await page.wait_for_function(_SEARCH_READY_JS, timeout=_WAIT_TIMEOUT_MS)

    # The query URL may only surface advertiser suggestions; open the first
    # one so the creatives grid renders. A timeout here propagates as a block.
    clicked = await page.evaluate(_CLICK_FIRST_SUGGESTION_JS)
    if clicked:
        await page.wait_for_function(_CREATIVES_READY_JS, timeout=_WAIT_TIMEOUT_MS)

    body_text = await page.inner_text("body")
    if _NO_ADS_RE.search(body_text):
        return _empty_result(url)

    samples = await _extract_samples(page)
    total = max(_parse_ad_count(body_text), len(samples))
    if total == 0:
        return _empty_result(url)

    formats = list(dict.fromkeys(ad["format"] for ad in samples if ad["format"]))
    # The search covers every region ("anywhere"); when the page does not
    # spell regions out, report that scope rather than an empty list.
    regions = _parse_regions(body_text) or ["anywhere"]
    return {
        "is_advertiser": True,
        "total_ads": total,
        "formats": formats,
        "sample_ads": samples,
        "regions": regions,
        "transparency_url": url,
    }


# ---------------------------------------------------------------- public API


async def audit_google_ads(company_name: str, website: str | None = None) -> dict:
    """Audit a company's presence in the Google Ads Transparency Center.

    Searches by company name, then domain-without-TLD; the first search that
    finds an advertiser wins. Each search runs in a fresh proxied browser
    session and is throttled via polite_delay().

    Returns (never raises):
    {
      "is_advertiser": bool,
      "total_ads": int,
      "formats": [str],                     # subset of text/image/video
      "sample_ads": [{"format", "preview_text", "last_shown", "landing_url"}],
      "regions": [str],
      "transparency_url": str
    }
    Circuit-breaker pause, missing playwright, blocks and timeouts all return
    the empty shape. Blocks/timeouts feed record_block(); completed searches
    feed record_success().
    """
    default_url = _search_url(company_name or "")
    if is_paused(_SOURCE):
        logger.info("google_ads circuit breaker open; skipping audit for %r", company_name)
        return _empty_result(default_url)

    terms = _candidate_terms(company_name, website)
    if not terms:
        logger.warning("audit_google_ads called with no usable search terms")
        return _empty_result(default_url)

    last_result = _empty_result(_search_url(terms[0]))
    for term in terms:
        url = _search_url(term)
        try:
            await polite_delay(_DOMAIN)
            async with get_page() as page:
                result = await _run_search(page, url)
        except ImportError:
            logger.warning(
                "playwright is not installed; google ads audit skipped for %r", company_name
            )
            return _empty_result(url)
        except Exception as exc:
            logger.warning(
                "google ads search failed for %r (%s): %s", term, type(exc).__name__, exc
            )
            record_block(_SOURCE)
            return _empty_result(url)

        record_success(_SOURCE)
        if result["is_advertiser"]:
            logger.info(
                "google ads found for %r: %d ads", term, result["total_ads"]
            )
            return result
        last_result = result

    logger.info(
        "no google ads found for %r (terms tried: %s)", company_name, ", ".join(terms)
    )
    return last_result
