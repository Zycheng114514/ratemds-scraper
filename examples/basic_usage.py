"""
Minimal library usage. Run with:  python examples/basic_usage.py

Writes ./output/doctors.csv and ./output/reviews.csv for dermatologists in
Wyoming (a small state, so it finishes quickly). On a headless machine, wrap the
process in `xvfb-run -a` so the Cloudflare-harvest browser has a display.
"""

from ratemds_scraper import Fetcher, run_all

if __name__ == "__main__":
    fetcher = Fetcher(delay=(1.0, 3.0))      # polite randomized delay
    run_all(
        fetcher,
        specialties=["dermatologist"],
        states=["wy"],
        workers=4,
    )
    print("Done -> ./output/doctors.csv and ./output/reviews.csv")
    print("Tip: run `ratemds-scraper verify` to reconcile counts.")
