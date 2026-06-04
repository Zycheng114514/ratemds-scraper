"""
cloudflare.py -- Layered, thread-safe, server-ready fetcher that gets past
RateMDs' Cloudflare JS challenge.

Strategy (confirmed with the user):
  1. Use a REAL headless browser (nodriver, falling back to Playwright+stealth)
     to solve the JS challenge ONCE and harvest the ``cf_clearance`` cookie plus
     the browser's exact User-Agent.
  2. Reuse that cookie/UA with fast ``curl_cffi`` requests (Chrome TLS
     impersonation) for the bulk of fetching.
  3. On a 403 / challenge response, transparently re-harvest and retry.

Concurrency model (why you do NOT need one browser per thread):
  The cf_clearance cookie is bound to {User-Agent, IP}, not to a browser session.
  A single harvested cookie can therefore be sent on MANY simultaneous curl_cffi
  requests from the same machine. So N worker threads share ONE cookie and each
  use their own curl_cffi session -- much lighter than launching N browsers.
  Concurrency is ultimately limited by per-IP rate limiting, which you tune with
  --workers and --delay. (To go beyond one IP you'd add a rotating proxy pool;
  the harvest is per-IP so each proxy would need its own cookie.)

Server / headless notes:
  Runs fine with no display. config.BROWSER_ARGS adds --no-sandbox etc. so Chrome
  starts in containers and as root. See README "Running on a server".
"""

from __future__ import annotations

import os
import json
import time
import random
import asyncio
import logging
import threading

from . import config
from .parse import looks_like_challenge

log = logging.getLogger("ratemds.fetcher")


class CloudflareError(Exception):
    pass


