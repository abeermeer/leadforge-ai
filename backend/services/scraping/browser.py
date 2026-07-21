"""Shared Playwright launcher with anti-ban measures (PRD section 7).

All Playwright-based scrapers MUST acquire pages through get_page() — never a
raw async_playwright() — so every session gets a rotated proxy, a rotated
user-agent, and a configurable viewport. Also provides per-domain politeness
delays and a per-source circuit breaker that pauses a source after repeated
blocks (403/429).

The playwright import is deliberately lazy (inside get_page) so this module
imports cleanly on machines without playwright installed.
"""
import asyncio
import random
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from services.scraping.proxies import get_proxy, get_user_agent

# --------------------------------------------------------------------------- politeness

# Minimum gap between hits to the same domain, plus random jitter on top.
MIN_DELAY_SECONDS = 4.0
JITTER_MAX_SECONDS = 3.0

# {domain: monotonic timestamp of the last hit}
_last_hit: dict[str, float] = {}

# --------------------------------------------------------------------------- circuit breaker

# Consecutive blocks before a source is paused, and for how long.
BLOCK_THRESHOLD = 5
PAUSE_SECONDS = 15 * 60

# {source: consecutive block count}
_block_counts: dict[str, int] = {}
# {source: monotonic timestamp until which the source is paused}
_paused_until: dict[str, float] = {}


@asynccontextmanager
async def get_page(viewport: tuple[int, int] = (1920, 1080)) -> AsyncIterator:
    """Yield a Playwright Page in a headless Chromium with proxy + UA rotation.

    Launches a fresh browser per acquisition (isolated fingerprint per session)
    with the next proxy from the pool (direct if the pool is empty), a random
    realistic user-agent, and the given viewport. Browser and context are
    always closed on exit, even on error.
    """
    # Lazy import so the module loads on machines without playwright installed.
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = None
    context = None
    try:
        launch_kwargs: dict = {"headless": True}
        proxy = get_proxy()
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        browser = await playwright.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=get_user_agent(),
            viewport={"width": viewport[0], "height": viewport[1]},
        )
        page = await context.new_page()
        yield page
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        await playwright.stop()


async def polite_delay(domain: str) -> None:
    """Sleep until at least MIN_DELAY_SECONDS (+ jitter) since the last hit to domain.

    First hit to a domain returns immediately. Call this before every request
    to a scraped host.
    """
    now = time.monotonic()
    last = _last_hit.get(domain)
    if last is not None:
        required_gap = MIN_DELAY_SECONDS + random.uniform(0.0, JITTER_MAX_SECONDS)
        remaining = required_gap - (now - last)
        if remaining > 0:
            await asyncio.sleep(remaining)
    _last_hit[domain] = time.monotonic()


def record_block(source: str) -> None:
    """Register a block (403/429/captcha) for a source.

    After BLOCK_THRESHOLD consecutive blocks the source is paused for
    PAUSE_SECONDS; further blocks extend the pause window.
    """
    _block_counts[source] = _block_counts.get(source, 0) + 1
    if _block_counts[source] >= BLOCK_THRESHOLD:
        _paused_until[source] = time.monotonic() + PAUSE_SECONDS


def record_success(source: str) -> None:
    """Register a successful request, resetting the consecutive-block counter."""
    _block_counts[source] = 0


def is_paused(source: str) -> bool:
    """True while the circuit breaker holds this source in a pause window."""
    until = _paused_until.get(source)
    if until is None:
        return False
    if time.monotonic() >= until:
        # Pause expired — allow traffic again with a fresh block counter.
        del _paused_until[source]
        _block_counts[source] = 0
        return False
    return True
