"""AI email generation — personalized cold outreach from audit data (PRD 3C).

The writer turns a lead's combined audit output into a single, specific cold
email. Two deterministic helpers do the framing so the language model only has
to write prose, never decide strategy:

- ``pick_top_gap`` reduces the whole audit to the ONE most addressable problem
  the agency can fix, in a fixed priority order (paid traffic landing on a weak
  site beats generic SEO, and so on). This forces one service matched to one
  real finding.
- ``build_email_context`` renders the audit sections the model is allowed to
  reference into a compact, human-readable block — including the enrichment
  ``social`` signals when present, which are the sharpest hooks available.

``generate_email`` fills the §11 Email Writing system prompt by verbatim string
substitution and calls the shared ``ai_completion`` entry point at temperature
0.7. Token usage is returned to the caller (like the other AI services) so it
can record it via ``record_ai_usage`` — this module never touches the DB.
"""
from services.ai.client import ai_completion

# The §11 Email Writing system prompt, copied verbatim. Placeholders are filled
# with str.replace (not str.format) so literal braces in audit text never break.
EMAIL_SYSTEM_PROMPT = """You are Ayesha, a Client Consultant at Trax9 — a web development, SEO and digital
marketing agency (Richmond, Texas / Karachi, Pakistan).

Trax9 services you may pitch:
{agency_services}

Write a personalized cold outreach email to {company_name}.

AUDIT FINDINGS ABOUT THEIR BUSINESS:
{full_audit_context}
TOP GAP (highest scoring issue): {top_gap}

RULES (follow all):
1. Pick the ONE Trax9 service that best solves {top_gap}. Lead with that.
2. Reference SPECIFIC audit findings. Never generic.
3. Consultative, value-first. Never "we can help you grow".
4. Exactly 3-4 short paragraphs.
5. Subject line: company/industry + a specific observation.
6. One concrete improvement suggestion from a real finding.
7. Low-friction CTA: "reply to this email" or "hop on a 15-min call".
8. Sign: "Ayesha | Client Consultant, Trax9".
9. NEVER use: "I hope this email finds you well", "I came across", "touching base",
   "I wanted to reach out", "I'm reaching out", "we specialize in".
10. Warm, expert, brief, specific — like a real person who did the research.

FORMAT:
Subject: [Subject line]

[Body]

Ayesha | Client Consultant, Trax9
"""

# --------------------------------------------------------------------------- thresholds
# Kept in step with services.audit.scoring so "weak site" means the same thing
# to the scorer and to the writer.

_ONPAGE_WEAK = 60          # onpage_score below this reads as a weak site
_MOBILE_SLOW = 50          # mobile PageSpeed performance below this reads as slow
_QUALITY_LOW = 4           # brand website_quality_score at/below this reads as weak
_BIG_AUDIENCE_FOLLOWERS = 10_000
_POOR_RATING = 3.5
_POOR_RATING_MIN_COUNT = 10

# Platforms that read as outdated / DIY relative to a custom build.
_DIY_PLATFORMS = frozenset({"wix", "squarespace", "godaddy", "weebly", "jimdo", "wordpress.com"})


