#!/usr/bin/env python3
"""
verify.py -- Data-integrity + robustness checks for the RateMDs scrape.

Run it against the merged output (or any single shard's output):

    python verify.py                              # auto-find output/combined or output
    python verify.py --doctors A.csv --reviews B.csv
    python verify.py --tol 0.2                     # tolerance for the rating checks

It runs FOUR checks:

  1. Review-count reconciliation
       (a) per doctor row:  ratemds_count + our_partners_count == total_reviews
       (b) completeness:    rows actually in reviews.csv for that doctor match those
                            counts (and split correctly by review_type)

  2. RateMDs sub-rating consistency
       mean(staff, punctuality, helpfulness, knowledge) ~= stars   (|diff| <= tol)
       (only for RateMDs reviews where all four sub-ratings + stars are present)

  3. Doctor aggregate-rating consistency
       mean(stars over a doctor's reviews) ~= overall_rating       (|diff| <= tol)

  4. Robustness (offline self-test of parse.py)
       Feed deliberately EMPTY / minimal blobs through the real parser to prove it
       never crashes when fields are missing -- every field except name + specialty
       is allowed to be empty (no rating == empty too).

Exit code is non-zero if any data check finds failures or the robustness test
crashes, so it can gate a pipeline.
"""
from __future__ import annotations

import os
import csv
import sys
import json
import argparse
from collections import defaultdict

from . import config
from . import parse   # the real parser -- check 4 exercises it directly

csv.field_size_limit(16 * 1024 * 1024)   # some review bodies are long


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def to_float(x):
    """CSV cell -> float, or None for '', 'NA', None, or non-numeric."""
    if x is None:
        return None
    s = str(x).strip()
    if s == "" or s.upper() == "NA":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(x):
    f = to_float(x)
    return int(f) if f is not None else None


def find_default_files():
    """Prefer the merged output, fall back to the single-machine output dir."""
    for d in (os.path.join(config.OUTPUT_DIR, "combined"), config.OUTPUT_DIR):
        dd = os.path.join(d, "doctors.csv")
        rr = os.path.join(d, "reviews.csv")
        if os.path.exists(dd):
            return dd, rr
    return config.DOCTORS_CSV, config.REVIEWS_CSV


