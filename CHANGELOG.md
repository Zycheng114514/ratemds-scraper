# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `analyze` command: a read-only descriptive summary of a scrape (totals and
  reviews-per-doctor distribution, RateMDs vs "Our Partners" source split, star
  and overall-rating distributions, top specialties/states, and review volume by
  year). Reuses `verify`'s loaders so the two stay in sync.

### Fixed
- "Our Partners" reviews are no longer collected redundantly. The partner feed
  (`?third_party_page=n`) re-serves the same recent batch regardless of the
  requested page, so the scraper now stops paginating once a page contributes no
  new `review_id`s, and de-duplicates reviews per doctor by `review_id` before
  writing. This removes duplicate partner rows from raw output and eliminates the
  spurious "scraped more than expected" over-capture anomalies.

## [0.1.0] — 2026-06-04

Initial public release.

### Added
- `discover` / `scrape-doctors` / `run-all` / `dedupe` / `verify` commands and a
  `ratemds-scraper` console entry point.
- JSON-blob extraction for doctor and search pages (no DOM scraping on the happy
  path), covering profile fields, geocoordinates, aggregate + per-review
  sub-ratings, and both RateMDs and "Our Partners" reviews.
- Layered Cloudflare handling: one-time headed-browser `cf_clearance` harvest
  (nodriver, Playwright fallback) reused across `curl_cffi` bulk fetches, with
  automatic re-harvest and a global rate-limit cooldown.
- Resumable JSONL ledger, append-only CSV sinks, and an anomaly log.
- Optional, host-agnostic distributed fleet (`fleet.sh` / `worker.sh` /
  `runloop.sh` / `combine.sh`) driven by a simple `fleet.conf`, plus a lossless
  merge that dedupes by id.
- Offline parser robustness tests.
