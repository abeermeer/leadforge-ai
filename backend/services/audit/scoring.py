"""Lead fit scoring — deterministic 0-100 heuristic after audit (PRD 3B.5).

Higher score = better prospect for THIS agency. Starts from a neutral base and
adds/subtracts weighted factors read from the audit output:

+  Website weak (low PageSpeed, no schema, poor on-page SEO) -> they NEED help
+  Already running Meta/Google ads                           -> budget + intent
+  Industry matches agency ideal_client.industries           -> fit
+  Has a real business email found                           -> reachable
+  Tech stack matches agency capability (e.g. Shopify)       -> fast to help
-  Enterprise / already high-quality site                    -> unlikely to convert
-  No email found                                            -> hard to reach

When audit_data['social'] is present (after enrich_social) it is folded in:
big audience + weak site scores up (prime CRO lead); excellent reviews on an
already-strong site scores down. Re-running score_lead after enrichment is the
PRD 3B.6 feedback loop.

Pure and deterministic: no I/O, no randomness, inputs are never mutated.
Callers persist the result (lead.fit_score, lead.score_reasons, status='scored').
"""

# --------------------------------------------------------------------------- weights

_BASE_SCORE = 50

_W_WEAK_ONPAGE_SEVERE = 15   # onpage_score < 40
_W_WEAK_ONPAGE = 8           # onpage_score < 60
_W_STRONG_ONPAGE = -8        # onpage_score >= 85
_W_SLOW_MOBILE = 10          # mobile PageSpeed < 50
_W_FAST_MOBILE = -5          # mobile PageSpeed >= 90
_W_NO_SCHEMA = 5
_W_NO_SSL = 5
_W_META_ADS = 10
_W_GOOGLE_ADS = 10
_W_INDUSTRY_MATCH = 10
_W_HAS_EMAIL = 10
_W_NO_EMAIL = -15
_W_TECH_IDEAL_MATCH = 8      # detected tech named in the agency's ideal-client text
_W_TECH_SERVICEABLE = 5      # detected tech is a platform agencies commonly service
_W_ENTERPRISE = -15
_W_HIGH_QUALITY_SITE = -8    # brand website_quality_score >= 8
_W_LOW_QUALITY_SITE = 8      # brand website_quality_score <= 3
_W_BIG_AUDIENCE_WEAK_SITE = 12
_W_POOR_REVIEWS = 6
_W_GREAT_REVIEWS_STRONG_SITE = -10
_W_LARGE_HEADCOUNT = -10

# --------------------------------------------------------------------------- thresholds

_ONPAGE_SEVERE = 40
_ONPAGE_WEAK = 60
_ONPAGE_STRONG = 85
_ONPAGE_WEAK_FOR_SOCIAL = 50   # "weak site" bar used with social signals
_ONPAGE_STRONG_FOR_SOCIAL = 80
_MOBILE_SLOW = 50
_MOBILE_FAST = 90
_QUALITY_HIGH = 8              # brand website_quality_score (1-10)
_QUALITY_LOW = 3
_BIG_AUDIENCE_FOLLOWERS = 10_000
_POOR_RATING = 3.5
_GREAT_RATING = 4.5
_GREAT_RATING_MIN_COUNT = 50
_POOR_RATING_MIN_COUNT = 10
_LARGE_HEADCOUNT = 200

# Platforms most agencies can service quickly (subset of website.py's
# TECH_STACK_PATTERNS keys — frameworks like react/jquery are not a fit signal).
_SERVICEABLE_PLATFORMS = frozenset(
    {"shopify", "wordpress", "woocommerce", "magento", "wix", "webflow", "squarespace"}
)


