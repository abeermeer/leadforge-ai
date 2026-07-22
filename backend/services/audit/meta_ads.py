"""Meta Ads Library audit (PRD section 3B).

Scrapes the public Meta (Facebook) Ads Library search page. The page is
React-rendered, so it goes through the shared Playwright launcher in
services/scraping/browser.py (proxy + UA rotation REQUIRED) with per-domain
politeness delays and the per-source circuit breaker.

URL: https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&q={term}
Rate: ~10 searches/min per IP — every search is throttled via polite_delay()
and each search runs in its own browser session (fresh fingerprint), released
immediately after the search completes.

Search strategy: exact company name first; if that finds nothing, the domain
without its TLD, then a brand short name (first word of the company name).

This module NEVER raises. Any Playwright failure — playwright not installed,
browsers missing, navigation timeout, 403/429 block — degrades to the empty
result shape so the audit chain survives on machines without a browser.
"""
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from services.discovery import normalize_domain
from services.scraping.browser import (
    dismiss_consent,
    get_page,
    is_paused,
    polite_delay,
    record_block,
    record_success,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger("trax9.audit.meta_ads")

_SOURCE = "meta_ads"
_DOMAIN = "facebook.com"

ADS_LIBRARY_URL = (
    "https://www.facebook.com/ads/library/"
    "?active_status=all&ad_type=all&country=ALL&q={query}"
)

# Navigation gets a little headroom; element/content waits are capped at 10s (PRD).
_NAV_TIMEOUT_MS = 20_000
_WAIT_TIMEOUT_MS = 10_000
_EXPAND_SETTLE_MS = 500
_MAX_SAMPLE_ADS = 5

# "1,234 results" / "~1,900 results" / "0 results"
_RESULT_COUNT_RE = re.compile(r"([\d][\d,.\s]*)\s*results?\b", re.IGNORECASE)
_NO_RESULTS_RE = re.compile(
    r"\b0\s+results?\b|no ads (?:found|match)|no results", re.IGNORECASE
)

# ---------------------------------------------------------------- strategy heuristics

_DISCOUNT_RE = re.compile(
    r"\d{1,3}\s*%|\b(?:sale|discount|promo|coupon|deal|save|free shipping|clearance|bogo)\b",
    re.IGNORECASE,
)
_BRAND_WORDS = (
    "brand",
    "story",
    "mission",
    "community",
    "trusted",
    "quality",
    "heritage",
    "since",
    "family owned",
    "who we are",
    "our journey",
)

# ---------------------------------------------------------------- page-side scripts

# The search page is "ready" once it shows either a result count or an
# explicit empty state. A login wall / block never shows either, so the wait
# times out and the caller records a block.
_RESULTS_READY_JS = r"""
() => {
  const text = (document.body && document.body.innerText) || "";
  return /\d[\d,.]*\s*results?/i.test(text)
    || /no ads (found|match)|no results/i.test(text);
}
"""

# Expand truncated primary texts before extraction ("See more" togglers).
_EXPAND_SEE_MORE_JS = r"""
() => {
  const buttons = Array.from(
    document.querySelectorAll('div[role="button"], span[role="button"]')
  );
  let clicks = 0;
  for (const button of buttons) {
    if (clicks >= 10) break;
    const label = (button.textContent || "").trim().toLowerCase();
    if (label === "see more") {
      try { button.click(); clicks += 1; } catch (err) {}
    }
  }
  return clicks;
}
"""

# Meta's class names are obfuscated and unstable, so cards are located by a
# stable text anchor: every ad card contains a "Library ID: ..." leaf node.
# From that anchor we climb to the smallest ancestor that also carries the
# "Started running on" metadata plus the creative, then parse it with text
# heuristics. Best effort by design — every card is wrapped in try/catch.
_EXTRACT_CARDS_JS = r"""
() => {
  const CTA_WORDS = [
    "Shop Now", "Learn More", "Sign Up", "Book Now", "Contact Us", "Get Offer",
    "Get Quote", "Subscribe", "Apply Now", "Download", "Order Now",
    "Send Message", "Send WhatsApp Message", "Watch More", "Get Directions",
    "Call Now", "Donate Now", "Install Now", "Play Game", "Use App", "See Menu"
  ];
  const SKIP = new RegExp(
    "^(library id|started running|sponsored|active$|inactive$|see ad details|" +
    "see summary details|platforms|categories|ads use this creative|" +
    "this ad has multiple versions|open drop)", "i"
  );
  const DOMAIN_LINE = /^[A-Z0-9.\-]+\.[A-Z]{2,}(\/\S*)?$/;

  const markers = Array.from(document.querySelectorAll("span, div")).filter(
    (el) => el.childElementCount === 0
      && /^library id/i.test((el.textContent || "").trim())
  );
  const seen = new Set();
  const cards = [];

  for (const marker of markers) {
    try {
      let node = marker.parentElement;
      let container = null;
      for (let depth = 0; depth < 14 && node; depth++) {
        const text = node.innerText || "";
        if (/started running/i.test(text) && node.querySelector("a, img, video")) {
          container = node;
          if (text.length > 200) break;
        }
        node = node.parentElement;
      }
      if (!container || seen.has(container)) continue;
      seen.add(container);

      const text = container.innerText || "";
      const lines = text.split("\n").map((s) => s.trim()).filter(Boolean);
      const startMatch = text.match(/Started running on ([^\n]+)/i);

      let media = "image";
      if (container.querySelector("video")) media = "video";
      else if (container.querySelectorAll("img").length > 2) media = "carousel";
      else if (!container.querySelector("img")) media = "text";

      let landing = "";
      const outbound = container.querySelector('a[href*="l.facebook.com/l.php"]')
        || Array.from(container.querySelectorAll("a[href^='http']"))
          .find((a) => !a.href.includes("facebook.com"));
      if (outbound) {
        landing = outbound.href;
        const wrapped = landing.match(/[?&]u=([^&]+)/);
        if (wrapped) {
          try { landing = decodeURIComponent(wrapped[1]); } catch (err) {}
        }
      }

      let cta = "";
      for (const word of CTA_WORDS) {
        if (lines.some((l) => l.toLowerCase() === word.toLowerCase())) {
          cta = word;
          break;
        }
      }

      const content = lines.filter((l) =>
        l.length > 1
        && !SKIP.test(l)
        && !DOMAIN_LINE.test(l)
        && !CTA_WORDS.some((w) => w.toLowerCase() === l.toLowerCase())
      );
      let primary = "";
      for (const line of content) if (line.length > primary.length) primary = line;
      let headline = "";
      const primaryIndex = content.indexOf(primary);
      for (let i = primaryIndex + 1; i < content.length; i++) {
        if (content[i] !== primary) { headline = content[i]; break; }
      }

      cards.push({
        headline: headline,
        primary_text: primary,
        cta: cta,
        media_type: media,
        landing_page: landing,
        start_date: startMatch ? startMatch[1].trim() : "",
      });
    } catch (err) {}
  }
  return cards;
}
"""

# ---------------------------------------------------------------- helpers


def _search_url(term: str) -> str:
    """Ads Library search URL for a term."""
    return ADS_LIBRARY_URL.format(query=quote_plus(term))


def _empty_result(ad_library_url: str = "") -> dict:
    """The no-ads result shape (also returned on pause/failure paths)."""
    return {
        "has_ads": False,
        "total_active_ads": 0,
        "sample_ads": [],
        "ad_strategies": [],
        "ad_library_url": ad_library_url,
    }


def _candidate_terms(company_name: str, website: str | None) -> list[str]:
    """Search terms in priority order: exact name, domain stem, brand short name."""
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
    first_word = (company_name or "").strip().split(" ")[0]
    if len(first_word) >= 4:
        _add(first_word)
    return terms


def _parse_result_count(body_text: str) -> int:
    """Parse the 'N results' banner; 0 when absent or unreadable."""
    match = _RESULT_COUNT_RE.search(body_text)
    if not match:
        return 0
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else 0


def _clean_sample(raw: dict) -> dict:
    """Coerce a page-side card dict into the contract shape (all-string, capped)."""

    def _text(key: str, limit: int) -> str:
        value = raw.get(key)
        return str(value).strip()[:limit] if value else ""

    media_type = _text("media_type", 20) or "image"
    if media_type not in ("image", "video", "carousel", "text"):
        media_type = "image"
    return {
        "headline": _text("headline", 300),
        "primary_text": _text("primary_text", 1000),
        "cta": _text("cta", 50),
        "media_type": media_type,
        "landing_page": _text("landing_page", 500),
        "start_date": _text("start_date", 50),
    }


def _classify_strategies(sample_ads: list[dict], company_name: str) -> list[str]:
    """Simple heuristics over sample ad copy (PRD: note patterns).

    discount %/sale wording -> 'discount-focused'; brand-story wording or the
    company name repeated across creatives -> 'brand-awareness'; a majority of
    video creatives -> 'video-led'.
    """
    if not sample_ads:
        return []
    combined = " ".join(
        f"{ad['headline']} {ad['primary_text']}" for ad in sample_ads
    ).lower()
    strategies: list[str] = []
    if _DISCOUNT_RE.search(combined):
        strategies.append("discount-focused")
    name = (company_name or "").strip().lower()
    name_mentions = combined.count(name) if len(name) >= 3 else 0
    if any(word in combined for word in _BRAND_WORDS) or name_mentions >= 2:
        strategies.append("brand-awareness")
    video_count = sum(1 for ad in sample_ads if ad["media_type"] == "video")
    if video_count * 2 > len(sample_ads):
        strategies.append("video-led")
    return strategies


async def _extract_samples(page: "Page") -> list[dict]:
    """Expand truncated copy and pull up to _MAX_SAMPLE_ADS cards. Never raises."""
    try:
        await page.evaluate(_EXPAND_SEE_MORE_JS)
        await page.wait_for_timeout(_EXPAND_SETTLE_MS)
        raw_cards = await page.evaluate(_EXTRACT_CARDS_JS)
    except Exception as exc:
        logger.debug("meta ads card extraction failed: %s", exc)
        return []
    if not isinstance(raw_cards, list):
        return []
    return [
        _clean_sample(card)
        for card in raw_cards[:_MAX_SAMPLE_ADS]
        if isinstance(card, dict)
    ]


async def _run_search(page: "Page", url: str, company_name: str) -> dict:
    """Run one Ads Library search on an already-acquired page.

    Returns the full result dict for a COMPLETED search (with or without ads).
    Raises on block (403/429) or timeout — the caller records the block.
    """
    response = await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
    if response is not None and response.status in (403, 429):
        raise RuntimeError(f"blocked by facebook with HTTP {response.status}")
    await dismiss_consent(page)  # clear the cookie wall so results render
    await page.wait_for_function(_RESULTS_READY_JS, timeout=_WAIT_TIMEOUT_MS)

    body_text = await page.inner_text("body")
    if _NO_RESULTS_RE.search(body_text):
        return _empty_result(url)

    total = _parse_result_count(body_text)
    samples = await _extract_samples(page)
    total = max(total, len(samples))
    if total == 0:
        return _empty_result(url)
    return {
        "has_ads": True,
        "total_active_ads": total,
        "sample_ads": samples,
        "ad_strategies": _classify_strategies(samples, company_name),
        "ad_library_url": url,
    }


# ---------------------------------------------------------------- public API


async def audit_meta_ads(company_name: str, website: str | None = None) -> dict:
    """Audit a company's presence in the Meta Ads Library.

    Searches by exact company name, then domain-without-TLD, then a brand
    short name; the first search that finds ads wins. Each search runs in a
    fresh proxied browser session and is throttled via polite_delay().

    Returns (never raises):
    {
      "has_ads": bool,
      "total_active_ads": int,
      "sample_ads": [{"headline", "primary_text", "cta", "media_type",
                      "landing_page", "start_date"}],   # up to 5
      "ad_strategies": [str],
      "ad_library_url": str
    }
    Circuit-breaker pause, missing playwright, blocks and timeouts all return
    the empty shape. Blocks/timeouts feed record_block(); completed searches
    feed record_success().
    """
    default_url = _search_url(company_name or "")
    if is_paused(_SOURCE):
        logger.info("meta_ads circuit breaker open; skipping audit for %r", company_name)
        return _empty_result(default_url)

    terms = _candidate_terms(company_name, website)
    if not terms:
        logger.warning("audit_meta_ads called with no usable search terms")
        return _empty_result(default_url)

    last_result = _empty_result(_search_url(terms[0]))
    for term in terms:
        url = _search_url(term)
        try:
            await polite_delay(_DOMAIN)
            async with get_page() as page:
                result = await _run_search(page, url, company_name)
        except ImportError:
            logger.warning(
                "playwright is not installed; meta ads audit skipped for %r", company_name
            )
            return _empty_result(url)
        except Exception as exc:
            logger.warning(
                "meta ads search failed for %r (%s): %s", term, type(exc).__name__, exc
            )
            record_block(_SOURCE)
            return _empty_result(url)

        record_success(_SOURCE)
        if result["has_ads"]:
            logger.info(
                "meta ads found for %r: %d active ads", term, result["total_active_ads"]
            )
            return result
        last_result = result

    logger.info("no meta ads found for %r (terms tried: %s)", company_name, ", ".join(terms))
    return last_result
