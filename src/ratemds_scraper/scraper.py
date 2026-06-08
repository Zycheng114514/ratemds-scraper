"""
Crawl orchestrator: discover -> scrape -> (dedupe).

Two outputs are produced under the output directory (see :mod:`.config`):

    doctors.csv  -- one row per doctor
    reviews.csv  -- one row per review (RateMDs + "Our Partners")

Everything is resumable: a JSONL ledger records completed units (search pages
and doctor ids), so a killed run picks up where it left off, and the CSVs are
append-only. The crawl can be narrowed by state, specialty and gender, or run
across every state at full breadth.
"""

from __future__ import annotations

import os
import csv
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from . import config
from . import parse
from .fetcher import Fetcher, CloudflareError
from .ledger import Ledger, CsvSink, anomaly

log = logging.getLogger("ratemds.scraper")


# --------------------------------------------------------------------------- #
# Phase A -- discover
# --------------------------------------------------------------------------- #
def discover(fetcher: Fetcher, specialty: str | None = None,
             gender: str | None = None, states: list[str] | None = None,
             use_cache: bool = False, workers: int = 1,
             max_page: int | None = None):
    """Crawl per-state search facets into the queue CSV.

    For each state we fetch ``/best-doctors/<state>/`` (optionally filtered by
    ``specialty`` / ``gender``), read the blob's ``total_pages`` to learn how
    deep to go, and stamp the state (and gender, when filtered) straight onto
    every doctor. Resumable per (facet, page); deduped by ``doctor_id`` across
    the whole queue.
    """
    states = states or config.STATES
    ledger = Ledger(config.DONE_LEDGER)
    queue = CsvSink(config.QUEUE_CSV,
                    ["doctor_id", "name", "specialty", "location",
                     "profile_url", "state", "gender", "facet"])
    seen = _load_seen_ids(config.QUEUE_CSV)
    seen_lock = threading.Lock()
    fetcher.ensure_clearance()   # warm up cookie in the main thread

    def record(stubs, key) -> int:
        added = 0
        for s in stubs:
            with seen_lock:
                if s.doctor_id in seen:
                    continue
                seen.add(s.doctor_id)
            queue.write({**parse.stub_to_dict(s), "facet": key})
            added += 1
        queue.flush()
        return added

    for state in states:
        slabel = config.state_label(state)
        key = config.facet_key(state, specialty, gender)

        def handle_page(page: int):
            unit = f"search:{key}:{page}"
            if ledger.has(unit):
                return
            url = config.search_url(state, specialty, gender, page)
            try:
                html = fetcher.get(url, use_cache=use_cache)
                stubs, _ = parse.parse_search_page(html, gender=gender, state=slabel)
            except parse.BlobNotFound:
                anomaly(f"{unit}: no doctor_list_props blob; url={url}")
                return
            except CloudflareError as e:
                log.error("Cloudflare gave up on %s: %s", url, e)
                return
            record(stubs, key)
            ledger.mark(unit)

        # Page 1 first to learn total_pages for this state.
        unit1 = f"search:{key}:1"
        total_pages = 1
        if not ledger.has(unit1):
            try:
                html = fetcher.get(config.search_url(state, specialty, gender, 1),
                                   use_cache=use_cache)
                stubs, pg = parse.parse_search_page(html, gender=gender, state=slabel)
                total_pages = max_page or pg.total_pages or 1
                record(stubs, key)
                ledger.mark(unit1)
            except parse.BlobNotFound:
                anomaly(f"{unit1}: no blob; skipping state {slabel}")
                continue
            except CloudflareError as e:
                log.error("Cloudflare gave up on state %s: %s", slabel, e)
                continue
        else:
            # already did page 1 on a prior run; refetch just to learn total_pages
            try:
                html = fetcher.get(config.search_url(state, specialty, gender, 1),
                                   use_cache=use_cache)
                _, pg = parse.parse_search_page(html, gender=gender, state=slabel)
                total_pages = max_page or pg.total_pages or 1
            except Exception:
                total_pages = max_page or 1

        log.info("Discover %s/%s state=%s: %d pages (queue total=%d)",
                 specialty or "all", gender or "all", slabel, total_pages, len(seen))

        rest = range(2, total_pages + 1)
        if workers <= 1:
            for p in rest:
                handle_page(p)
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(handle_page, rest))

    queue.close()
    ledger.close()
    log.info("Discover done for %s/%s: %d unique doctors queued total.",
             specialty or "all", gender or "all", len(seen))


