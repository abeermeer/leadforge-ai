"""Social + review enrichment via SocialCrawl API (PRD 3B.6).

WHY: adds organic-social + review-site signals the website/ad audits can't see
— richer email hooks and a sharper re-score. NOT core discovery/audit.

COST-GATED by the caller: only leads with fit_score >= SOCIAL_ENRICH_MIN_SCORE
are enriched (SocialCrawl bills per credit). AUTH is the per-user key from
user_settings.encrypted_keys["socialcrawl"] — no key means a silent no-op
({"skipped": "no_key"}). Multi-tenant: never a shared global key.

Kept lean — 2-4 targeted calls per lead, never the whole catalog:
1. Review-site lookup by domain -> rating + count (email angle + score signal).
2. LinkedIn company -> employees / industry / followers (size + fit).
3. One primary social profile (IG or TikTok preferred), reusing the social
   URLs already extracted by audit/website.py — never re-discovered here.

Failure contract: 402/429 anywhere -> {"error": "quota"}; individual non-quota
call failures are skipped (that section becomes None); if NO call succeeds the
last error is surfaced as {"error": str}. The lead itself is never failed.

The returned credits_used is recorded by the caller into
usage_counters.socialcrawl_credits so per-user cost stays visible.
"""
import logging

import httpx

from services.discovery import normalize_domain

logger = logging.getLogger("trax9.audit.social")

SOCIALCRAWL_BASE = "https://api.socialcrawl.dev/v1"
REQUEST_TIMEOUT_SECONDS = 20.0

_REVIEWS_PATH = "/reviews/lookup"
_LINKEDIN_PATH = "/profiles/linkedin"
_PROFILE_PATH = "/profiles/{platform}"

_QUOTA_STATUS_CODES = (402, 429)

# Preference order for the single primary-profile call (PRD: IG or TikTok
# first — the "big audience, weak site" platforms). LinkedIn is handled by
# its own dedicated call above.
_PRIMARY_PLATFORM_ORDER = ("instagram", "tiktok", "facebook", "twitter")

# Follower count above which an audience is "large" for signal heuristics.
_BIG_AUDIENCE_FOLLOWERS = 10_000
_WEAK_ONPAGE_SCORE = 50
_POOR_RATING = 3.5
_GREAT_RATING = 4.5
_GREAT_RATING_MIN_COUNT = 50


class _QuotaExhausted(Exception):
    """SocialCrawl returned 402/429 — stop immediately, credits are gone."""


def _unwrap(payload: dict) -> dict:
    """SocialCrawl envelopes results under 'data'; unwrap when present."""
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _str_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def _call(
    client: httpx.AsyncClient, path: str, params: dict, errors: list[str]
) -> dict | None:
    """One SocialCrawl GET (= one credit when it succeeds).

    Returns the parsed JSON dict, or None on a non-quota failure (the error
    message is appended to `errors`). Raises _QuotaExhausted on 402/429 so
    the whole enrichment aborts without burning further credits.
    """
    try:
        response = await client.get(path, params=params)
        if response.status_code in _QUOTA_STATUS_CODES:
            raise _QuotaExhausted(f"HTTP {response.status_code} on {path}")
        response.raise_for_status()
        payload = response.json()
    except _QuotaExhausted:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        # First line only — httpx appends multi-line MDN boilerplate we don't
        # want stored in audit_data.
        detail = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        message = f"{path}: {detail}"
        logger.warning("enrich_social: SocialCrawl call failed — %s", message)
        errors.append(message)
        return None
    if not isinstance(payload, dict):
        errors.append(f"{path}: unexpected response type {type(payload).__name__}")
        return None
    return payload


def _parse_reviews(payload: dict) -> dict | None:
    """-> {"platform", "rating", "count", "url"} or None when no rating found."""
    data = _unwrap(payload)
    results = data.get("results")
    if isinstance(results, list) and results and isinstance(results[0], dict):
        data = results[0]  # Universal-search style list: best match first
    rating = _float_or_none(data.get("rating")) or _float_or_none(data.get("average_rating"))
    if rating is None:
        return None
    count = _int_or_none(data.get("count")) or _int_or_none(data.get("review_count")) or 0
    return {
        "platform": _str_or_none(data.get("platform")) or _str_or_none(data.get("source")) or "unknown",
        "rating": rating,
        "count": count,
        "url": _str_or_none(data.get("url")),
    }


def _parse_linkedin(payload: dict) -> dict | None:
    """-> {"employees", "industry", "followers"} or None when nothing usable."""
    data = _unwrap(payload)
    employees = _int_or_none(data.get("employees")) or _int_or_none(data.get("employee_count"))
    industry = _str_or_none(data.get("industry"))
    followers = _int_or_none(data.get("followers")) or _int_or_none(data.get("follower_count"))
    if employees is None and industry is None and followers is None:
        return None
    return {"employees": employees, "industry": industry, "followers": followers}


def _parse_primary(payload: dict, platform: str) -> dict | None:
    """-> {"platform", "handle", "followers", "engagement_rate"} or None."""
    data = _unwrap(payload)
    handle = _str_or_none(data.get("handle")) or _str_or_none(data.get("username"))
    followers = _int_or_none(data.get("followers")) or _int_or_none(data.get("follower_count"))
    if handle is None and followers is None:
        return None
    return {
        "platform": platform,
        "handle": handle or "",
        "followers": followers or 0,
        "engagement_rate": _float_or_none(data.get("engagement_rate")) or 0.0,
    }