def load_rows(path):
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def banner(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def pct(n, d):
    return f"{(100.0 * n / d):.1f}%" if d else "n/a"


# --------------------------------------------------------------------------- #
# Check 1 -- review-count reconciliation
# --------------------------------------------------------------------------- #
def check_counts(doctors, reviews, examples=8):
    banner("CHECK 1 - review-count reconciliation")
    if not doctors:
        print("  (no doctor rows yet -- nothing to check)")
        return True

    # dedupe doctors by id (keep last), mirroring the merge step
    by_id = {d.get("doctor_id"): d for d in doctors}

    # tally actual scraped review rows per (doctor_id, type)
    scraped = defaultdict(lambda: {"RateMDs": 0, "Our Partners": 0})
    for r in reviews:
        scraped[r.get("doctor_id")][r.get("review_type", "")] += 1

    fail_a = fail_b = 0
    ex_a, ex_b = [], []
    for did, d in by_id.items():
        rm = to_int(d.get("ratemds_count")) or 0
        op = to_int(d.get("our_partners_count")) or 0
        tot = to_int(d.get("total_reviews")) or 0
        # (a) internal: the two type counts add to the stated total
        if rm + op != tot:
            fail_a += 1
            if len(ex_a) < examples:
                ex_a.append(f"    id={did} {d.get('name','')!r}: "
                            f"ratemds={rm}+partners={op} != total={tot}")
        # (b) completeness: rows we actually stored match the stated counts
        s = scraped.get(did, {"RateMDs": 0, "Our Partners": 0})
        if s["RateMDs"] != rm or s["Our Partners"] != op:
            fail_b += 1
            if len(ex_b) < examples:
                ex_b.append(f"    id={did} {d.get('name','')!r}: stored "
                            f"RateMDs={s['RateMDs']}/{rm} "
                            f"Partners={s['Our Partners']}/{op}")

    n = len(by_id)
    print(f"  (a) ratemds+partners == total_reviews : "
          f"{n - fail_a}/{n} pass ({pct(n - fail_a, n)}), {fail_a} fail")
    for e in ex_a:
        print(e)
    print(f"  (b) stored review rows == stated counts: "
          f"{n - fail_b}/{n} pass ({pct(n - fail_b, n)}), {fail_b} fail")
    for e in ex_b:
        print(e)
    if fail_b:
        print("      (note: 'fail' here often just means that doctor was still being"
              "\n       scraped when you snapshotted -- re-run after it finishes.)")
    return fail_a == 0 and fail_b == 0


# --------------------------------------------------------------------------- #
# Check 2 -- RateMDs sub-ratings average to the review's stars
# --------------------------------------------------------------------------- #
def check_subratings(reviews, tol, examples=8):
    banner(f"CHECK 2 - RateMDs sub-ratings average to stars (|diff| <= {tol})")
    checked = passed = 0
    skipped = 0
    devs = []
    ex = []
    for r in reviews:
        if r.get("review_type") != "RateMDs":
            continue
        stars = to_float(r.get("stars"))
        subs = [to_float(r.get(k)) for k in ("staff", "punctuality",
                                             "helpfulness", "knowledge")]
        if stars is None or any(s is None for s in subs):
            skipped += 1                 # empty sub-ratings are allowed -> skip
            continue
        checked += 1
        mean = sum(subs) / 4.0
        diff = abs(mean - stars)
        devs.append(diff)
        if diff <= tol:
            passed += 1
        elif len(ex) < examples:
            ex.append(f"    id={r.get('review_id')} doc={r.get('doctor_id')}: "
                      f"mean({'/'.join(str(s) for s in subs)})={mean:.2f} "
                      f"vs stars={stars} (diff {diff:.2f})")
    print(f"  checked {checked} RateMDs reviews "
          f"(skipped {skipped} with empty sub-ratings/stars)")
    if checked:
        print(f"  within tol: {passed}/{checked} ({pct(passed, checked)})")
        print(f"  mean |diff|: {sum(devs)/len(devs):.3f} | max |diff|: {max(devs):.3f}")
        for e in ex:
            print(e)
    else:
        print("  (no RateMDs reviews with full sub-ratings yet)")
    return checked == 0 or passed == checked


# --------------------------------------------------------------------------- #
# Check 3 -- a doctor's reviews average to the doctor's overall_rating
# --------------------------------------------------------------------------- #
def check_doctor_aggregate(doctors, reviews, tol, examples=8):
    banner(f"CHECK 3 - reviews average to doctor's overall_rating (|diff| <= {tol})")
    stars_by_doc = defaultdict(list)
    rm_stars_by_doc = defaultdict(list)
    for r in reviews:
        s = to_float(r.get("stars"))
        if s is None:
            continue
        stars_by_doc[r.get("doctor_id")].append(s)
        if r.get("review_type") == "RateMDs":
            rm_stars_by_doc[r.get("doctor_id")].append(s)

    by_id = {d.get("doctor_id"): d for d in doctors}
    checked = passed = passed_rm = 0
    skipped = 0
    devs = []
    ex = []
    for did, d in by_id.items():
        overall = to_float(d.get("overall_rating"))
        all_stars = stars_by_doc.get(did, [])
        if overall is None or not all_stars:
            skipped += 1                 # no rating == empty -> allowed, skip
            continue
        checked += 1
        mean_all = sum(all_stars) / len(all_stars)
        diff = abs(mean_all - overall)
        devs.append(diff)
        if diff <= tol:
            passed += 1
        # diagnostic: does it match better if the site averages RateMDs only?
        rm = rm_stars_by_doc.get(did, [])
        if rm and abs(sum(rm) / len(rm) - overall) <= tol:
            passed_rm += 1
        if diff > tol and len(ex) < examples:
            ex.append(f"    id={did} {d.get('name','')!r}: mean(all {len(all_stars)} "
                      f"reviews)={mean_all:.2f} vs overall={overall} (diff {diff:.2f})")
    print(f"  checked {checked} doctors (skipped {skipped} with no rating / no reviews)")
    if checked:
        print(f"  within tol (all reviews):       {passed}/{checked} ({pct(passed, checked)})")
        print(f"  within tol (RateMDs-only mean): {passed_rm}/{checked} ({pct(passed_rm, checked)})"
              "   <- diagnostic: which average the site uses")
        print(f"  mean |diff|: {sum(devs)/len(devs):.3f} | "
              f"within 0.5: {pct(sum(1 for x in devs if x <= 0.5), len(devs))}")
        for e in ex:
            print(e)
    else:
        print("  (no rated doctors with reviews yet)")
    return checked == 0 or passed == checked


# --------------------------------------------------------------------------- #
# Check 4 -- robustness: parser must not crash on empty / missing fields
# --------------------------------------------------------------------------- #
def _embed(var, obj):
    """Wrap a python object the way the real pages do:
       window.DATA.<var> = JSON.parse("<escaped json>");
    so parse.extract_blob decodes it identically."""
    inner = json.dumps(obj)              # JSON text
    raw = json.dumps(inner)[1:-1]        # escape it as a JS string literal, drop quotes
    return f'<script>window.DATA.{var} = JSON.parse("{raw}");</script>'


def check_robustness():
    banner("CHECK 4 - robustness: parser survives empty / missing info")
    print("  Every field except name + specialty may be empty. Feeding minimal/empty")
    print("  blobs through the REAL parser and asserting it does not crash:\n")
    cases = []

    # --- search page cases ---
    def c_search_minimal():
        html = _embed("doctor_list_props",
                      {"doctorPage": {"results": [
                          {"full_name": "Dr. Only Name", "specialty_name": "Dermatologist"}
                      ]}})
        stubs, pg = parse.parse_search_page(html, gender="F", state="WY")
        assert stubs[0].name == "Dr. Only Name"
        assert stubs[0].specialty == "Dermatologist"
        assert stubs[0].doctor_id is None and stubs[0].location == ""

    def c_search_totally_empty_result():
        html = _embed("doctor_list_props", {"doctorPage": {"results": [{}]}})
        stubs, _ = parse.parse_search_page(html)
        assert stubs[0].name == "" and stubs[0].doctor_id is None

    def c_search_no_results_key():
        html = _embed("doctor_list_props", {"doctorPage": {}})
        stubs, pg = parse.parse_search_page(html)
        assert stubs == [] and pg.total_pages == 1

    # --- doctor page cases ---
    def c_doctor_name_specialty_only():
        html = _embed("doctorDetailProps",
                      {"doctor": {"full_name": "Dr. Bare", "specialty_name": "Family"}})
        dp = parse.parse_doctor_page(html, gender="M")
        row = dp.doctor_row
        assert row["name"] == "Dr. Bare" and row["specialty"] == "Family"
        assert row["overall_rating"] == parse.NA and row["total_reviews"] == 0
        assert row["ratemds_count"] == 0 and row["our_partners_count"] == 0
        assert row["location"] == "" and row["hospital_affiliation_name"] == ""
        assert dp.reviews == []

    def c_doctor_completely_empty():
        html = _embed("doctorDetailProps", {"doctor": {}})
        dp = parse.parse_doctor_page(html)
        assert dp.doctor_row["name"] == "" and dp.doctor_row["total_reviews"] == 0

    def c_doctor_rating_but_zero_count():
        html = _embed("doctorDetailProps",
                      {"doctor": {"full_name": "Dr. Z", "specialty_name": "Derm",
                                  "rating": {"average": 4.5, "count": 0}}})
        dp = parse.parse_doctor_page(html)
        # avg present but count 0 -> overall NA (no reviews), must not crash
        assert dp.doctor_row["overall_rating"] == parse.NA

    def c_doctor_empty_review_objects():
        html = _embed("doctorDetailProps",
                      {"doctor": {"full_name": "Dr. R", "specialty_name": "Derm",
                                  "rating": {"average": None, "count": 2}},
                       "ratingsPage": {"results": [{}], "count": 1, "total_pages": 1},
                       "thirdPartyRatingsPage": {"results": [{}], "count": 1,
                                                 "total_pages": 1}})
        dp = parse.parse_doctor_page(html)
        assert len(dp.reviews) == 2          # one empty RateMDs + one empty partner
        rm = dp.reviews[0]
        assert rm["stars"] is None and rm["staff"] is None   # empty -> None, no crash

    def c_doctor_locations_empty():
        html = _embed("doctorDetailProps",
                      {"doctor": {"full_name": "Dr. L", "specialty_name": "Derm",
                                  "doctor_locations_on_display": [{}, {"location": {}},
                                                                  {"location": None}]}})
        dp = parse.parse_doctor_page(html)
        assert dp.doctor_row["hospital_affiliation_name"] == ""

    # --- review-page + partner-model cases ---
    def c_partner_models_and_missing_data():
        html = _embed("doctorDetailProps",
                      {"doctor": {"full_name": "Dr. P", "specialty_name": "Derm"},
                       "thirdPartyRatingsPage": {"total_pages": 1, "count": 3, "results": [
                           {"model": "review", "data": {"comment": "ok", "star_rating": 4}},
                           {"model": "rating", "data": {"rating": None, "title": None,
                                                        "description": None}},
                           {"model": "review"},          # no 'data' at all
                       ]}})
        dp = parse.parse_doctor_page(html)
        assert len(dp.reviews) == 3
        assert dp.reviews[0]["reviewer"] == "Verified Patient"   # review-model, no name

    def c_review_page_empty():
        html = _embed("doctorDetailProps",
                      {"ratingsPage": {"results": []},
                       "thirdPartyRatingsPage": {"results": [{}]}})
        a = parse.parse_review_page(html, 1, "x", "ratemds")
        b = parse.parse_review_page(html, 1, "x", "partners")
        assert a == [] and len(b) == 1

    cases = [
        ("search: name+specialty only", c_search_minimal),
        ("search: totally empty result {}", c_search_totally_empty_result),
        ("search: no 'results' key", c_search_no_results_key),
        ("doctor: name+specialty only (no rating/loc/reviews)", c_doctor_name_specialty_only),
        ("doctor: completely empty {}", c_doctor_completely_empty),
        ("doctor: rating avg present but count 0", c_doctor_rating_but_zero_count),
        ("doctor: empty review objects {}", c_doctor_empty_review_objects),
        ("doctor: empty/null locations", c_doctor_locations_empty),
        ("partners: both models + missing 'data'", c_partner_models_and_missing_data),
        ("review page: empty results", c_review_page_empty),
    ]

    passed = 0
    for label, fn in cases:
        try:
            fn()
            print(f"  PASS  {label}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {label}  ->  {type(e).__name__}: {e}")
    print(f"\n  robustness: {passed}/{len(cases)} cases passed")
    return passed == len(cases)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run(doctors=None, reviews=None, tol=0.2, skip_robustness=False) -> int:
    """Programmatic entry point (used by ``ratemds-scraper verify``).

    Returns 0 if every check passes, 1 otherwise. ``doctors`` / ``reviews`` may
    be paths; when omitted the merged-then-single output dir is auto-discovered.
    """
    dd, rr = find_default_files()
    doctors_path = doctors or dd
    reviews_path = reviews or rr

    print(f"doctors: {doctors_path}")
    print(f"reviews: {reviews_path}")
    doctor_rows = load_rows(doctors_path)
    review_rows = load_rows(reviews_path)
    print(f"loaded {len(doctor_rows)} doctor rows, {len(review_rows)} review rows")

    results = {}
    results["1 counts"] = check_counts(doctor_rows, review_rows)
    results["2 sub-ratings"] = check_subratings(review_rows, tol)
    results["3 doctor aggregate"] = check_doctor_aggregate(doctor_rows, review_rows, tol)
    if not skip_robustness:
        results["4 robustness"] = check_robustness()

    banner("SUMMARY")
    ok = True
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL'}  check {k}")
        ok = ok and v
    print()
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify RateMDs scrape integrity + robustness")
    dd, rr = find_default_files()
    ap.add_argument("--doctors", default=dd, help=f"doctors.csv (default: {dd})")
    ap.add_argument("--reviews", default=rr, help=f"reviews.csv (default: {rr})")
    ap.add_argument("--tol", type=float, default=0.2,
                    help="tolerance for the rating checks (default 0.2)")
    ap.add_argument("--skip-robustness", action="store_true",
                    help="skip check 4 (the offline parser self-test)")
    args = ap.parse_args(argv)
    return run(doctors=args.doctors, reviews=args.reviews, tol=args.tol,
               skip_robustness=args.skip_robustness)


if __name__ == "__main__":
    sys.exit(main())
