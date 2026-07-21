"""SEO Audit — Google PageSpeed API (free with key) + on-page analysis (PRD §3B).

Three parts, all resilient:
1. PageSpeed Insights for MOBILE and DESKTOP. Skipped cleanly (empty dict)
   when no API key is configured; a failed strategy call yields {} for that
   strategy instead of crashing. Caching lives in the audit task layer via
   audit_cache — this module is stateless.
2. On-page SEO score (0-100) computed from the audit_website() result.
3. Technical SEO: robots.txt, sitemap.xml, and a human-readable issues list.
"""
import logging

import httpx

from services.discovery import normalize_domain

logger = logging.getLogger("trax9.audit.seo")

PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# PageSpeed runs a full Lighthouse pass server-side — it is slow.
PAGESPEED_TIMEOUT_SECONDS = 60.0
FETCH_TIMEOUT_SECONDS = 10.0

_STRATEGIES = ("mobile", "desktop")
_CATEGORIES = ("PERFORMANCE", "ACCESSIBILITY", "BEST_PRACTICES", "SEO")
_MAX_FINDINGS = 10

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ------------------------------------------------------------------ part 1: PageSpeed


def _category_score(categories: dict, name: str) -> int | None:
    score = (categories.get(name) or {}).get("score")
    if isinstance(score, (int, float)):
        return int(round(score * 100))
    return None


def _audit_numeric(audits: dict, audit_id: str, digits: int = 0) -> float | int | None:
    value = (audits.get(audit_id) or {}).get("numericValue")
    if not isinstance(value, (int, float)):
        return None
    return round(value, digits) if digits else int(round(value))


def _parse_pagespeed(data: dict) -> dict:
    """Flatten one PageSpeed response into the fields the PRD asks for."""
    lighthouse = data.get("lighthouseResult") or {}
    categories = lighthouse.get("categories") or {}
    audits = lighthouse.get("audits") or {}

    opportunities: list[str] = []
    for audit in audits.values():
        details_type = ((audit.get("details") or {}).get("type")) or ""
        score = audit.get("score")
        if details_type == "opportunity" and isinstance(score, (int, float)) and score < 0.9:
            title = (audit.get("title") or "").strip()
            if title:
                opportunities.append(title)

    diagnostics: list[str] = []
    performance_refs = (categories.get("performance") or {}).get("auditRefs") or []
    for ref in performance_refs:
        if ref.get("group") != "diagnostics":
            continue
        audit = audits.get(ref.get("id") or "") or {}
        score = audit.get("score")
        if isinstance(score, (int, float)) and score < 0.9:
            title = (audit.get("title") or "").strip()
            if title:
                diagnostics.append(title)

    return {
        "performance_score": _category_score(categories, "performance"),
        "accessibility_score": _category_score(categories, "accessibility"),
        "best_practices_score": _category_score(categories, "best-practices"),
        "seo_score": _category_score(categories, "seo"),
        "fcp": _audit_numeric(audits, "first-contentful-paint"),
        "lcp": _audit_numeric(audits, "largest-contentful-paint"),
        "cls": _audit_numeric(audits, "cumulative-layout-shift", digits=3),
        "tbt": _audit_numeric(audits, "total-blocking-time"),
        "speed_index": _audit_numeric(audits, "speed-index"),
        "opportunities": opportunities[:_MAX_FINDINGS],
        "diagnostics": diagnostics[:_MAX_FINDINGS],
    }


async def _fetch_pagespeed(url: str, api_key: str) -> dict:
    """Run PageSpeed for both strategies. A failed strategy yields {} for it."""
    results: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=PAGESPEED_TIMEOUT_SECONDS) as client:
        for strategy in _STRATEGIES:
            params = [
                ("url", url),
                ("key", api_key),
                ("strategy", strategy.upper()),
            ] + [("category", category) for category in _CATEGORIES]
            try:
                response = await client.get(PAGESPEED_URL, params=params)
                response.raise_for_status()
                results[strategy] = _parse_pagespeed(response.json())
            except (httpx.HTTPError, ValueError):
                logger.warning("PageSpeed %s failed for %s", strategy, url, exc_info=True)
                results[strategy] = {}
    return results


# ------------------------------------------------------------------ part 2: on-page score


def _onpage_score(onpage_data: dict | None) -> int:
    """Score on-page SEO 0-100 from audit_website() output (PRD §3B rubric).

    The PRD's "+20 title (exists, 30-60 chars, has keyword)" is split into
    exists (+10) and length (+10) because no target keyword is known at audit
    time; likewise "+15 meta description" splits into exists (+8) and
    length 120-160 (+7). Remaining weights are verbatim from the PRD.
    """
    data = onpage_data or {}
    score = 0

    title = data.get("title") or {}
    if title.get("exists"):
        score += 10
    if 30 <= (title.get("length") or 0) <= 60:
        score += 10

    meta = data.get("meta_description") or {}
    if meta.get("exists"):
        score += 8
    if 120 <= (meta.get("length") or 0) <= 160:
        score += 7

    if (data.get("h1") or {}).get("exists"):
        score += 10
    if data.get("og_tags"):
        score += 10
    if data.get("schema_types"):
        score += 10

    images = data.get("images") or {}
    if (images.get("alt_ratio") or 0) > 0.8:
        score += 10

    if data.get("canonical"):
        score += 10
    if data.get("viewport"):
        score += 5
    if data.get("favicon"):
        score += 5
    if data.get("ssl_valid"):
        score += 5

    return score