def _pick_primary(social_links: dict) -> tuple[str, str] | None:
    """First (platform, profile_url) by preference from the website audit."""
    for platform in _PRIMARY_PLATFORM_ORDER:
        url = social_links.get(platform)
        if isinstance(url, str) and url.strip():
            return platform, url.strip()
    return None


def _build_signals(
    audit_data: dict,
    social_links: dict,
    reviews: dict | None,
    linkedin: dict | None,
    primary: dict | None,
) -> list[str]:
    """Heuristic hook strings for the email writer and re-score."""
    signals: list[str] = []

    onpage = ((audit_data or {}).get("seo") or {}).get("onpage_score")
    weak_site = (
        not isinstance(onpage, bool)
        and isinstance(onpage, (int, float))
        and onpage < _WEAK_ONPAGE_SCORE
    )

    followers = (primary or {}).get("followers") or 0
    if followers > _BIG_AUDIENCE_FOLLOWERS and weak_site:
        signals.append("large audience, weak site")

    if reviews is not None:
        platform = reviews.get("platform") or "review site"
        rating = reviews.get("rating") or 0.0
        count = reviews.get("count") or 0
        if rating and rating < _POOR_RATING:
            signals.append(f"poor {platform} rating ({rating:.1f})")
        elif rating >= _GREAT_RATING and count >= _GREAT_RATING_MIN_COUNT:
            signals.append(f"excellent {platform} reviews ({rating:.1f}, {count} reviews)")

    employees = (linkedin or {}).get("employees")
    if isinstance(employees, int) and employees > 200:
        signals.append(f"large company (~{employees} employees)")

    has_any_link = any(
        isinstance(social_links.get(p), str) and social_links.get(p)
        for p in ("facebook", "instagram", "linkedin", "twitter", "tiktok")
    )
    if not has_any_link and primary is None:
        signals.append("no social presence")

    return signals


async def enrich_social(lead: dict, audit_data: dict, socialcrawl_key: str | None) -> dict:
    """Enrich a high-fit lead with review + social signals via SocialCrawl.

    Reuses social profile URLs from audit_data['website']['social_links']
    (extracted by audit/website.py — never re-discovered here) and makes at
    most 3 API calls: reviews lookup, LinkedIn company, one primary profile.

    Returns (stored under audit_data['social']):
      {"skipped": "no_key"}                      when the user has no key
      {"error": "quota"}                         on 402/429
      {"error": str}                             when every call failed
      {"reviews": {...}|None, "linkedin": {...}|None,
       "primary_social": {...}|None, "signals": [str],
       "credits_used": int}                      on (partial) success

    Never raises — a failed enrichment must not fail the lead. The caller
    records credits_used in usage_counters and re-runs scoring.score_lead
    with audit_data['social'] present (PRD 3B.6 feedback loop).
    """
    if not socialcrawl_key:
        return {"skipped": "no_key"}

    lead = lead or {}
    audit_data = audit_data or {}
    website_audit = audit_data.get("website") or {}
    social_links = website_audit.get("social_links") or {}

    domain = normalize_domain(lead.get("website") or website_audit.get("fetched_url") or "")
    company_name = (lead.get("company_name") or "").strip()
    if not domain and not company_name:
        return {"error": "no domain or company name available to enrich"}

    def _identity_params() -> dict:
        return {"domain": domain} if domain else {"query": company_name}

    errors: list[str] = []
    credits_used = 0
    reviews: dict | None = None
    linkedin: dict | None = None
    primary: dict | None = None

    try:
        async with httpx.AsyncClient(
            base_url=SOCIALCRAWL_BASE,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {socialcrawl_key}"},
        ) as client:
            # 1. Review sites (Trustpilot / TripAdvisor / Google) by domain.
            payload = await _call(client, _REVIEWS_PATH, _identity_params(), errors)
            if payload is not None:
                credits_used += 1
                reviews = _parse_reviews(payload)

            # 2. LinkedIn company: by the exact URL the website audit found,
            #    else resolved by domain/name.
            linkedin_url = social_links.get("linkedin")
            if isinstance(linkedin_url, str) and linkedin_url.strip():
                params = {"url": linkedin_url.strip()}
            else:
                params = _identity_params()
            payload = await _call(client, _LINKEDIN_PATH, params, errors)
            if payload is not None:
                credits_used += 1
                linkedin = _parse_linkedin(payload)

            # 3. One primary social profile — only when the site links one.
            picked = _pick_primary(social_links)
            if picked is not None:
                platform, profile_url = picked
                payload = await _call(
                    client,
                    _PROFILE_PATH.format(platform=platform),
                    {"url": profile_url},
                    errors,
                )
                if payload is not None:
                    credits_used += 1
                    primary = _parse_primary(payload, platform)
    except _QuotaExhausted as exc:
        logger.warning(
            "enrich_social: SocialCrawl quota exhausted for %r (%s)",
            domain or company_name,
            exc,
        )
        return {"error": "quota"}

    if credits_used == 0 and errors:
        return {"error": errors[-1]}

    signals = _build_signals(audit_data, social_links, reviews, linkedin, primary)
    logger.info(
        "enrich_social %r: %d credits, signals=%s", domain or company_name, credits_used, signals
    )
    return {
        "reviews": reviews,
        "linkedin": linkedin,
        "primary_social": primary,
        "signals": signals,
        "credits_used": credits_used,
    }
