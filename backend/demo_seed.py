"""LeadForge AI — client-demo simulator (local dev tool, no app changes).

Drives the real dev database through the full agent pipeline on a timer so the
dashboard shows agents working live — no external API keys needed.

Usage (from backend/, venv python):
    python demo_seed.py            # create demo user + agency profile + campaign
    python demo_seed.py --live     # run the ~4-minute live pipeline simulation
    python demo_seed.py --reset    # wipe ALL demo data -> original condition

Login for the recording:  demo@trax9.com / demo12345
"""
import argparse
import random
import time
from datetime import datetime, timedelta

from database import SessionLocal
from models import (
    AgencyProfile, Campaign, CampaignStatus, EmailLog, EmailLogStatus, EmailSource,
    Lead, LeadStatus, SequenceStep, Suppression, Task, TaskStatus, TaskType, User,
    UserSettings,
)
from services.auth_service import hash_password

DEMO_EMAIL = "demo@trax9.com"
DEMO_PASSWORD = "demo12345"

COMPANIES = [
    ("Bayou Wear", "bayouwear.com", "Houston", "clothing_store"),
    ("Acme Threads", "acmethreads.com", "Houston", "clothing_store"),
    ("Lone Star Apparel", "lonestarapparel.com", "Austin", "clothing_store"),
    ("Gulf Coast Goods", "gulfcoastgoods.com", "Houston", "home_goods_store"),
    ("Space City Style", "spacecitystyle.com", "Houston", "clothing_store"),
    ("Hill Country Boots", "hillcountryboots.com", "Austin", "shoe_store"),
    ("Bluebonnet Beauty", "bluebonnetbeauty.com", "Dallas", "beauty_salon"),
    ("Rio Grande Roasters", "riograndecoffee.com", "Austin", "cafe"),
    ("Texan Trail Gear", "texantrailgear.com", "Dallas", "sporting_goods"),
    ("Magnolia Home Decor", "magnoliahomedecor.com", "Houston", "furniture_store"),
    ("Cedar Creek Candles", "cedarcreekcandles.com", "Austin", "store"),
    ("Alamo Athletics", "alamoathletics.com", "San Antonio", "sporting_goods"),
]

TECHS = [["shopify", "jquery"], ["wordpress", "woocommerce"], ["wix"], ["shopify"], ["squarespace"], ["wordpress"]]


def audit_blob(name: str, domain: str, weak: bool) -> dict:
    perf = random.randint(22, 44) if weak else random.randint(58, 78)
    onpage = random.randint(30, 55) if weak else random.randint(62, 84)
    tech = random.choice(TECHS)
    brand = {
        "industry": "ecommerce - retail",
        "target_audience": "young, style-conscious local shoppers",
        "brand_positioning": f"{name} sells local-identity products with a friendly, casual voice.",
        "website_quality_score": 4 if weak else 7,
        "strengths": ["distinct brand identity", "active Instagram presence", "quality product photos"],
        "weaknesses": ["slow mobile load", "no schema markup", "thin category descriptions"],
        "estimated_size": "small",
        "pain_points": ["paid traffic lands on a slow page", "poor organic visibility"],
        "best_services": ["Web Development", "SEO"],
    }
    blob = {
        "website": {
            "tech_stack": tech,
            "title": {"exists": True, "length": 48, "text": f"{name} | Shop Online"},
            "meta_description": {"exists": not weak, "length": 0 if weak else 142},
            "og_tags": {} if weak else {"og:title": name},
            "h1": {"exists": True, "count": 1},
            "images": {"total": 42, "with_alt": 12 if weak else 36, "alt_ratio": 0.29 if weak else 0.86},
            "schema_types": [] if weak else ["Product", "Organization"],
            "favicon": True, "viewport": True, "canonical": not weak, "html_lang": "en",
            "load_time_ms": 4200 if weak else 1400,
            "page_size_kb": 3800.5 if weak else 1250.2,
            "resource_count": 96 if weak else 41,
            "ssl_valid": True, "hsts": not weak, "x_frame_options": False,
            "emails": [f"info@{domain}"], "phones": ["+1 713-555-0142"],
            "contact_page": f"https://{domain}/contact",
            "social_links": {"facebook": f"https://facebook.com/{name.split()[0].lower()}", "instagram": f"https://instagram.com/{name.split()[0].lower()}", "linkedin": None, "twitter": None},
        },
        "seo": {
            "onpage_score": onpage,
            "pagespeed": {
                "mobile": {"performance_score": perf, "accessibility_score": perf + 20, "best_practices_score": perf + 25, "seo_score": onpage + 8, "lcp": 6.2 if weak else 2.1, "fcp": 3.4 if weak else 1.2, "cls": 0.31 if weak else 0.05, "tbt": 890 if weak else 120},
                "desktop": {"performance_score": perf + 25, "accessibility_score": perf + 28, "best_practices_score": perf + 30, "seo_score": onpage + 10, "lcp": 2.8 if weak else 1.1, "fcp": 1.4 if weak else 0.6, "cls": 0.12 if weak else 0.02, "tbt": 240 if weak else 40},
            },
            "technical": {"robots_exists": True, "robots_allows": True, "sitemap_exists": not weak, "sitemap_urls": 0 if weak else 148},
            "issues": (["Missing meta description", "No schema markup", "Slow mobile page speed", "68% of images missing alt text"] if weak else ["Minor: canonical missing on 3 pages"]),
        },
        "meta_ads": {
            "has_ads": weak, "total_active_ads": random.randint(4, 11) if weak else 0,
            "sample_ads": ([{"headline": "Summer Drop is LIVE", "primary_text": "Fresh styles just landed. 20% off this week only.", "cta": "Shop Now", "media_type": "image", "landing_page": f"https://{domain}/collections/new", "start_date": "2026-06-28"}] if weak else []),
            "ad_strategies": ["discount-focused"] if weak else [],
            "ad_library_url": f"https://www.facebook.com/ads/library/?q={name.replace(' ', '%20')}",
        },
        "google_ads": {"is_advertiser": weak and random.random() > 0.5, "total_ads": random.randint(2, 6) if weak else 0, "formats": ["text", "image"] if weak else [], "sample_ads": [], "regions": ["United States"] if weak else [], "transparency_url": f"https://adstransparency.google.com/?query={domain}"},
        "brand": brand, "brand_rnd": brand,
    }
    return blob


