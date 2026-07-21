"""Website Audit — httpx + BeautifulSoup, no external API needed (PRD §3B).

Fetches the lead's homepage once and extracts everything the scoring and
email-writing stages need: tech stack, on-page SEO signals, performance
numbers, security headers, contact details, and social profile URLs
(the social URLs are REUSED later by audit/social.py — do not re-discover).

Resilience contract: every sub-extraction that fails is replaced by a safe
default and logged; audit_website() itself raises only when the page cannot
be fetched at all (httpx errors propagate to the calling task).
"""
import json
import logging
import re
import time
from typing import Callable, TypeVar
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from services.discovery import normalize_domain
from services.discovery.email_finder import EMAIL_RE, JUNK_TOKENS, MAILTO_RE

logger = logging.getLogger("trax9.audit.website")

FETCH_TIMEOUT_SECONDS = 15.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

TECH_STACK_PATTERNS = {
    'shopify':     ['cdn.shopify.com', 'myshopify.com', '/cdn/shop/', 'shopifycdn'],
    'wordpress':   ['/wp-content/', '/wp-admin', '/wp-includes', 'wp-json'],
    'woocommerce': ['/wp-content/plugins/woocommerce', 'woocommerce'],
    'magento':     ['/skin/frontend/', 'Magento', 'mage/'],
    'wix':         ['wix.com', 'WixEditor'],
    'webflow':     ['webflow', 'Webflow'],
    'squarespace': ['squarespace.com', 'Squarespace'],
    'react':       ['react', 'next.js', 'Next.js'],
    'vue':         ['vuejs', 'vue.js'],
    'angular':     ['angular', 'Angular'],
    'jquery':      ['jquery', 'jQuery'],
}

# Open Graph properties worth reporting (PRD step 4).
_OG_PROPERTIES = ("og:title", "og:description", "og:image", "og:url")

# href schemes that are not real page links.
_NON_HTTP_PREFIXES = ("mailto:", "tel:", "javascript:", "data:", "#")

# Loose phone shape: optional country code, then 3-3-4-ish groups. Candidates
# are still filtered by digit count (8-15) to drop prices/years/IDs.
_PHONE_RE = re.compile(r"\+?\d{1,3}[\s.\-]?\(?\d{2,4}\)?[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}")

_MAX_EMAILS = 10
_MAX_PHONES = 5

T = TypeVar("T")


def _safe(name: str, extractor: Callable[[], T], default: T) -> T:
    """Run one sub-extraction; on ANY failure log it and return the default."""
    try:
        return extractor()
    except Exception:  # noqa: BLE001 — resilience contract: never let one section kill the audit
        logger.warning("audit_website: %r extraction failed, using default", name, exc_info=True)
        return default


def _detect_tech_stack(html: str, soup: BeautifulSoup) -> list[str]:
    """Match TECH_STACK_PATTERNS against the HTML, meta generator and script srcs."""
    generator = ""
    meta_generator = soup.find("meta", attrs={"name": "generator"})
    if meta_generator is not None:
        generator = meta_generator.get("content") or ""
    script_srcs = " ".join(
        script.get("src") or "" for script in soup.find_all("script", src=True)
    )
    haystack = f"{html} {generator} {script_srcs}".lower()
    return [
        name
        for name, patterns in TECH_STACK_PATTERNS.items()
        if any(pattern.lower() in haystack for pattern in patterns)
    ]


def _analyze_title(soup: BeautifulSoup) -> dict:
    tag = soup.find("title")
    text = tag.get_text(strip=True) if tag is not None else ""
    return {"exists": bool(text), "length": len(text), "text": text}


def _analyze_meta_description(soup: BeautifulSoup) -> dict:
    tag = soup.find("meta", attrs={"name": "description"})
    content = (tag.get("content") or "").strip() if tag is not None else ""
    return {"exists": bool(content), "length": len(content)}


def _extract_og_tags(soup: BeautifulSoup) -> dict:
    """Found og:* tags only — empty dict means no Open Graph markup."""
    og_tags: dict[str, str] = {}
    for prop in _OG_PROPERTIES:
        tag = soup.find("meta", attrs={"property": prop})
        content = (tag.get("content") or "").strip() if tag is not None else ""
        if content:
            og_tags[prop] = content
    return og_tags


def _has_twitter_card(soup: BeautifulSoup) -> bool:
    return any(
        (tag.get("name") or "").lower().startswith("twitter:")
        for tag in soup.find_all("meta")
    )


def _count_headings(soup: BeautifulSoup) -> dict:
    return {level: len(soup.find_all(level)) for level in ("h1", "h2", "h3", "h4", "h5", "h6")}


def _analyze_images(soup: BeautifulSoup) -> dict:
    images = soup.find_all("img")
    total = len(images)
    with_alt = sum(1 for img in images if (img.get("alt") or "").strip())
    return {
        "total": total,
        "with_alt": with_alt,
        "alt_ratio": round(with_alt / total, 2) if total else 1.0,
    }