def _load_seen_ids(queue_path: str) -> set:
    seen = set()
    if os.path.exists(queue_path):
        with open(queue_path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                v = r.get("doctor_id")
                if v:
                    try:
                        seen.add(int(v))
                    except ValueError:
                        seen.add(v)
    return seen


# --------------------------------------------------------------------------- #
# Phase B -- scrape doctors + reviews
# --------------------------------------------------------------------------- #
def scrape_doctors(fetcher: Fetcher, specialty: str | None = None,
                   gender: str | None = None, limit: int | None = None,
                   use_cache: bool = False, workers: int = 1,
                   states: list[str] | None = None):
    """Visit each queued doctor, write the doctor row + paginate all reviews.

    With ``workers > 1`` doctors are processed concurrently (each worker handles
    one doctor and its review pages sequentially). The shared Fetcher, CSV sinks
    and ledger are all thread-safe.
    """
    if not os.path.exists(config.QUEUE_CSV):
        log.error("No queue file at %s -- run 'discover' first.", config.QUEUE_CSV)
        return

    ledger = Ledger(config.DONE_LEDGER)
    doctors = CsvSink(config.DOCTORS_CSV, parse.DOCTOR_COLUMNS)
    reviews = CsvSink(config.REVIEWS_CSV, parse.REVIEW_COLUMNS)

    rows = list(csv.DictReader(open(config.QUEUE_CSV, encoding="utf-8")))
    # Filter by the queue's own facet/gender/state columns.
    if specialty:
        rows = [r for r in rows if r.get("facet", "").startswith(specialty + "-")]
    if gender:
        rows = [r for r in rows if r.get("gender", "") == gender]
    if states:
        wanted = {config.state_label(s) for s in states}
        rows = [r for r in rows if r.get("state", "") in wanted]

    # Skip already-completed doctors, then apply the per-run limit.
    pending = [r for r in rows if not ledger.has(f"doctor:{r.get('doctor_id')}")]
    if limit:
        pending = pending[:limit]

    log.info("scrape-doctors: %d doctors pending (workers=%d)", len(pending), workers)
    fetcher.ensure_clearance()   # warm up cookie in the main thread
    counter = {"done": 0}
    counter_lock = threading.Lock()

    def work(r):
        did = r.get("doctor_id")
        try:
            _scrape_one_doctor(fetcher, r, doctors, reviews, use_cache=use_cache)
            ledger.mark(f"doctor:{did}")
            with counter_lock:
                counter["done"] += 1
        except CloudflareError as e:
            # A per-doctor give-up is almost always a transient timeout, NOT a
            # real IP block, so it must not abort the run. Leave the doctor
            # un-done so a later pass retries it, log it, and keep going. Genuine
            # IP blocks are handled by the fetcher's 403/429 re-harvest, not by
            # killing the crawl.
            log.error("Cloudflare gave up on doctor %s: %s", did, e)
            anomaly(f"doctor:{did}: cloudflare gave up (left pending for retry); {e}")
        except parse.BlobNotFound:
            anomaly(f"doctor:{did}: no doctorDetailProps blob; url={r.get('profile_url')}")
        except Exception as e:
            anomaly(f"doctor:{did}: unexpected error {type(e).__name__}: {e}")

    if workers <= 1:
        for r in pending:
            work(r)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, pending))

    doctors.close()
    reviews.close()
    ledger.close()
    log.info("scrape-doctors done: %d doctors processed this run.", counter["done"])


