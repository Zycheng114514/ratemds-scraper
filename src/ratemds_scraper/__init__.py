"""
ratemds-scraper -- a resumable, Cloudflare-aware scraper for RateMDs doctor
profiles and reviews.

Public API
----------
    from ratemds_scraper import Fetcher, discover, scrape_doctors, run_all

The scraper reads the JSON blob that RateMDs embeds in every page rather than
parsing fragile HTML, harvests a Cloudflare ``cf_clearance`` cookie once with a
real browser, then bulk-fetches with curl_cffi. See the README for usage.
"""

from __future__ import annotations

from .fetcher import Fetcher, CloudflareError
from .scraper import discover, scrape_doctors, run_all, dedupe

__version__ = "0.1.0"

__all__ = [
    "Fetcher",
    "CloudflareError",
    "discover",
    "scrape_doctors",
    "run_all",
    "dedupe",
    "__version__",
]
