"""Email finder — locate a contact address for a discovered lead (PRD §3A.5).

Runs after discovery, before/with audit. Without this the lead has no send
target. Three tiers, stopping at the first candidate with confidence >= 60:

1. SCRAPE: fetch homepage + /contact + /contact-us + /about, regex emails and
   mailto: hrefs. Prefer role addresses (info@, hello@, ...) on the lead's own
   domain. Free-mail (gmail/yahoo/...) is only a last-resort fallback.
2. PATTERN: if a person name appears near founder/owner/CEO text on the about
   page, guess first@domain. Low confidence, NO SMTP verification (risky).
3. HUNTER.IO: optional, only when the user supplied a per-user Hunter key.
   Confidence comes from Hunter's own score.

The caller sets lead.email / lead.email_source / lead.email_confidence from
the returned dict; a None email leaves the lead flagged as "needs email".
"""
import re

import httpx

from services.discovery import normalize_domain

FETCH_TIMEOUT_SECONDS = 10.0
CONFIDENCE_THRESHOLD = 60
HUNTER_URL = "https://api.hunter.io/v2/domain-search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Order matters: homepage first, then the pages most likely to list an address.
SCRAPE_PATHS = ("", "/contact", "/contact-us", "/about")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
MAILTO_RE = re.compile(r"""mailto:([^"'<>?\s]+)""", re.IGNORECASE)

# Generic inbox prefixes worth contacting (role addresses).
ROLE_PREFIXES = {"info", "hello", "contact", "sales", "support", "office"}

# Local parts that look generic/automated rather than a person or a role inbox.
GENERIC_LOCALS = ROLE_PREFIXES | {
    "abuse",
    "admin",
    "billing",
    "careers",
    "email",
    "enquiries",
    "help",
    "inquiries",
    "jobs",
    "mail",
    "marketing",
    "postmaster",
    "press",
    "privacy",
    "team",
    "webmaster",
}

# Free-mail providers, matched against the dot-separated labels of the email
# domain so 'mail.yahoo.co.uk' is caught but 'paolo.com' is not.
FREE_MAIL_LABELS = {
    "aol",
    "gmail",
    "googlemail",
    "hotmail",
    "icloud",
    "outlook",
    "proton",
    "protonmail",
    "yahoo",
}

# Substrings that mark a regex hit as junk (asset filenames, tracker noise,
# CMS internals, placeholder and do-not-reply addresses).
JUNK_TOKENS = (
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
    "example.com",
    "no-reply",
    "noreply",
    "sentry",
    "wixpress",
)

# Founder/owner titles for the pattern tier, and a First Last shape nearby.
# NAME_RE is a lookahead so matches overlap: in "Founder Jane Doe" both
# ("Founder", "Jane") and ("Jane", "Doe") are seen, and stopword filtering
# can discard the former without losing the latter.
TITLE_RE = re.compile(r"\b(?:founders?|co-?founders?|owners?|ceo)\b", re.IGNORECASE)
NAME_RE = re.compile(r"\b(?=([A-Z][a-z]{1,19})\s+([A-Z][a-z]{1,19})\b)")
NAME_STOPWORDS = {
    "about", "and", "ceo", "chief", "company", "contact", "executive",
    "founder", "meet", "officer", "our", "owner", "team", "the", "was",
}

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


async def find_email(
    company_name: str, website: str, hunter_key: str | None = None
) -> dict:
    """Find a contact email for a lead.

    Tiers (scrape -> pattern -> hunter) run in order; the first candidate with
    confidence >= 60 wins. Otherwise the best candidate found anywhere is
    returned, or {"email": None, "source": None, "confidence": 0}.

    Never raises: per-page scrape failures are skipped and Hunter API errors
    are swallowed (the tier simply yields nothing).
    """
    empty = {"email": None, "source": None, "confidence": 0}
    domain = normalize_domain(website)
    if not domain:
        return empty

    best = empty
    pages = await _fetch_pages(domain)

    # Tier 1: scrape site pages for addresses.
    candidate = _best_scraped_candidate(pages, domain)
    if candidate:
        if candidate["confidence"] >= CONFIDENCE_THRESHOLD:
            return candidate
        best = _better(best, candidate)

    # Tier 2: guess from a founder/owner name on the about page.
    candidate = _pattern_candidate(pages, domain)
    if candidate:
        if candidate["confidence"] >= CONFIDENCE_THRESHOLD:
            return candidate
        best = _better(best, candidate)

    # Tier 3: Hunter.io domain search, only with a user-provided key.
    if hunter_key:
        try:
            candidate = await _hunter_candidate(domain, hunter_key)
        except (httpx.HTTPError, ValueError):
            candidate = None
        if candidate:
            if candidate["confidence"] >= CONFIDENCE_THRESHOLD:
                return candidate
            best = _better(best, candidate)

    return best


# --------------------------------------------------------------------- tier 1


async def _fetch_pages(domain: str) -> dict[str, str]:
    """Fetch homepage + contact/about pages. Per-page failures are ignored."""
    pages: dict[str, str] = {}
    base = f"https://{domain}"
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for path in SCRAPE_PATHS:
            try:
                response = await client.get(f"{base}{path}")
            except httpx.HTTPError:
                continue
            if response.status_code == 200 and response.text:
                pages[path] = response.text
    return pages