# ------------------------------------------------------------------ part 3: technical SEO


def _parse_robots(text: str) -> tuple[bool, list[str]]:
    """Parse robots.txt -> (allows_all_crawlers, sitemap URLs declared in it).

    allows is False only when a 'User-agent: *' group contains 'Disallow: /'
    (full-site block); anything narrower still counts as crawlable.
    """
    allows = True
    sitemaps: list[str] = []
    current_agents: list[str] = []
    expecting_agents = True

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            if not expecting_agents:
                current_agents = []
                expecting_agents = True
            current_agents.append(value)
        elif key == "sitemap":
            if value:
                sitemaps.append(value)
        else:
            expecting_agents = False
            if key == "disallow" and value == "/" and "*" in current_agents:
                allows = False

    return allows, sitemaps


async def _check_technical(domain: str) -> dict:
    """robots.txt + sitemap.xml checks. Network failures degrade to False/0."""
    technical = {
        "robots_exists": False,
        "robots_allows": True,
        "sitemap_exists": False,
        "sitemap_urls": 0,
    }
    if not domain:
        return technical

    robots_sitemaps: list[str] = []
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        try:
            response = await client.get(f"https://{domain}/robots.txt")
            if response.status_code == 200 and response.text.strip():
                technical["robots_exists"] = True
                allows, robots_sitemaps = _parse_robots(response.text)
                technical["robots_allows"] = allows
        except httpx.HTTPError:
            logger.warning("robots.txt check failed for %s", domain, exc_info=True)

        candidates = robots_sitemaps + [
            f"https://{domain}/sitemap.xml",
            f"https://{domain}/sitemap_index.xml",
        ]
        for candidate in candidates:
            try:
                response = await client.get(candidate)
            except httpx.HTTPError:
                continue
            if response.status_code != 200 or not response.text.strip():
                continue
            body = response.text
            if "<loc" not in body and "<urlset" not in body and "<sitemapindex" not in body:
                continue
            technical["sitemap_exists"] = True
            technical["sitemap_urls"] = body.count("<loc>")
            break

    return technical


def _build_issues(onpage_data: dict | None, pagespeed: dict, technical: dict) -> list[str]:
    """Human-readable SEO issues for the outreach email's audit hook."""
    issues: list[str] = []

    if onpage_data:
        title = onpage_data.get("title") or {}
        if not title.get("exists"):
            issues.append("Missing title tag")
        elif not 30 <= (title.get("length") or 0) <= 60:
            issues.append("Title length outside the recommended 30-60 characters")

        meta = onpage_data.get("meta_description") or {}
        if not meta.get("exists"):
            issues.append("Missing meta description")
        elif not 120 <= (meta.get("length") or 0) <= 160:
            issues.append("Meta description length outside the recommended 120-160 characters")

        if not (onpage_data.get("h1") or {}).get("exists"):
            issues.append("Missing H1 heading")
        if not onpage_data.get("canonical"):
            issues.append("Missing canonical tag")

        images = onpage_data.get("images") or {}
        if (images.get("total") or 0) > 0 and (images.get("alt_ratio") or 0) < 0.8:
            issues.append("Images missing alt text")

        if not onpage_data.get("og_tags"):
            issues.append("Missing Open Graph tags")
        if not onpage_data.get("schema_types"):
            issues.append("No schema markup detected")
        if not onpage_data.get("ssl_valid"):
            issues.append("Site not served over valid HTTPS")

    if not technical.get("sitemap_exists"):
        issues.append("Missing sitemap.xml")
    if technical.get("robots_exists") and not technical.get("robots_allows"):
        issues.append("robots.txt blocks all crawlers")

    for strategy in _STRATEGIES:
        score = (pagespeed.get(strategy) or {}).get("performance_score")
        if isinstance(score, int) and score < 50:
            issues.append(f"Slow {strategy} page speed (PageSpeed score {score})")

    return issues


async def audit_seo(url: str, onpage_data: dict | None = None, api_key: str | None = None) -> dict:
    """Full SEO audit for a lead's site.

    Part 1: PageSpeed Insights, mobile + desktop (skipped to {} without a key).
    Part 2: on-page score 0-100 from onpage_data (audit_website() output).
    Part 3: robots.txt / sitemap.xml checks and an issues[] list.

    Never raises: PageSpeed and technical checks degrade to empty/default
    values on failure so a partial audit is always returned.
    """
    target = url.strip()
    if "://" not in target:
        target = f"https://{target}"
    domain = normalize_domain(target)

    if api_key:
        pagespeed = await _fetch_pagespeed(target, api_key)
    else:
        logger.info("audit_seo: no PageSpeed API key, skipping PageSpeed for %s", domain or url)
        pagespeed = {}

    technical = await _check_technical(domain)

    return {
        "pagespeed": pagespeed,
        "onpage_score": _onpage_score(onpage_data),
        "technical": technical,
        "issues": _build_issues(onpage_data, pagespeed, technical),
    }