def social_blob(name: str) -> dict:
    return {
        "reviews": {"platform": "trustpilot", "rating": round(random.uniform(3.0, 4.6), 1), "count": random.randint(40, 320), "url": "https://trustpilot.com"},
        "linkedin": {"employees": random.randint(4, 28), "industry": "Retail", "followers": random.randint(150, 2200)},
        "primary_social": {"platform": "instagram", "handle": f"@{name.split()[0].lower()}", "followers": random.randint(8000, 46000), "engagement_rate": round(random.uniform(1.2, 4.8), 2)},
        "signals": ["large audience, weak site", "running ads onto a slow landing page"],
        "credits_used": 3,
    }


def email_copy(name: str) -> tuple[str, str]:
    first = name.split()[0]
    return (
        f"{name}'s ads deserve a faster landing page",
        f"Hi there,\n\nI ran a quick audit of {name.lower().replace(' ', '')}.com while researching {first} — "
        "you're actively running Meta ads (nice creative, by the way), but the mobile page they land on "
        "scores 34/100 on Google PageSpeed and takes over 4 seconds to load. That combination usually "
        "means paid clicks bouncing before they ever see a product.\n\n"
        "The fix is narrow: compress the hero media, defer two render-blocking scripts, and add the "
        "product schema that's currently missing. Sites we've tuned this way typically recover 15-25% "
        "of paid traffic that was silently bouncing.\n\n"
        "Want me to send over the full audit? Just reply, or grab 15 minutes with me if easier.\n\n"
        "Ayesha | Client Consultant, Trax9",
    )


# ------------------------------------------------------------------ setup / reset

def get_or_create_user(db) -> User:
    u = db.query(User).filter(User.email == DEMO_EMAIL).first()
    if u is None:
        u = User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD), name="Ayesha (Trax9)")
        db.add(u)
        db.flush()
        db.add(UserSettings(user_id=u.id))
        db.commit()
        print(f"created user {DEMO_EMAIL} / {DEMO_PASSWORD}")
    return u


