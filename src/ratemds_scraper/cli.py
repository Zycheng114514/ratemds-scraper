"""
Command-line interface: ``ratemds-scraper <command>``.

Commands
--------
    discover        build the doctor queue from /best-doctors/ search facets
    scrape-doctors  visit each queued doctor + paginate all their reviews
    run-all         discover + scrape every facet, resumable (the usual entry)
    dedupe          drop duplicate rows left by interrupted/resumed runs
    verify          post-scrape integrity + robustness checks

``--specialty`` and ``--gender`` are optional everywhere; omit them to crawl
every doctor. ``run-all`` accepts comma-separated lists for both.
"""

from __future__ import annotations

import os
import logging
import argparse

from . import config


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ratemds-scraper",
        description="Resumable, Cloudflare-aware scraper for RateMDs doctor "
                    "profiles and reviews.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output", default=None, metavar="DIR",
                        help="output directory (default: ./output or $RATEMDS_OUTPUT_DIR)")
    common.add_argument("--delay", type=float, nargs=2, metavar=("MIN", "MAX"),
                        default=[config.DELAY_MIN, config.DELAY_MAX],
                        help="randomized per-request delay range in seconds")
    common.add_argument("--cache", action="store_true",
                        help="gzip-cache raw HTML and reuse it (great for re-parsing)")
    common.add_argument("--transport", choices=["curl", "browser"], default="curl",
                        help="curl (default): system curl, no display required; browser: "
                             "Cloudflare cookie harvest followed by curl_cffi")
    common.add_argument("--target", choices=["desktop", "server"], default="desktop",
                        help="desktop (default): HEADED browser for the Cloudflare "
                             "harvest -- robust, needs a display (use xvfb-run on a "
                             "headless box). server: headless browser, which "
                             "Cloudflare often detects.")
    common.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS,
                        help="concurrent worker threads sharing one cf_clearance cookie "
                             "(watch per-IP rate limits)")
    common.add_argument("--states", default=None,
                        help="comma-separated state slugs to restrict the crawl "
                             "(e.g. 'ca,tx,fl' or 'dc/washington'); used to shard work "
                             "across machines. Default: all 50 states + DC.")

    d = sub.add_parser("discover", parents=[common], help="build doctor queue (per state)")
    d.add_argument("--specialty", default=None,
                   help="RateMDs specialty slug (e.g. dermatologist, family-gp, "
                        "cardiologist); omit to crawl all specialties")
    d.add_argument("--gender", choices=["M", "F"], default=None,
                   help="restrict to one gender; omit for all")
    d.add_argument("--state", default=None,
                   help="single state slug (overrides --states); default = all states")
    d.add_argument("--max-page", type=int, default=None,
                   help="cap pages per state (default: use the site's total_pages)")

    s = sub.add_parser("scrape-doctors", parents=[common], help="scrape queued doctors + reviews")
    s.add_argument("--specialty", default=None, help="filter the queue by specialty slug")
    s.add_argument("--gender", choices=["M", "F"], default=None, help="filter the queue by gender")
    s.add_argument("--limit", type=int, default=None, help="max doctors this run")

    r = sub.add_parser("run-all", parents=[common],
                       help="discover + scrape every facet (resumable)")
    r.add_argument("--specialty", default=None,
                   help="comma-separated specialty slugs; omit to crawl all specialties")
    r.add_argument("--gender", default=None,
                   help="comma-separated genders (M,F); omit for all")

    sub.add_parser("dedupe", help="drop duplicate doctor/review rows from interrupted runs")

    v = sub.add_parser("verify", help="post-scrape integrity + robustness checks")
    v.add_argument("--output", default=None, metavar="DIR",
                   help="output directory to verify (default: ./output[/combined])")
    v.add_argument("--doctors", default=None, help="path to doctors.csv")
    v.add_argument("--reviews", default=None, help="path to reviews.csv")
    v.add_argument("--tol", type=float, default=0.2, help="tolerance for rating cross-checks")

    a = sub.add_parser("analyze", help="quick read-only descriptive summary of the scrape")
    a.add_argument("--output", default=None, metavar="DIR",
                   help="output directory to analyze (default: ./output[/combined])")
    a.add_argument("--doctors", default=None, help="path to doctors.csv")
    a.add_argument("--reviews", default=None, help="path to reviews.csv")
    a.add_argument("--top", type=int, default=10, help="rows per top-N table")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if getattr(args, "output", None):
        config.set_output_dir(args.output)

    # verify / analyze have their own entry points and need no fetcher / network
    if args.cmd == "verify":
        from . import verify
        verify.run(doctors=args.doctors, reviews=args.reviews, tol=args.tol)
        return

    if args.cmd == "analyze":
        from . import analyze
        analyze.run(doctors=args.doctors, reviews=args.reviews, top=args.top)
        return

    from . import scraper

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.STATE_DIR, exist_ok=True)

    if args.cmd == "dedupe":      # no network/fetcher needed
        scraper.dedupe()
        return

    fetcher = scraper.Fetcher(
        delay=tuple(args.delay),
        headless=(args.target == "server"),
        cache_dir=config.HTML_CACHE_DIR if args.cache else None,
        transport=args.transport,
    )
    states_list = _split(getattr(args, "states", None))

    try:
        if args.cmd == "discover":
            states = ([args.state] if args.state else None) or states_list
            scraper.discover(fetcher, args.specialty, args.gender, states=states,
                             max_page=args.max_page, use_cache=args.cache, workers=args.workers)
        elif args.cmd == "scrape-doctors":
            scraper.scrape_doctors(fetcher, specialty=args.specialty, gender=args.gender,
                                   limit=args.limit, use_cache=args.cache,
                                   workers=args.workers, states=states_list)
        elif args.cmd == "run-all":
            scraper.run_all(fetcher, specialties=_split(args.specialty),
                            genders=_split(args.gender), states=states_list,
                            use_cache=args.cache, workers=args.workers)
    except (scraper.IncompleteScrapeError, scraper.CloudflareError) as exc:
        logging.getLogger("ratemds").error("Crawl incomplete; rerun to resume: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
