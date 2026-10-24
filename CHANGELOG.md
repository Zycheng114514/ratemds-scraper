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
- Use ordinary system `curl` by default on hosts where browser impersonation
  returns HTTP 202. The existing browser/curl_cffi path remains available with
  `--transport browser`; default delays and concurrency are unchanged.
- Fetch all "Our Partners" review pages through the public profile's JSON
  response (`?third_party_page=n&json=true`). HTML embeds page 1 regardless of
  the requested partner page, so stopping at repeated HTML truncated the feed.
- Scope review deduplication by source and ID, preserving distinct reviews with
  the same numeric ID in RateMDs and partner feeds.
- Keep doctors with missing pages or unreconciled counts pending. Report failed
  discovery and incomplete scrapes through exit status 2; the fleet loop retries
  those exits instead of treating zero processed doctors as convergence.

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
