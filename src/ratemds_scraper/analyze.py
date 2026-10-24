#!/usr/bin/env python3
"""
analyze.py -- Quick descriptive analysis of a RateMDs scrape.

Run it against the merged output (or any single shard's output):

    ratemds-scraper analyze                       # auto-find output/combined or output
    ratemds-scraper analyze --doctors A.csv --reviews B.csv
    ratemds-scraper analyze --top 15              # how many rows in each "top N" table

Unlike ``verify`` (which gates the pipeline on integrity), this is a read-only
summary meant for a quick sanity look at what was collected:

  1. Totals + reviews-per-doctor distribution (mean / median / max).
  2. Review source split (RateMDs vs Our Partners).
  3. Star-rating distribution across reviews, and mean doctor overall_rating.
  4. Top specialties and top states by doctor count.
  5. Review volume by year (from the review date column).

It never fails the build -- it only prints. Uses the standard library only.
"""
from __future__ import annotations

import re
import csv
import argparse
import statistics
from collections import Counter

# Reuse verify's small, well-tested loaders/formatters so the two stay in sync.
from .verify import to_float, find_default_files, load_rows, banner, pct

_YEAR_RE = re.compile(r"\b(19|20)\d\d\b")

csv.field_size_limit(16 * 1024 * 1024)   # some review bodies are long


def _bar(n, total, width=30):
    """A tiny text bar for histograms."""
    filled = int(round(width * n / total)) if total else 0
    return "#" * filled + "-" * (width - filled)


# --------------------------------------------------------------------------- #
# Section 1 -- totals + reviews per doctor
# --------------------------------------------------------------------------- #
def summary_totals(doctors, reviews):
    banner("1 - totals")
    by_id = {d.get("doctor_id"): d for d in doctors}      # dedupe, keep last
    n_doc, n_rev = len(by_id), len(reviews)
    print(f"  doctors (unique): {n_doc:,}")
    print(f"  reviews:          {n_rev:,}")

    per_doc = Counter(r.get("doctor_id") for r in reviews)
    counts = [per_doc.get(did, 0) for did in by_id] or [0]
    counts.sort()
    print(f"  reviews / doctor: mean {statistics.mean(counts):.1f}  "
          f"median {int(statistics.median(counts))}  max {max(counts):,}")
    zero = sum(1 for c in counts if c == 0)
    print(f"  doctors with 0 reviews: {zero:,} ({pct(zero, n_doc)})")


# --------------------------------------------------------------------------- #
# Section 2 -- review source split
# --------------------------------------------------------------------------- #
def summary_sources(reviews):
    banner("2 - review source split")
    src = Counter(r.get("review_type", "") or "(blank)" for r in reviews)
    total = sum(src.values()) or 1
    for name, n in src.most_common():
        print(f"  {name:<14} {n:>9,}  {pct(n, total):>6}  {_bar(n, total)}")


# --------------------------------------------------------------------------- #
# Section 3 -- rating distribution
# --------------------------------------------------------------------------- #
def summary_ratings(doctors, reviews):
    banner("3 - rating distribution")
    stars = Counter()
    for r in reviews:
        s = to_float(r.get("stars"))
        if s is not None:
            stars[int(round(s))] += 1
    total = sum(stars.values()) or 1
    print("  review stars (rounded):")
    for s in range(5, 0, -1):
        n = stars.get(s, 0)
        print(f"    {s}*  {n:>9,}  {pct(n, total):>6}  {_bar(n, total)}")

    overalls = [to_float(d.get("overall_rating")) for d in doctors]
    overalls = [x for x in overalls if x is not None]
    if overalls:
        print(f"  doctor overall_rating: mean {statistics.mean(overalls):.2f}  "
              f"median {statistics.median(overalls):.2f}  (n={len(overalls):,})")


# --------------------------------------------------------------------------- #
# Section 4 -- top specialties / states
# --------------------------------------------------------------------------- #
def _top_table(label, rows, key, top):
    banner(f"4 - top {label}")
    by_id = {d.get("doctor_id"): d for d in rows}
    c = Counter((d.get(key) or "(blank)").strip() for d in by_id.values())
    total = sum(c.values()) or 1
    for name, n in c.most_common(top):
        print(f"  {name[:34]:<34} {n:>8,}  {pct(n, total):>6}")


# --------------------------------------------------------------------------- #
# Section 5 -- review volume by year
# --------------------------------------------------------------------------- #
def summary_years(reviews):
    banner("5 - reviews by year")
    years = Counter()
    for r in reviews:
        # dates look like "October 12, 2024" -- pull the 4-digit year out.
        m = _YEAR_RE.search(r.get("date") or "")
        if m:
            years[m.group(0)] += 1
    if not years:
        print("  (no parseable review dates)")
        return
    total = sum(years.values()) or 1
    for y in sorted(years):
        n = years[y]
        print(f"  {y}  {n:>9,}  {pct(n, total):>6}  {_bar(n, total)}")


def run(doctors=None, reviews=None, top=10) -> int:
    """Programmatic entry point (used by ``ratemds-scraper analyze``).

    Read-only: always returns 0. ``doctors`` / ``reviews`` may be paths; when
    omitted the merged-then-single output dir is auto-discovered.
    """
    dd, rr = find_default_files()
    doctors_path = doctors or dd
    reviews_path = reviews or rr
    print(f"doctors: {doctors_path}")
    print(f"reviews: {reviews_path}")
    doctor_rows = load_rows(doctors_path)
    review_rows = load_rows(reviews_path)
    print(f"loaded {len(doctor_rows):,} doctor rows, {len(review_rows):,} review rows")

    summary_totals(doctor_rows, review_rows)
    summary_sources(review_rows)
    summary_ratings(doctor_rows, review_rows)
    _top_table("specialties", doctor_rows, "specialty", top)
    _top_table("states", doctor_rows, "doctor_state", top)
    summary_years(review_rows)
    print()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Quick descriptive analysis of a RateMDs scrape.")
    ap.add_argument("--doctors", default=None, help="path to doctors.csv")
    ap.add_argument("--reviews", default=None, help="path to reviews.csv")
    ap.add_argument("--top", type=int, default=10, help="rows per top-N table")
    args = ap.parse_args(argv)
    return run(doctors=args.doctors, reviews=args.reviews, top=args.top)


if __name__ == "__main__":
    raise SystemExit(main())