def setup(db) -> Campaign:
    u = get_or_create_user(db)
    profile = db.query(AgencyProfile).filter(AgencyProfile.user_id == u.id).first()
    if profile is None:
        profile = AgencyProfile(
            user_id=u.id, website="trax9.com", company_name="Trax9",
            services=[
                {"name": "Web Development", "description": "High-converting, fast custom sites"},
                {"name": "SEO", "description": "Technical + on-page organic growth"},
                {"name": "Digital Marketing", "description": "Paid social and search that converts"},
            ],
            ideal_client={"industries": ["ecommerce - retail", "local services"], "company_size": "small-medium", "geos": ["Texas"], "buying_signals": ["runs ads but weak site", "poor mobile speed"]},
            suggested_keywords=["clothing brand", "boutique store", "home decor shop"],
            suggested_locations=["Houston", "Austin", "Dallas"],
            positioning="Full-stack digital agency for ambitious SMBs — Richmond, TX & Karachi.",
        )
        db.add(profile)
        db.commit()
        print("created agency profile (Trax9 brain)")
    camp = db.query(Campaign).filter(Campaign.user_id == u.id, Campaign.name == "Texas Ecommerce Sweep").first()
    if camp is None:
        camp = Campaign(user_id=u.id, agency_profile_id=profile.id, name="Texas Ecommerce Sweep",
                        seed_keywords=["clothing brand", "boutique store"], target_locations=["Houston", "Austin", "Dallas"],
                        status=CampaignStatus.running)
        db.add(camp)
        db.commit()
        print(f"created campaign 'Texas Ecommerce Sweep' -> /campaigns/{camp.id}")
    return camp


def reset(db) -> None:
    u = db.query(User).filter(User.email == DEMO_EMAIL).first()
    if u is None:
        print("nothing to reset")
        return
    ids = [c.id for c in db.query(Campaign).filter(Campaign.user_id == u.id)]
    n = 0
    for model in (SequenceStep, EmailLog, Suppression):
        n += db.query(model).filter(model.user_id == u.id).delete(synchronize_session=False)
    n += db.query(Lead).filter(Lead.user_id == u.id).delete(synchronize_session=False)
    n += db.query(Task).filter(Task.user_id == u.id).delete(synchronize_session=False)
    n += db.query(Campaign).filter(Campaign.user_id == u.id).delete(synchronize_session=False)
    n += db.query(AgencyProfile).filter(AgencyProfile.user_id == u.id).delete(synchronize_session=False)
    n += db.query(UserSettings).filter(UserSettings.user_id == u.id).delete(synchronize_session=False)
    db.delete(u)
    db.commit()
    print(f"reset complete — removed demo user + {n} rows. Original condition restored.")


# ------------------------------------------------------------------ live simulation

