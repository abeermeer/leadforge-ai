"""One-time: capture a logged-in browser session for ad-library scraping.

Meta's Ads Library and Google's Ads Transparency Center now require an
authenticated session to render ads. Run this once:

    cd backend
    .venv/Scripts/python capture_session.py

A real Chrome window opens. Log into Facebook (and optionally Google) in it,
browse to https://www.facebook.com/ads/library/ to confirm ads load, then press
Enter in the terminal. The session is saved to scraper_session.json and used by
every scraper afterward (set SCRAPER_STORAGE_STATE=scraper_session.json in .env).

Gitignored — it holds your login cookies. Never commit it.

Use a throwaway / dedicated Facebook account, not your personal one — automated
access is against Meta's ToS and can get an account flagged.
"""
import asyncio
import os

OUTPUT = os.path.join(os.path.dirname(__file__), "scraper_session.json")


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.facebook.com/login")
        print("\n=== A browser window opened ===")
        print("1. Log into Facebook (use a dedicated/throwaway account).")
        print("2. Optionally open https://adstransparency.google.com and sign into Google.")
        print("3. Visit https://www.facebook.com/ads/library/ and confirm ads render.")
        input("\nWhen done, come back here and press Enter to save the session... ")
        await context.storage_state(path=OUTPUT)
        await browser.close()
        print(f"\nSaved session -> {OUTPUT}")
        print("Now add to backend/.env:  SCRAPER_STORAGE_STATE=scraper_session.json")


if __name__ == "__main__":
    asyncio.run(main())