class Fetcher:
    """Thread-safe RateMDs fetcher.

    Share a single Fetcher instance across all worker threads:
        f = Fetcher(delay=(1.5, 4.0))
        f.ensure_clearance()          # optional warm-up in the main thread
        html = f.get(url)             # safe to call concurrently
    """

    def __init__(self, delay=(config.DELAY_MIN, config.DELAY_MAX),
                 headless: bool = False, cache_dir: str | None = None):
        # NOTE: default is HEADED. Cloudflare detects headless Chrome and never
        # clears the challenge; a headed browser (real or xvfb display) solves it
        # instantly. On a display-less server, run under `xvfb-run`.
        self.delay_min, self.delay_max = delay
        self.headless = headless
        self.cache_dir = cache_dir

        self._cf_clearance: str | None = None
        self._user_agent: str | None = None

        self._local = threading.local()         # per-thread curl_cffi session
        self._harvest_lock = threading.Lock()   # only one browser harvest at a time
        self._cache_lock = threading.Lock()     # guard the gzip cache writes
        self._cooldown_until = 0.0              # global throttle shared by all threads
        self._cooldown_lock = threading.Lock()

        self._load_cf_state()

    # ------------------------------------------------------------------ #
    # cf_clearance persistence
    # ------------------------------------------------------------------ #
    def _load_cf_state(self):
        if os.path.exists(config.CF_STATE):
            try:
                d = json.load(open(config.CF_STATE))
                self._cf_clearance = d.get("cf_clearance")
                self._user_agent = d.get("user_agent")
                log.info("Loaded cached cf_clearance (UA=%s...)", (self._user_agent or "")[:40])
            except Exception:
                pass

    def _save_cf_state(self):
        os.makedirs(os.path.dirname(config.CF_STATE), exist_ok=True)
        json.dump(
            {"cf_clearance": self._cf_clearance, "user_agent": self._user_agent,
             "saved": time.time()},
            open(config.CF_STATE, "w"),
        )

    # ------------------------------------------------------------------ #
    # curl_cffi session (one per thread; Session is not thread-safe)
    # ------------------------------------------------------------------ #
    def _ensure_session(self):
        sess = getattr(self._local, "session", None)
        if sess is None:
            from curl_cffi import requests as creq
            sess = creq.Session(impersonate=config.IMPERSONATE)
            self._local.session = sess
        return sess

    def _drop_session(self):
        # force this thread to rebuild its session (e.g. after a UA change)
        self._local.session = None

    def _headers(self) -> dict:
        h = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": config.SITE + "/",
        }
        if self._user_agent:
            h["User-Agent"] = self._user_agent
        return h

    def _cookies(self) -> dict:
        return {"cf_clearance": self._cf_clearance} if self._cf_clearance else {}

    # ------------------------------------------------------------------ #
    # Cloudflare clearance harvest (browser) -- thread-safe
    # ------------------------------------------------------------------ #
    def ensure_clearance(self):
        """Make sure we hold a cookie; harvest once if not. Safe to call from
        the main thread before dispatching workers (recommended)."""
        if not self._cf_clearance:
            self._refresh_clearance(old_cookie=None)

    def _refresh_clearance(self, old_cookie: str | None, warm_url: str | None = None):
        """Re-harvest the cookie, but only one thread does it at a time. If
        another thread already refreshed it while we waited on the lock, reuse
        their result instead of launching a second browser."""
        with self._harvest_lock:
            if self._cf_clearance and self._cf_clearance != old_cookie:
                # someone else already refreshed -> just adopt the new cookie
                self._drop_session()
                return
            self._harvest_clearance(warm_url)

    def _harvest_clearance(self, warm_url: str | None = None):
        warm_url = warm_url or (config.SITE + "/best-doctors/")
        log.warning("Harvesting fresh cf_clearance via browser (slow, ~10-30s)...")
        # nodriver's first COLD chrome launch on a fresh box can time out with
        # "Failed to connect to browser"; the launch warms things up, so a couple
        # of retries almost always succeed. Only fall back to Playwright if every
        # nodriver attempt fails.
        for attempt in range(3):
            try:
                self._harvest_with_nodriver(warm_url)
            except Exception as e:
                log.warning("nodriver harvest attempt %d/3 failed (%s)", attempt + 1, e)
            if self._cf_clearance:
                break
            time.sleep(2.0)
        if not self._cf_clearance:
            log.warning("nodriver exhausted; trying Playwright fallback...")
            try:
                self._harvest_with_playwright(warm_url)
            except Exception as e:
                log.warning("Playwright path also failed (%s)", e)
        if not self._cf_clearance:
            raise CloudflareError("failed to obtain cf_clearance cookie")
        self._save_cf_state()
        self._drop_session()
        log.warning("Got cf_clearance OK (UA=%s...).", (self._user_agent or "")[:40])

    def _harvest_with_nodriver(self, warm_url: str):
        import nodriver as uc

        async def _run():
            browser = await uc.start(
                headless=self.headless,
                browser_args=list(config.BROWSER_ARGS),
            )
            try:
                page = await browser.get(warm_url)
                cookie = None
                for _ in range(40):                       # up to ~30s
                    await asyncio.sleep(0.75)
                    # Read the cf_clearance cookie each tick. The decisive
                    # success condition is the COOKIE being present -- not just
                    # the challenge HTML disappearing. On some hosts the page
                    # momentarily stops "looking like a challenge" before the
                    # cookie is actually set, so breaking on the HTML alone
                    # harvested nothing. Require the cookie (or, as a backstop,
                    # the real data blob) before we stop.
                    for c in await browser.cookies.get_all():
                        if c.name == "cf_clearance" and c.value:
                            cookie = c.value
                    html = await page.get_content()
                    if cookie and not looks_like_challenge(html):
                        break
                    if cookie and "doctor_list_props" in html:
                        break
                ua = await page.evaluate("navigator.userAgent")
                if cookie:
                    self._cf_clearance = cookie
                if isinstance(ua, str):
                    self._user_agent = ua
            finally:
                browser.stop()

        # Use a fresh event loop bound to THIS thread so harvest works whether
        # it's called from the main thread or a worker thread. nodriver leaves
        # background tasks running, so we cancel/drain them before closing the
        # loop and quiet asyncio's "Task was destroyed" teardown chatter.
        prev_level = logging.getLogger("asyncio").level
        logging.getLogger("asyncio").setLevel(logging.CRITICAL)
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_run())
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        finally:
            loop.close()
            asyncio.set_event_loop(None)
            logging.getLogger("asyncio").setLevel(prev_level)

    def _harvest_with_playwright(self, warm_url: str):
        from playwright.sync_api import sync_playwright
        try:
            from playwright_stealth import stealth_sync
        except Exception:
            stealth_sync = None

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless,
                                        args=list(config.BROWSER_ARGS))
            ctx = browser.new_context()
            page = ctx.new_page()
            if stealth_sync:
                stealth_sync(page)
            page.goto(warm_url, wait_until="domcontentloaded", timeout=60000)
            cookie = None
            for _ in range(40):
                time.sleep(0.75)
                for c in ctx.cookies():
                    if c.get("name") == "cf_clearance" and c.get("value"):
                        cookie = c.get("value")
                html = page.content()
                if cookie and (not looks_like_challenge(html)
                               or "doctor_list_props" in html):
                    break
            self._user_agent = page.evaluate("navigator.userAgent")
            if cookie:
                self._cf_clearance = cookie
            browser.close()

    # ------------------------------------------------------------------ #
    # Public fetch (thread-safe)
    # ------------------------------------------------------------------ #
    def _sleep(self):
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    def _respect_cooldown(self):
        """Block until any global rate-limit cooldown has elapsed. When one
        worker is told to slow down (429), every worker honours it, so the
        AGGREGATE request rate actually drops."""
        while True:
            with self._cooldown_lock:
                wait = self._cooldown_until - time.time()
            if wait <= 0:
                return
            time.sleep(min(wait, 5.0))

    def _set_cooldown(self, seconds: float):
        with self._cooldown_lock:
            self._cooldown_until = max(self._cooldown_until, time.time() + seconds)

    def get(self, url: str, use_cache: bool = False) -> str:
        """Fetch a URL's HTML, solving/refreshing the challenge as needed.
        Safe to call concurrently from multiple threads.

        Distinguishes failure modes:
          * 403 (no challenge)  -> cookie outright rejected -> re-harvest now.
          * 429 / 503 / challenge -> rate-limited: set a GLOBAL cooldown so all
            workers back off; only re-harvest if it persists past the first retry
            (a fresh cf_clearance resets Cloudflare's rate window).
        """
        if use_cache and self.cache_dir:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        if not self._cf_clearance:
            self._refresh_clearance(old_cookie=None, warm_url=url)

        last_err = None
        for attempt in range(config.MAX_RETRIES):
            self._respect_cooldown()
            self._sleep()
            cookie_used = self._cf_clearance
            backoff = config.BACKOFF_BASE * (2 ** attempt)
            try:
                sess = self._ensure_session()
                resp = sess.get(
                    url, headers=self._headers(), cookies=self._cookies(),
                    timeout=config.REQUEST_TIMEOUT,
                )
                html = resp.text
                status = resp.status_code
                if status == 200 and not looks_like_challenge(html):
                    if use_cache and self.cache_dir:
                        self._write_cache(url, html)
                    return html
                challenged = looks_like_challenge(html)
                log.warning("status=%s challenge=%s on %s (attempt %d)",
                            status, challenged, url, attempt + 1)
                if status == 403 and not challenged:
                    self._refresh_clearance(old_cookie=cookie_used, warm_url=url)
                elif status in (429, 503) or challenged:
                    self._set_cooldown(backoff)          # slow EVERY worker down
                    if attempt >= 1:                     # persistent -> fresh cookie
                        self._refresh_clearance(old_cookie=cookie_used, warm_url=url)
                else:
                    self._set_cooldown(backoff)
            except Exception as e:
                last_err = e
                log.warning("request error on %s: %s (attempt %d)", url, e, attempt + 1)
                self._set_cooldown(backoff)

        raise CloudflareError(f"giving up on {url}: {last_err}")

    # ------------------------------------------------------------------ #
    # Optional raw-HTML cache (handy while tuning parse.py)
    # ------------------------------------------------------------------ #
    def _cache_path(self, url: str) -> str:
        import hashlib
        h = hashlib.sha1(url.encode()).hexdigest()
        return os.path.join(self.cache_dir, h + ".html.gz")

    def _read_cache(self, url: str):
        import gzip
        p = self._cache_path(url)
        if os.path.exists(p):
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                return fh.read()
        return None

    def _write_cache(self, url: str, html: str):
        import gzip
        with self._cache_lock:
            os.makedirs(self.cache_dir, exist_ok=True)
            with gzip.open(self._cache_path(url), "wt", encoding="utf-8") as fh:
                fh.write(html)