def _num(value: object) -> float | None:
    """Value as float when it is a real number; None otherwise (bools excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _mobile_performance(seo: dict) -> float | None:
    """Mobile PageSpeed performance score (0-100) if PageSpeed ran, else None."""
    pagespeed = seo.get("pagespeed") or {}
    mobile = pagespeed.get("mobile") or {}
    return _num(mobile.get("performance_score"))


def _social_or_empty(audit: dict) -> dict:
    """The enrichment 'social' block, but only when it is a real success payload."""
    social = audit.get("social") or {}
    if not isinstance(social, dict) or social.get("error") or social.get("skipped"):
        return {}
    return social


def _is_weak_site(audit: dict) -> bool:
    """True when on-page SEO, mobile speed, or brand quality flags a weak site."""
    seo = audit.get("seo") or {}
    brand = audit.get("brand") or audit.get("brand_rnd") or {}

    onpage = _num(seo.get("onpage_score"))
    if onpage is not None and onpage < _ONPAGE_WEAK:
        return True

    mobile = _mobile_performance(seo)
    if mobile is not None and mobile < _MOBILE_SLOW:
        return True

    quality = _num(brand.get("website_quality_score"))
    if quality is not None and quality <= _QUALITY_LOW:
        return True

    return False


def pick_top_gap(audit: dict, agency_services: list) -> str:
    """Choose the single biggest, most addressable gap the agency can fix.

    Priority order (first match wins), per PRD 3C:
      1. Running paid ads (Meta or Google) onto a weak site/slow page -> the
         highest-intent gap: they already pay for traffic that lands badly.
         Pitch CRO / web development.
      2. Poor review rating (from the enrichment social block) -> reputation.
      3. Large social audience but a weak site -> a conversion gap.
      4. No schema / weak on-page SEO with no ads running -> organic SEO.
      5. Outdated or DIY website platform -> a rebuild.
      6. Fallback: a general web presence / conversion improvement.

    ``agency_services`` is accepted so the caller's real service list is in
    scope, but the returned value is a short human phrase naming the gap — the
    system prompt is what maps it to a specific service.
    """
    audit = audit or {}
    website = audit.get("website") or {}
    seo = audit.get("seo") or {}
    meta_ads = audit.get("meta_ads") or {}
    google_ads = audit.get("google_ads") or {}
    social = _social_or_empty(audit)

    running_ads = bool(meta_ads.get("has_ads")) or bool(google_ads.get("is_advertiser"))
    weak_site = _is_weak_site(audit)

    # 1. Paid traffic landing on a weak/slow site.
    if running_ads and weak_site:
        mobile = _mobile_performance(seo)
        if mobile is not None and mobile < _MOBILE_SLOW:
            return (
                f"paying for ads that land on a slow site "
                f"(mobile PageSpeed {int(mobile)}/100) — losing conversions"
            )
        return "paying for ads that land on a weak, low-converting site"

    # 2. Poor review rating.
    reviews = social.get("reviews") or {}
    rating = _num(reviews.get("rating"))
    review_count = int(_num(reviews.get("count")) or 0)
    if rating is not None and rating < _POOR_RATING and review_count >= _POOR_RATING_MIN_COUNT:
        platform = reviews.get("platform") or "review sites"
        return f"a weak online reputation ({rating:.1f} on {platform}) hurting trust"

    # 3. Large social audience but a weak site.
    primary = social.get("primary_social") or {}
    followers = _num(primary.get("followers")) or 0
    if followers > _BIG_AUDIENCE_FOLLOWERS and weak_site:
        return (
            f"a large social audience (~{int(followers):,} followers) but a site "
            f"that does not convert that attention"
        )

    # 4. No schema / weak SEO with no ads running.
    if not running_ads:
        onpage = _num(seo.get("onpage_score"))
        no_schema = not website.get("schema_types")
        if (onpage is not None and onpage < _ONPAGE_WEAK) or no_schema:
            if no_schema and onpage is not None and onpage < _ONPAGE_WEAK:
                return f"weak on-page SEO (score {int(onpage)}/100) with no schema markup"
            if no_schema:
                return "no schema markup and thin organic SEO, limiting search visibility"
            return f"weak on-page SEO (score {int(onpage)}/100) limiting organic traffic"

    # 5. Outdated / DIY platform.
    tech_stack = [t.lower() for t in website.get("tech_stack") or [] if isinstance(t, str)]
    diy = next((t for t in tech_stack if t in _DIY_PLATFORMS), None)
    if diy:
        return f"an outdated / DIY website platform ({diy}) holding back growth"

    # 6. Fallback.
    return "an under-optimized web presence with room to convert more visitors"


def _yes_no(value: object) -> str:
    """'yes' / 'no' for a truthy audit flag."""
    return "yes" if value else "no"


def _fmt_list(values: object, limit: int = 5) -> str:
    """Comma-join a list of stringy values, capped, else an empty string."""
    if not isinstance(values, (list, tuple)):
        return ""
    items = [str(v).strip() for v in values if isinstance(v, (str, int, float)) and str(v).strip()]
    return ", ".join(items[:limit])


def build_email_context(lead: dict, audit: dict) -> str:
    """Render the audit into a compact, model-facing context block (PRD 3C).

    Only sections that carry a real signal are included; the enrichment
    ``social`` block appears only when it succeeded. The output is plain text
    with labelled lines the model can quote specific findings from.
    """
    lead = lead or {}
    audit = audit or {}

    website = audit.get("website") or {}
    seo = audit.get("seo") or {}
    meta_ads = audit.get("meta_ads") or {}
    google_ads = audit.get("google_ads") or {}
    brand = audit.get("brand") or audit.get("brand_rnd") or {}
    social = _social_or_empty(audit)

    lines: list[str] = []

    # ---- identity --------------------------------------------------------
    company = (lead.get("company_name") or "").strip() or "Unknown"
    site = (lead.get("website") or website.get("fetched_url") or "").strip()
    industry = (brand.get("industry") or lead.get("category") or "").strip()
    tech = _fmt_list(website.get("tech_stack"), limit=8)

    lines.append(f"Company: {company}")
    if site:
        lines.append(f"Website: {site}")
    if industry:
        lines.append(f"Industry: {industry}")
    if tech:
        lines.append(f"Tech Stack: {tech}")

    # ---- website audit ---------------------------------------------------
    audit_lines: list[str] = []
    performance = _mobile_performance(seo)
    if performance is not None:
        audit_lines.append(f"  Mobile PageSpeed performance: {int(performance)}/100")
    onpage = _num(seo.get("onpage_score"))
    if onpage is not None:
        audit_lines.append(f"  On-page SEO score: {int(onpage)}/100")
    if website:
        audit_lines.append(f"  Has schema markup: {_yes_no(website.get('schema_types'))}")
        audit_lines.append(f"  Mobile friendly (viewport): {_yes_no(website.get('viewport'))}")
        audit_lines.append(f"  Has contact page: {_yes_no(website.get('contact_page'))}")
        social_links = website.get("social_links") or {}
        has_social = any(social_links.get(k) for k in ("facebook", "instagram", "linkedin", "twitter"))
        audit_lines.append(f"  Social media linked from site: {_yes_no(has_social)}")
        if website.get("ssl_valid") is False:
            audit_lines.append("  Valid HTTPS: no")
    if audit_lines:
        lines.append("")
        lines.append("Website Audit:")
        lines.extend(audit_lines)

    # ---- SEO issues ------------------------------------------------------
    issues = seo.get("issues")
    issue_items = [str(i).strip() for i in issues or [] if isinstance(i, str) and i.strip()]
    if issue_items:
        lines.append("")
        lines.append("SEO Issues:")
        lines.extend(f"  - {issue}" for issue in issue_items[:6])

    # ---- ad activity -----------------------------------------------------
    if meta_ads.get("has_ads"):
        detail = f"running Meta ads ({int(meta_ads.get('total_active_ads') or 0)} active)"
        strategies = _fmt_list(meta_ads.get("ad_strategies"), limit=3)
        if strategies:
            detail += f"; strategy: {strategies}"
        lines.append("")
        lines.append(f"Meta Ads: {detail}")
    if google_ads.get("is_advertiser"):
        formats = _fmt_list(google_ads.get("formats"), limit=4)
        detail = f"active Google advertiser ({int(google_ads.get('total_ads') or 0)} ads)"
        if formats:
            detail += f"; formats: {formats}"
        lines.append("")
        lines.append(f"Google Ads: {detail}")

    # ---- social / review enrichment (only when present) ------------------
    if social:
        social_lines: list[str] = []
        reviews = social.get("reviews") or {}
        rating = _num(reviews.get("rating"))
        if rating is not None:
            platform = reviews.get("platform") or "reviews"
            count = int(_num(reviews.get("count")) or 0)
            social_lines.append(f"  {platform} rating: {rating:.1f} from {count} reviews")
        linkedin = social.get("linkedin") or {}
        employees = _num(linkedin.get("employees"))
        if employees is not None:
            social_lines.append(f"  LinkedIn company size: ~{int(employees)} employees")
        primary = social.get("primary_social") or {}
        followers = _num(primary.get("followers"))
        if followers is not None and followers > 0:
            platform = primary.get("platform") or "primary social"
            social_lines.append(f"  {platform} followers: {int(followers):,}")
        signal_items = [s.strip() for s in social.get("signals") or [] if isinstance(s, str) and s.strip()]
        if signal_items:
            social_lines.append(f"  Derived signals: {'; '.join(signal_items[:4])}")
        if social_lines:
            lines.append("")
            lines.append("Social:")
            lines.extend(social_lines)

    # ---- brand analysis --------------------------------------------------
    brand_lines: list[str] = []
    audience = (brand.get("target_audience") or "").strip()
    if audience:
        brand_lines.append(f"  Target audience: {audience}")
    positioning = (brand.get("brand_positioning") or brand.get("positioning") or "").strip()
    if positioning:
        brand_lines.append(f"  Positioning: {positioning}")
    pain_points = _fmt_list(brand.get("pain_points"), limit=4)
    if pain_points:
        brand_lines.append(f"  Pain points: {pain_points}")
    recommended = _fmt_list(brand.get("best_services"), limit=4)
    if recommended:
        brand_lines.append(f"  Recommended services: {recommended}")
    if brand_lines:
        lines.append("")
        lines.append("Brand Analysis:")
        lines.extend(brand_lines)

    return "\n".join(lines).strip()


def _format_agency_services(agency_services: list) -> str:
    """Render the agency's services as a bullet list for the prompt.

    Accepts the ``agency_profiles.services`` shape ([{name, description}]) and
    also tolerates a plain list of strings.
    """
    if not isinstance(agency_services, (list, tuple)) or not agency_services:
        return "- General web development, SEO, and digital marketing"

    bullets: list[str] = []
    for service in agency_services:
        if isinstance(service, dict):
            name = str(service.get("name") or "").strip()
            description = str(service.get("description") or "").strip()
            if name and description:
                bullets.append(f"- {name}: {description}")
            elif name:
                bullets.append(f"- {name}")
        elif isinstance(service, str) and service.strip():
            bullets.append(f"- {service.strip()}")

    return "\n".join(bullets) if bullets else "- General web development, SEO, and digital marketing"


def _parse_email(text: str) -> dict:
    """Split the model output into {'subject', 'body'}.

    The first non-empty line is the subject (with a leading 'Subject:' removed);
    everything after it is the body. If no subject line is found the whole text
    becomes the body with an empty subject, so the caller can decide how to
    handle it.
    """
    stripped = (text or "").strip()
    if not stripped:
        return {"subject": "", "body": ""}

    lines = stripped.splitlines()
    # Skip any leading blank lines to find the subject line.
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return {"subject": "", "body": ""}

    first = lines[index].strip()
    lowered = first.lower()
    if lowered.startswith("subject:"):
        subject = first[len("subject:"):].strip()
    else:
        subject = first
    body = "\n".join(lines[index + 1:]).strip()
    return {"subject": subject, "body": body}


async def generate_email(
    lead: dict,
    audit: dict,
    agency_services: list,
    *,
    provider: str,
    api_key: str,
) -> tuple[dict, int]:
    """Generate a personalized cold email for a lead from its audit.

    Steps (PRD 3C):
      1. Build the model-facing audit context.
      2. Pick the single top gap the agency can address.
      3. Fill the §11 Email Writing system prompt by verbatim substitution and
         call ``ai_completion`` at temperature 0.7.
      4. Parse the first line as the subject (stripping 'Subject:'), the rest as
         the body.

    Returns ``({"subject": str, "body": str}, total_tokens)``. Token usage is
    returned to the caller to record via ``record_ai_usage`` — this function
    performs no DB writes. Propagates ``ai_completion`` errors (bad provider or
    key -> ValueError; HTTP / timeout -> httpx exceptions) for the caller to
    catch.
    """
    lead = lead or {}
    audit = audit or {}

    context = build_email_context(lead, audit)
    top_gap = pick_top_gap(audit, agency_services)
    company_name = (lead.get("company_name") or "").strip() or "your business"
    services_block = _format_agency_services(agency_services)

    prompt = (
        EMAIL_SYSTEM_PROMPT
        .replace("{agency_services}", services_block)
        .replace("{company_name}", company_name)
        .replace("{full_audit_context}", context)
        .replace("{top_gap}", top_gap)
    )

    text, tokens = await ai_completion(
        prompt,
        provider=provider,
        api_key=api_key,
        temperature=0.7,
    )

    return _parse_email(text), tokens