def _extract_emails(html: str) -> set[str]:
    """All lowercase email addresses in page text and mailto: hrefs."""
    found = {match.group(0).lower() for match in EMAIL_RE.finditer(html)}
    for match in MAILTO_RE.finditer(html):
        target = match.group(1).split("?", 1)[0]
        hit = EMAIL_RE.search(target)
        if hit:
            found.add(hit.group(0).lower())
    return found


def _is_junk(email: str) -> bool:
    return any(token in email for token in JUNK_TOKENS)


def _is_free_mail(email_domain: str) -> bool:
    return any(label in FREE_MAIL_LABELS for label in email_domain.split("."))


def _same_domain(email_domain: str, lead_domain: str) -> bool:
    """True if the address lives on the lead's own domain (incl. subdomains)."""
    return (
        email_domain == lead_domain
        or email_domain.endswith(f".{lead_domain}")
        or lead_domain.endswith(f".{email_domain}")
    )


def _looks_personal(local: str) -> bool:
    """'jane', 'jane.doe', 'j-doe' — but not generic inboxes or hex noise."""
    if len(local) < 3 or local in GENERIC_LOCALS:
        return False
    return re.fullmatch(r"[a-z]+(?:[._-][a-z]+){0,2}", local) is not None


def _score(email: str, domain: str) -> int:
    """Ranking score per PRD: own domain +50, role prefix +40, personal +45."""
    local, _, email_domain = email.partition("@")
    on_domain = _same_domain(email_domain, domain)
    score = 0
    if on_domain:
        score += 50
    if local in ROLE_PREFIXES:
        score += 40
    if on_domain and _looks_personal(local):
        score += 45
    return score


def _confidence(email: str, domain: str) -> int:
    """Map a scraped candidate to a confidence value.

    ~90 own-domain role, ~85 anything else on the lead's own domain, 25 for
    the free-mail last resort, low for off-domain strays.
    """
    local, _, email_domain = email.partition("@")
    if _is_free_mail(email_domain):
        return 25
    if _same_domain(email_domain, domain):
        return 90 if local in ROLE_PREFIXES else 85
    return 40 if local in ROLE_PREFIXES else 30


def _best_scraped_candidate(pages: dict[str, str], domain: str) -> dict | None:
    """Pick the best address scraped from the site, if any."""
    candidates: set[str] = set()
    for html in pages.values():
        candidates |= _extract_emails(html)
    candidates = {email for email in candidates if not _is_junk(email)}
    if not candidates:
        return None

    # Free-mail addresses are only considered when nothing else was found.
    proper = [e for e in candidates if not _is_free_mail(e.partition("@")[2])]
    pool = proper or sorted(candidates)
    best = sorted(pool, key=lambda e: (-_score(e, domain), e))[0]
    return {"email": best, "source": "scraped", "confidence": _confidence(best, domain)}


# --------------------------------------------------------------------- tier 2


def _html_to_text(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text)


def _find_person_name(text: str) -> tuple[str, str] | None:
    """First (first, last) capitalized pair near a founder/owner/CEO mention."""
    for title in TITLE_RE.finditer(text):
        window = text[max(0, title.start() - 60): title.end() + 60]
        for name in NAME_RE.finditer(window):
            first, last = name.group(1), name.group(2)
            if first.lower() in NAME_STOPWORDS or last.lower() in NAME_STOPWORDS:
                continue
            return first.lower(), last.lower()
    return None


def _pattern_candidate(pages: dict[str, str], domain: str) -> dict | None:
    """Guess first@domain from a founder name. Confidence 40, never verified."""
    for path in ("/about", "", "/contact", "/contact-us"):
        html = pages.get(path)
        if not html:
            continue
        name = _find_person_name(_html_to_text(html))
        if name:
            first, _last = name
            # Patterns in preference order: first@, first.last@, firstlast@.
            # Without SMTP verification they are equally unproven — take the
            # first one as the candidate per PRD §3A.5.
            return {"email": f"{first}@{domain}", "source": "pattern", "confidence": 40}
    return None


# --------------------------------------------------------------------- tier 3


async def _hunter_candidate(domain: str, hunter_key: str) -> dict | None:
    """Top result of a Hunter.io domain search, confidence = Hunter's score.

    Raises httpx.HTTPError / ValueError on API failure — find_email catches.
    """
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
        response = await client.get(
            HUNTER_URL, params={"domain": domain, "api_key": hunter_key}
        )
    response.raise_for_status()
    data = response.json()

    emails = (data.get("data") or {}).get("emails") or []
    if not emails:
        return None
    top = emails[0]
    email = str(top.get("value") or "").strip().lower()
    if not email or not EMAIL_RE.fullmatch(email):
        return None
    confidence = max(0, min(100, int(top.get("confidence") or 0)))
    return {"email": email, "source": "hunter", "confidence": confidence}


# --------------------------------------------------------------------- shared


def _better(current: dict, candidate: dict) -> dict:
    """Keep whichever candidate has the higher confidence."""
    return candidate if candidate["confidence"] > current["confidence"] else current