def live(db) -> None:
    camp = setup(db)
    u = db.query(User).filter(User.email == DEMO_EMAIL).first()
    print("\n=== LIVE SIMULATION (~4 min). Open the campaign page now and hit record. ===\n")
    time.sleep(6)

    # ---- 1. DISCOVERY (~40s)
    t = Task(user_id=u.id, campaign_id=camp.id, type=TaskType.discovery, status=TaskStatus.running, total_items=len(COMPANIES))
    db.add(t); db.commit()
    print("[discovery] agent deployed")
    leads = []
    for i, (name, domain, city, cat) in enumerate(COMPANIES):
        lead = Lead(user_id=u.id, campaign_id=camp.id, company_name=name, website=domain,
                    city=city, country="United States", category=cat,
                    source=random.choice(["google_maps", "google_search"]), status=LeadStatus.discovered)
        db.add(lead); leads.append(lead)
        t.completed_items = i + 1
        db.commit()
        print(f"[discovery] found {name} ({domain})")
        time.sleep(random.uniform(2.0, 3.5))
    t.status = TaskStatus.completed; db.commit()
    print("[discovery] complete: 12 leads\n")

    # ---- 2. EMAIL FINDING (~30s)
    print("[email] agent hunting inboxes")
    for lead in leads:
        lead.status = LeadStatus.finding_email; db.commit()
        time.sleep(random.uniform(1.2, 2.2))
        if random.random() > 0.15:
            lead.email = f"info@{lead.website}"
            lead.email_source = EmailSource.scraped
            lead.email_confidence = random.randint(82, 95)
        lead.status = LeadStatus.discovered; db.commit()
    print("[email] 10/12 inboxes found\n")

    # ---- 3. AUDIT + SCORE (~90s)
    t = Task(user_id=u.id, campaign_id=camp.id, type=TaskType.audit, status=TaskStatus.running, total_items=len(leads))
    db.add(t); db.commit()
    print("[audit] agent x-raying sites")
    for i, lead in enumerate(leads):
        lead.status = LeadStatus.auditing; db.commit()
        time.sleep(random.uniform(3.0, 5.0))
        weak = i % 3 != 2  # two thirds are juicy prospects
        lead.audit_data = audit_blob(lead.company_name, lead.website, weak)
        lead.status = LeadStatus.audited; db.commit()
        time.sleep(0.8)
        base = random.randint(62, 88) if weak else random.randint(30, 48)
        if not lead.email:
            base -= 15
        lead.fit_score = max(5, min(97, base))
        lead.score_reasons = [
            {"factor": "baseline", "points": 50},
            {"factor": "running Meta ads — budget exists", "points": 10 if weak else 0},
            {"factor": "weak on-page SEO — needs the work", "points": 15 if weak else -5},
            {"factor": "slow mobile PageSpeed", "points": 10 if weak else 0},
            {"factor": "contact email found", "points": 10 if lead.email else -15},
        ]
        lead.status = LeadStatus.scored
        t.completed_items = i + 1
        db.commit()
        print(f"[audit] {lead.company_name}: fit {lead.fit_score}/100")
    t.status = TaskStatus.completed; db.commit()
    print("[audit] complete\n")

    # ---- 4. ENRICH top scorers (~25s)
    hot = sorted([l for l in leads if (l.fit_score or 0) >= 60], key=lambda x: -(x.fit_score or 0))[:5]
    print("[enrich] SocialCrawl on top", len(hot))
    for lead in hot:
        lead.status = LeadStatus.enriching; db.commit()
        time.sleep(random.uniform(2.0, 3.5))
        blob = dict(lead.audit_data or {}); blob["social"] = social_blob(lead.company_name)
        lead.audit_data = blob
        lead.fit_score = min(98, (lead.fit_score or 60) + random.randint(2, 7))
        lead.status = LeadStatus.enriched; db.commit()
        print(f"[enrich] {lead.company_name} -> {lead.fit_score}/100")
    print("[enrich] done\n")

    # ---- 5. WRITE (~30s)
    print("[write] AI drafting openers")
    for lead in hot:
        lead.status = LeadStatus.writing; db.commit()
        time.sleep(random.uniform(3.0, 4.5))
        subj, body = email_copy(lead.company_name)
        lead.email_subject, lead.email_body = subj, body
        lead.status = LeadStatus.written; db.commit()
        print(f"[write] {lead.company_name}: \"{subj}\"")
    print("[write] done\n")

    # ---- 6. SEND + TRACK (~35s)
    t = Task(user_id=u.id, campaign_id=camp.id, type=TaskType.send, status=TaskStatus.running, total_items=len(hot))
    db.add(t); db.commit()
    print("[send] launching")
    now = datetime.utcnow()
    for i, lead in enumerate(hot):
        lead.status = LeadStatus.sending; db.commit()
        time.sleep(random.uniform(2.0, 3.0))
        lead.status = LeadStatus.sent; lead.sent_at = datetime.utcnow()
        db.add(EmailLog(user_id=u.id, lead_id=lead.id, campaign_id=camp.id,
                        message_id=f"demo-{lead.id.hex[:10]}", from_email="ayesha@trax9.com",
                        to_email=lead.email, subject=lead.email_subject,
                        status=EmailLogStatus.sent, sent_at=lead.sent_at))
        for step, days in ((1, 3), (2, 7)):
            db.add(SequenceStep(lead_id=lead.id, user_id=u.id, step_number=step, scheduled_for=now + timedelta(days=days)))
        t.completed_items = i + 1; db.commit()
        print(f"[send] {lead.company_name} -> sent")
    t.status = TaskStatus.completed; db.commit()
    time.sleep(4)
    # opens + a reply
    for lead in hot[:3]:
        lead.status = LeadStatus.opened; lead.opened_at = datetime.utcnow(); db.commit()
        print(f"[track] {lead.company_name} OPENED"); time.sleep(2.5)
    star = hot[0]
    star.status = LeadStatus.replied; star.replied_at = datetime.utcnow()
    db.query(SequenceStep).filter(SequenceStep.lead_id == star.id).update({SequenceStep.status: "cancelled"})
    db.commit()
    print(f"[track] {star.company_name} REPLIED — follow-ups auto-cancelled")
    print("\n=== SIMULATION COMPLETE. Stop recording. `python demo_seed.py --reset` restores original state. ===")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    p.add_argument("--reset", action="store_true")
    args = p.parse_args()
    db = SessionLocal()
    try:
        if args.reset:
            reset(db)
        elif args.live:
            live(db)
        else:
            setup(db)
    finally:
        db.close()