def _analyze_links(soup: BeautifulSoup, base_url: str, base_domain: str) -> dict:
    total = internal = external = 0
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.lower().startswith(_NON_HTTP_PREFIXES):
            continue
        total += 1
        host = normalize_domain(urljoin(base_url, href))
        if not host or host == base_domain or host.endswith(f".{base_domain}"):
            internal += 1
        else:
            external += 1
    return {"total": total, "internal": internal, "external": external}


def _jsonld_types(value: object) -> list[str]:
    """Recursively collect @type strings from a parsed JSON-LD document."""
    types: list[str] = []
    if isinstance(value, dict):
        declared = value.get("@type")
        if isinstance(declared, str):
            types.append(declared)
        elif isinstance(declared, list):
            types.extend(t for t in declared if isinstance(t, str))
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in value:
                types.extend(_jsonld_types(value[key]))
    elif isinstance(value, list):
        for item in value:
            types.extend(_jsonld_types(item))
    return types


def _extract_schema_types(soup: BeautifulSoup) -> list[str]:
    """Schema.org types from JSON-LD blocks and Microdata itemtype attributes."""
    types: list[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            types.extend(_jsonld_types(json.loads(raw)))
        except (json.JSONDecodeError, ValueError):
            continue
    for element in soup.find_all(attrs={"itemtype": True}):
        itemtype = (element.get("itemtype") or "").strip().rstrip("/")
        if itemtype:
            types.append(itemtype.rsplit("/", 1)[-1])
    # Dedupe preserving first-seen order.
    return list(dict.fromkeys(t for t in types if t))


def _has_favicon(soup: BeautifulSoup) -> bool:
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        if "icon" in rel:
            return True
    return False


def _has_viewport(soup: BeautifulSoup) -> bool:
    return soup.find("meta", attrs={"name": "viewport"}) is not None


def _has_canonical(soup: BeautifulSoup) -> bool:
    tag = soup.find("link", rel="canonical")
    return tag is not None and bool((tag.get("href") or "").strip())


def _html_lang(soup: BeautifulSoup) -> str | None:
    html_tag = soup.find("html")
    lang = (html_tag.get("lang") or "").strip() if html_tag is not None else ""
    return lang or None


def _has_charset(soup: BeautifulSoup) -> bool:
    if soup.find("meta", charset=True) is not None:
        return True
    http_equiv = soup.find("meta", attrs={"http-equiv": re.compile("content-type", re.IGNORECASE)})
    return http_equiv is not None and "charset" in (http_equiv.get("content") or "").lower()


def _count_resources(soup: BeautifulSoup) -> int:
    count = len(soup.find_all("script", src=True))
    count += len(soup.find_all("img", src=True))
    count += len(soup.find_all(["iframe", "source", "video", "audio"], src=True))
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        if "stylesheet" in rel or "icon" in rel or "preload" in rel:
            count += 1
    return count


def _extract_emails(html: str) -> list[str]:
    """Deduped addresses from page text and mailto: hrefs, junk filtered."""
    found = {match.group(0).lower() for match in EMAIL_RE.finditer(html)}
    for match in MAILTO_RE.finditer(html):
        target = match.group(1).split("?", 1)[0]
        hit = EMAIL_RE.search(target)
        if hit:
            found.add(hit.group(0).lower())
    clean = [email for email in sorted(found) if not any(t in email for t in JUNK_TOKENS)]
    return clean[:_MAX_EMAILS]


def _extract_phones(soup: BeautifulSoup) -> list[str]:
    """Phone numbers from tel: hrefs (preferred) and visible page text."""
    phones: list[str] = []
    seen_digits: set[str] = set()

    def _add(candidate: str) -> None:
        candidate = candidate.strip(" .-")
        if "(" not in candidate:
            # Regex can start mid-parenthesis ("303) 555-...") — drop the stray ")".
            candidate = candidate.replace(")", "")
        digits = re.sub(r"\D", "", candidate)
        if 8 <= len(digits) <= 15 and digits not in seen_digits:
            seen_digits.add(digits)
            phones.append(candidate)

    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if href.lower().startswith("tel:"):
            _add(href[4:])
    for match in _PHONE_RE.finditer(soup.get_text(" ")):
        if len(phones) >= _MAX_PHONES:
            break
        _add(match.group(0))
    return phones[:_MAX_PHONES]


def _find_contact_page(soup: BeautifulSoup, base_url: str) -> str | None:
    """First link that looks like a contact page, as an absolute URL."""
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.lower().startswith(_NON_HTTP_PREFIXES):
            continue
        text = anchor.get_text(" ", strip=True).lower()
        if "contact" in href.lower() or "contact" in text:
            return urljoin(base_url, href)
    return None


def _extract_social_links(soup: BeautifulSoup, base_url: str) -> dict:
    """First Facebook/Instagram/LinkedIn/Twitter profile URL found on the page."""
    social: dict[str, str | None] = {
        "facebook": None,
        "instagram": None,
        "linkedin": None,
        "twitter": None,
    }
    platform_hosts = {
        "facebook": ("facebook.com",),
        "instagram": ("instagram.com",),
        "linkedin": ("linkedin.com",),
        "twitter": ("twitter.com", "x.com"),
    }
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.lower().startswith(_NON_HTTP_PREFIXES):
            continue
        absolute = urljoin(base_url, href)
        host = normalize_domain(absolute)
        if not host or "/share" in absolute.lower() or "sharer" in absolute.lower():
            continue
        for platform, hosts in platform_hosts.items():
            if social[platform] is None and any(
                host == h or host.endswith(f".{h}") for h in hosts
            ):
                social[platform] = absolute
    return social


async def _fetch_homepage(url: str) -> tuple[httpx.Response, int, bool]:
    """Fetch the homepage, falling back https -> http on connection failure.

    Returns (response, load_time_ms, ssl_valid). ssl_valid is True only when
    the page was actually served over HTTPS (no fallback, no http redirect).
    Raises httpx.HTTPError / httpx.HTTPStatusError on total fetch failure.
    """
    target = url.strip()
    if "://" not in target:
        target = f"https://{target}"

    ssl_valid = True
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        started = time.monotonic()
        try:
            response = await client.get(target)
        except httpx.HTTPError:
            if not target.startswith("https://"):
                raise
            # SSL/connection trouble on https — one retry over plain http.
            ssl_valid = False
            target = "http://" + target[len("https://"):]
            started = time.monotonic()
            response = await client.get(target)
        load_time_ms = int((time.monotonic() - started) * 1000)

    response.raise_for_status()
    if not str(response.url).lower().startswith("https://"):
        ssl_valid = False
    return response, load_time_ms, ssl_valid


async def audit_website(url: str) -> dict:
    """Audit a lead's homepage: tech stack, on-page SEO, performance, contacts.

    1. Normalize URL (add https:// if missing), fetch homepage (15s timeout,
       https -> http fallback on connection/SSL failure).
    2. Parse with BeautifulSoup.
    3. Tech stack via TECH_STACK_PATTERNS (HTML + meta generator + script srcs).
    4. Page analysis: title, meta description, Open Graph, Twitter card,
       headings, images/alt, links, schema markup, favicon/viewport/canonical/
       lang/charset.
    5. Performance: load time, page size KB, linked-resource count.
    6. Security: SSL valid, HSTS, X-Frame-Options.
    7. Contacts: emails, phones, contact page URL.
    8. Social profile URLs (reused later by audit/social.py).

    Any sub-extraction that fails yields its safe default; only a total fetch
    failure raises (httpx errors propagate to the calling task).
    """
    response, load_time_ms, ssl_valid = await _fetch_homepage(url)
    html = response.text or ""
    fetched_url = str(response.url)
    base_domain = normalize_domain(fetched_url)

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001 — lxml missing/broken; stdlib parser always works
        soup = BeautifulSoup(html, "html.parser")

    headings = _safe("headings", lambda: _count_headings(soup), dict.fromkeys(
        ("h1", "h2", "h3", "h4", "h5", "h6"), 0
    ))
    headers = response.headers

    return {
        "tech_stack": _safe("tech_stack", lambda: _detect_tech_stack(html, soup), []),
        "title": _safe(
            "title", lambda: _analyze_title(soup), {"exists": False, "length": 0, "text": ""}
        ),
        "meta_description": _safe(
            "meta_description",
            lambda: _analyze_meta_description(soup),
            {"exists": False, "length": 0},
        ),
        "og_tags": _safe("og_tags", lambda: _extract_og_tags(soup), {}),
        "twitter_card": _safe("twitter_card", lambda: _has_twitter_card(soup), False),
        "h1": {"exists": headings.get("h1", 0) > 0, "count": headings.get("h1", 0)},
        "headings": headings,
        "images": _safe(
            "images",
            lambda: _analyze_images(soup),
            {"total": 0, "with_alt": 0, "alt_ratio": 1.0},
        ),
        "links": _safe(
            "links",
            lambda: _analyze_links(soup, fetched_url, base_domain),
            {"total": 0, "internal": 0, "external": 0},
        ),
        "schema_types": _safe("schema_types", lambda: _extract_schema_types(soup), []),
        "favicon": _safe("favicon", lambda: _has_favicon(soup), False),
        "viewport": _safe("viewport", lambda: _has_viewport(soup), False),
        "canonical": _safe("canonical", lambda: _has_canonical(soup), False),
        "html_lang": _safe("html_lang", lambda: _html_lang(soup), None),
        "charset": _safe("charset", lambda: _has_charset(soup), False),
        "load_time_ms": load_time_ms,
        "page_size_kb": round(len(response.content) / 1024, 1),
        "resource_count": _safe("resource_count", lambda: _count_resources(soup), 0),
        "ssl_valid": ssl_valid,
        "hsts": "strict-transport-security" in headers,
        "x_frame_options": "x-frame-options" in headers,
        "emails": _safe("emails", lambda: _extract_emails(html), []),
        "phones": _safe("phones", lambda: _extract_phones(soup), []),
        "contact_page": _safe("contact_page", lambda: _find_contact_page(soup, fetched_url), None),
        "social_links": _safe(
            "social_links",
            lambda: _extract_social_links(soup, fetched_url),
            {"facebook": None, "instagram": None, "linkedin": None, "twitter": None},
        ),
        "fetched_url": fetched_url,
    }
