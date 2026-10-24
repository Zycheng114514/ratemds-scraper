"""
ratemds-scraper -- a resumable, Cloudflare-aware scraper for RateMDs doctor
profiles and reviews.

Public API
----------
    from ratemds_scraper import Fetcher, discover, scrape_doctors, run_all

The scraper reads the JSON blob that RateMDs embeds in every page rather than
parsing fragile HTML. It fetches with ordinary system curl by default, with an
optional browser/curl_cffi transport. See the README for usage.
"""

from __future__ import annotations

from .fetcher import Fetcher, CloudflareError
from .scraper import IncompleteScrapeError, discover, scrape_doctors, run_all, dedupe

__version__ = "0.1.0"

__all__ = [
    "Fetcher",
    "CloudflareError",
    "IncompleteScrapeError",
    "discover",
    "scrape_doctors",
    "run_all",
    "dedupe",
    "__version__",
]