def _scrape_one_doctor(fetcher, qrow, doctors: CsvSink, reviews: CsvSink,
                       use_cache: bool = False):
    profile_url = qrow.get("profile_url")
    gender = qrow.get("gender", "")
    state = qrow.get("state", "")           # known from the per-state facet
    html = fetcher.get(profile_url, use_cache=use_cache)
    dp = parse.parse_doctor_page(html, gender=gender, profile_url=profile_url, state=state)
    dr = dp.doctor_row
    did = dr["doctor_id"]
    name = dr["name"]

    doctors.write(dr)
    doctors.flush()

    all_reviews = list(dp.reviews)   # page 1 already has RateMDs + partners

    # Paginate RateMDs reviews (page 2..N) via ?page=n
    for n in range(2, dp.ratemds_pages + 1):
        url = config.review_page_url(profile_url, n, "ratemds")
        try:
            all_reviews += parse.parse_review_page(fetcher.get(url, use_cache=use_cache),
                                                   did, name, "ratemds")
        except parse.BlobNotFound:
            anomaly(f"doctor:{did}: ratemds page {n} missing blob; url={url}")

    # Paginate Our Partners reviews (page 2..N) via ?third_party_page=n.
    # NOTE: the partner feed re-serves the same recent batch regardless of the
    # requested page (the server keeps current_page=1), so a page that adds no
    # new review_ids means the distinct partner reviews are exhausted -- stop
    # rather than keep re-fetching (and re-appending) the same rows.
    seen_partner_ids = {r["review_id"] for r in all_reviews
                        if r.get("review_type") == "Our Partners"}
    for n in range(2, dp.partner_pages + 1):
        url = config.review_page_url(profile_url, n, "partners")
        try:
            page_rows = parse.parse_review_page(fetcher.get(url, use_cache=use_cache),
                                                did, name, "partners")
        except parse.BlobNotFound:
            anomaly(f"doctor:{did}: partner page {n} missing blob; url={url}")
            continue
        new_rows = [r for r in page_rows if r["review_id"] not in seen_partner_ids]
        if not new_rows:
            break   # partner feed repeated -- no further distinct reviews
        seen_partner_ids.update(r["review_id"] for r in new_rows)
        all_reviews += new_rows

    # De-duplicate within this doctor by review_id (keep first). Guards against
    # the partner feed re-serving rows and against an interrupted/resumed run
    # revisiting a page.
    seen_ids: set = set()
    deduped: list[dict] = []
    for r in all_reviews:
        rid = r.get("review_id")
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        deduped.append(r)
    all_reviews = deduped

    reviews.write_many(all_reviews)
    reviews.flush()

    # Reconciliation: emitted reviews should match the doctor's stated total.
    if len(all_reviews) != dp.expected_total:
        anomaly(f"doctor:{did} ({name}): scraped {len(all_reviews)} reviews but "
                f"expected {dp.expected_total} "
                f"(ratemds={dr['ratemds_count']} partners={dr['our_partners_count']})")
    log.info("  doctor %s '%s': %d reviews (expected %d)",
             did, name, len(all_reviews), dp.expected_total)


# --------------------------------------------------------------------------- #
# dedupe -- clean duplicates left by interrupted/resumed runs
# --------------------------------------------------------------------------- #
def _dedupe_csv(path: str, key_cols: tuple, keep: str = "first") -> tuple[int, int]:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return (0, 0)
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames
        rows = list(reader)
    seen: dict = {}
    for r in rows:
        k = tuple(r.get(c, "") for c in key_cols)
        if keep == "last" or k not in seen:
            seen[k] = r
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(seen.values())
    os.replace(tmp, path)
    return (len(rows), len(seen))


def dedupe():
    """Drop duplicate rows an interrupted+resumed run can produce (a doctor
    killed mid-scrape is redone and its rows re-appended). Safe to run anytime;
    keyed on stable IDs so it never merges distinct records."""
    db, da = _dedupe_csv(config.DOCTORS_CSV, ("doctor_id",), keep="last")
    rb, ra = _dedupe_csv(config.REVIEWS_CSV,
                         ("doctor_id", "review_type", "review_id"), keep="first")
    log.info("dedupe: doctors %d -> %d (-%d), reviews %d -> %d (-%d)",
             db, da, db - da, rb, ra, rb - ra)


# --------------------------------------------------------------------------- #
# run-all
# --------------------------------------------------------------------------- #
def run_all(fetcher: Fetcher, specialties: list[str | None] | None = None,
            genders: list[str | None] | None = None,
            states: list[str] | None = None,
            use_cache: bool = False, workers: int = 1):
    """Discover every (specialty x gender) facet, then scrape, then dedupe.

    ``specialties`` / ``genders`` default to :data:`config.DEFAULT_SPECIALTIES`
    / :data:`config.DEFAULT_GENDERS` (both ``[None]`` => crawl everything).
    ``states`` restricts the crawl to a subset (used to shard across machines).
    """
    specialties = specialties or config.DEFAULT_SPECIALTIES
    genders = genders or config.DEFAULT_GENDERS
    for specialty in specialties:
        for gender in genders:
            discover(fetcher, specialty, gender, states=states,
                     use_cache=use_cache, workers=workers)
    for specialty in specialties:
        for gender in genders:
            scrape_doctors(fetcher, specialty=specialty, gender=gender,
                           states=states, use_cache=use_cache, workers=workers)
    dedupe()   # tidy up any rows left by mid-doctor interruptions