def _num(value: object) -> float | None:
    """Value as float when it is a real number; None otherwise (bools excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _industry_match(ideal_industries: list, candidates: list[str]) -> str | None:
    """First ideal-client industry that substring-matches a lead industry hint.

    Match is case-insensitive and bidirectional so 'restaurant' pairs with
    'local services - restaurant' regardless of which side is more specific.
    """
    for industry in ideal_industries:
        if not isinstance(industry, str):
            continue
        industry_l = industry.strip().lower()
        if not industry_l:
            continue
        for candidate in candidates:
            candidate_l = candidate.strip().lower()
            if candidate_l and (industry_l in candidate_l or candidate_l in industry_l):
                return industry.strip()
    return None


def score_lead(lead: dict, audit_data: dict, ideal_client: dict | None) -> dict:
    """Compute the lead's fit score from audit findings.

    lead: lead dict (uses 'email' and 'category').
    audit_data: combined audit output — reads 'website', 'seo', 'meta_ads',
        'google_ads', 'brand' (fallback 'brand_rnd') and, when present after
        enrichment, 'social'. Missing sections simply contribute no points.
    ideal_client: agency_profiles.ideal_client (industries[], company_size,
        buying_signals[]) or None when no agency profile exists.

    Returns {"fit_score": int 0-100, "score_reasons": [{"factor": str,
    "points": int}]} where the reasons list every contributing factor
    (including the baseline) so the dashboard can explain the number.
    """
    lead = lead or {}
    audit_data = audit_data or {}
    ideal_client = ideal_client or {}

    website = audit_data.get("website") or {}
    seo = audit_data.get("seo") or {}
    meta_ads = audit_data.get("meta_ads") or {}
    google_ads = audit_data.get("google_ads") or {}
    brand = audit_data.get("brand") or audit_data.get("brand_rnd") or {}
    social = audit_data.get("social") or {}
    if not isinstance(social, dict) or social.get("error") or social.get("skipped"):
        social = {}

    score = _BASE_SCORE
    reasons: list[dict] = [{"factor": "baseline", "points": _BASE_SCORE}]

    def _add(factor: str, points: int) -> None:
        nonlocal score
        score += points
        reasons.append({"factor": factor, "points": points})

    # ---- website weakness / strength -------------------------------------
    onpage = _num(seo.get("onpage_score"))
    if onpage is not None:
        if onpage < _ONPAGE_SEVERE:
            _add(f"very weak on-page SEO (score {int(onpage)})", _W_WEAK_ONPAGE_SEVERE)
        elif onpage < _ONPAGE_WEAK:
            _add(f"weak on-page SEO (score {int(onpage)})", _W_WEAK_ONPAGE)
        elif onpage >= _ONPAGE_STRONG:
            _add(f"already strong on-page SEO (score {int(onpage)})", _W_STRONG_ONPAGE)

    mobile_perf = _num(((seo.get("pagespeed") or {}).get("mobile") or {}).get("performance_score"))
    if mobile_perf is not None:
        if mobile_perf < _MOBILE_SLOW:
            _add(f"slow mobile site (PageSpeed {int(mobile_perf)})", _W_SLOW_MOBILE)
        elif mobile_perf >= _MOBILE_FAST:
            _add(f"fast mobile site (PageSpeed {int(mobile_perf)})", _W_FAST_MOBILE)

    if website:
        if not website.get("schema_types"):
            _add("no schema markup", _W_NO_SCHEMA)
        if website.get("ssl_valid") is False:
            _add("no valid HTTPS", _W_NO_SSL)

    # ---- ad activity: budget + intent ------------------------------------
    if meta_ads.get("has_ads"):
        _add("running Meta ads (budget + intent)", _W_META_ADS)
    if google_ads.get("is_advertiser"):
        _add("running Google ads (budget + intent)", _W_GOOGLE_ADS)

    # ---- industry fit vs the agency's ideal client -----------------------
    ideal_industries = ideal_client.get("industries") or []
    candidates = [
        value for value in (lead.get("category"), brand.get("industry"))
        if isinstance(value, str) and value.strip()
    ]
    if isinstance(ideal_industries, list) and candidates:
        matched = _industry_match(ideal_industries, candidates)
        if matched:
            _add(f"industry matches ideal client ({matched})", _W_INDUSTRY_MATCH)

    # ---- reachability ----------------------------------------------------
    email = lead.get("email")
    if isinstance(email, str) and email.strip():
        _add("business email found", _W_HAS_EMAIL)
    else:
        _add("no email found", _W_NO_EMAIL)

    # ---- tech stack vs agency capability ---------------------------------
    tech_stack = [t.lower() for t in website.get("tech_stack") or [] if isinstance(t, str)]
    if tech_stack:
        ideal_text_parts: list[str] = []
        for key in ("industries", "buying_signals"):
            values = ideal_client.get(key) or []
            if isinstance(values, list):
                ideal_text_parts.extend(v for v in values if isinstance(v, str))
        company_size = ideal_client.get("company_size")
        if isinstance(company_size, str):
            ideal_text_parts.append(company_size)
        ideal_text = " ".join(ideal_text_parts).lower()

        ideal_hits = [t for t in tech_stack if t and t in ideal_text]
        serviceable_hits = [t for t in tech_stack if t in _SERVICEABLE_PLATFORMS]
        if ideal_hits:
            _add(f"tech stack matches agency focus ({ideal_hits[0]})", _W_TECH_IDEAL_MATCH)
        elif serviceable_hits:
            _add(f"serviceable platform ({serviceable_hits[0]})", _W_TECH_SERVICEABLE)

    # ---- enterprise / already high-quality -------------------------------
    estimated_size = str(brand.get("estimated_size") or "").lower()
    if "large" in estimated_size or "enterprise" in estimated_size:
        _add("enterprise-sized business", _W_ENTERPRISE)

    quality = _num(brand.get("website_quality_score"))
    if quality is not None:
        if quality >= _QUALITY_HIGH:
            _add(f"already high-quality website ({int(quality)}/10)", _W_HIGH_QUALITY_SITE)
        elif quality <= _QUALITY_LOW:
            _add(f"very low website quality ({int(quality)}/10)", _W_LOW_QUALITY_SITE)

    # ---- social / review signals (post-enrichment re-score) --------------
    if social:
        weak_site = (onpage is not None and onpage < _ONPAGE_WEAK_FOR_SOCIAL) or (
            quality is not None and quality <= _QUALITY_LOW + 1
        )
        strong_site = (onpage is not None and onpage >= _ONPAGE_STRONG_FOR_SOCIAL) or (
            quality is not None and quality >= _QUALITY_HIGH
        )

        primary = social.get("primary_social") or {}
        followers = _num(primary.get("followers")) or 0
        if followers > _BIG_AUDIENCE_FOLLOWERS and weak_site:
            _add(
                f"large social audience ({int(followers):,} followers) but weak site",
                _W_BIG_AUDIENCE_WEAK_SITE,
            )

        reviews = social.get("reviews") or {}
        rating = _num(reviews.get("rating"))
        review_count = int(_num(reviews.get("count")) or 0)
        if rating is not None:
            platform = reviews.get("platform") or "review site"
            if rating < _POOR_RATING and review_count >= _POOR_RATING_MIN_COUNT:
                _add(f"poor {platform} rating ({rating:.1f})", _W_POOR_REVIEWS)
            elif (
                rating >= _GREAT_RATING
                and review_count >= _GREAT_RATING_MIN_COUNT
                and strong_site
            ):
                _add(
                    f"excellent {platform} reviews ({rating:.1f}) on an already-strong site",
                    _W_GREAT_REVIEWS_STRONG_SITE,
                )

        employees = _num((social.get("linkedin") or {}).get("employees"))
        if employees is not None and employees > _LARGE_HEADCOUNT:
            _add(f"large headcount (~{int(employees)} employees)", _W_LARGE_HEADCOUNT)

    return {"fit_score": max(0, min(100, int(score))), "score_reasons": reasons}
