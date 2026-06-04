"""
Static configuration and URL helpers for the RateMDs scraper.

Output paths, crawl facets and politeness defaults live here so the rest of the
package stays declarative. Output locations honour the ``RATEMDS_OUTPUT_DIR``
environment variable and otherwise default to an ``output/`` directory under the
current working directory, so an installed package never writes inside itself.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
OUTPUT_DIR = os.environ.get("RATEMDS_OUTPUT_DIR") or os.path.join(os.getcwd(), "output")
STATE_DIR = os.path.join(OUTPUT_DIR, "state")
HTML_CACHE_DIR = os.path.join(STATE_DIR, "html")

DOCTORS_CSV = os.path.join(OUTPUT_DIR, "doctors.csv")
REVIEWS_CSV = os.path.join(OUTPUT_DIR, "reviews.csv")
QUEUE_CSV = os.path.join(OUTPUT_DIR, "doctors_queue.csv")

DONE_LEDGER = os.path.join(STATE_DIR, "done.jsonl")     # completed units (resume)
ANOMALY_LOG = os.path.join(STATE_DIR, "anomalies.log")  # count mismatches etc.
CF_STATE = os.path.join(STATE_DIR, "cf.json")           # cf_clearance + UA cache

SITE = "https://www.ratemds.com"


def set_output_dir(path: str) -> None:
    """Point all output paths at ``path`` (used by the CLI's ``--output`` flag).

    Recomputes the derived file locations so a single call relocates the whole
    dataset, ledger and cache."""
    global OUTPUT_DIR, STATE_DIR, HTML_CACHE_DIR
    global DOCTORS_CSV, REVIEWS_CSV, QUEUE_CSV, DONE_LEDGER, ANOMALY_LOG, CF_STATE
    OUTPUT_DIR = os.path.abspath(path)
    STATE_DIR = os.path.join(OUTPUT_DIR, "state")
    HTML_CACHE_DIR = os.path.join(STATE_DIR, "html")
    DOCTORS_CSV = os.path.join(OUTPUT_DIR, "doctors.csv")
    REVIEWS_CSV = os.path.join(OUTPUT_DIR, "reviews.csv")
    QUEUE_CSV = os.path.join(OUTPUT_DIR, "doctors_queue.csv")
    DONE_LEDGER = os.path.join(STATE_DIR, "done.jsonl")
    ANOMALY_LOG = os.path.join(STATE_DIR, "anomalies.log")
    CF_STATE = os.path.join(STATE_DIR, "cf.json")

# --------------------------------------------------------------------------- #
# What to crawl.
#
# The crawl is organised PER STATE, optionally narrowed by specialty and/or
# gender, via the site's own search facets:
#     /best-doctors/<state>/?gender=<G>&specialty=<S>
# Both ``specialty`` and ``gender`` are OPTIONAL. Omit them to crawl every
# doctor in a state; pass one or both to scrape a focused slice. Keeping the
# crawl per-state keeps pagination shallow (hundreds of pages/state instead of
# tens of thousands for the whole country) and lets the state be stamped onto
# every row straight from the facet.
#
# ``specialty`` is just the slug RateMDs uses in ``?specialty=``; any valid slug
# works (e.g. "dermatologist", "family-gp", "cardiologist", "dentist"). The list
# below is only the default set used by ``run-all`` when you don't pass
# ``--specialty``; ``None`` in that list means "no specialty filter" (all).
# --------------------------------------------------------------------------- #
DEFAULT_SPECIALTIES: list[str | None] = [None]   # [None] => every specialty
DEFAULT_GENDERS: list[str | None] = [None]       # [None] => both/all

# 50 states + DC. URL slugs are lowercase; DC uses the path "dc/washington".
STATES = [
    "ak", "al", "ar", "az", "ca", "co", "ct", "de", "fl", "ga", "hi", "ia",
    "id", "il", "in", "ks", "ky", "la", "ma", "md", "me", "mi", "mn", "mo",
    "ms", "mt", "nc", "nd", "ne", "nh", "nj", "nm", "nv", "ny", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "va", "vt", "wa", "wi",
    "wv", "wy", "dc/washington",
]


def state_label(state: str) -> str:
    """URL slug -> CSV/column value, e.g. 'az'->'AZ', 'dc/washington'->'DC'."""
    if state == "dc/washington":
        return "DC"
    return state.upper()


def facet_key(state: str, specialty: str | None = None,
              gender: str | None = None) -> str:
    """Stable key for a (specialty, gender, state) facet, used in the resume
    ledger and the queue's ``facet`` column. ``None`` becomes ``all``."""
    return f"{specialty or 'all'}-{gender or 'all'}-{state_label(state)}"


def search_url(state: str, specialty: str | None = None,
               gender: str | None = None, page: int = 1) -> str:
    """Per-state search results URL. ``specialty`` and ``gender`` are optional
    query filters. Page 1 omits the ``&page=`` param (a site quirk)."""
    params = []
    if gender:
        params.append(f"gender={gender}")
    if specialty:
        params.append(f"specialty={specialty}")
    url = f"{SITE}/best-doctors/{state}/"
    if params:
        url += "?" + "&".join(params)
    if page > 1:
        url += ("&" if "?" in url else "?") + f"page={page}"
    return url


def review_page_url(profile_url: str, page: int, kind: str) -> str:
    """Paginated review URL for a doctor profile.

    kind == 'ratemds'  -> ?page=n              (RateMDs reviews)
    kind == 'partners' -> ?third_party_page=n  (Our Partners reviews)
    """
    base = profile_url.split("?")[0].split("#")[0]
    if not base.endswith("/"):
        base += "/"
    param = "page" if kind == "ratemds" else "third_party_page"
    return f"{base}?{param}={page}"


# --------------------------------------------------------------------------- #
# Politeness / robustness defaults (all overridable from the CLI)
# --------------------------------------------------------------------------- #
# Calibrated around ~2-3 req/s: 4 workers x random 1-3s delay. Measured safe on a
# single IP (8 workers @ 0.3-0.8s tripped Cloudflare 429s; this stays under).
DELAY_MIN = 1.0           # seconds, lower bound of randomized per-request delay
DELAY_MAX = 3.0           # seconds, upper bound
MAX_RETRIES = 4           # per request before giving up
BACKOFF_BASE = 5.0        # seconds, exponential backoff base on 429/403/5xx
REQUEST_TIMEOUT = 30      # seconds

# Impersonation profile for curl_cffi (must roughly match the harvested UA).
IMPERSONATE = "chrome"

# Default number of concurrent worker threads (override with --workers).
DEFAULT_WORKERS = 4

# Chrome/Chromium launch flags that make the browser start in containers, on
# servers, and when running as root (where the sandbox refuses to start).
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",   # avoid /dev/shm exhaustion in containers
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
]
