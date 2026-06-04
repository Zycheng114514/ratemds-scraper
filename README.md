# ratemds-scraper

A resumable, Cloudflare-aware scraper for [RateMDs](https://www.ratemds.com)
doctor profiles and their reviews. It produces two tidy CSVs — one row per
**doctor**, one row per **review** — for any specialty, gender and US state you
ask for.

It is built around two ideas that make it fast and robust:

- **Read the data, not the DOM.** Every RateMDs page embeds a complete JSON blob
  (`window.DATA.doctorDetailProps` / `doctor_list_props`). The scraper decodes
  that blob and reads structured fields straight out of it, so it does not break
  when the site reshuffles its HTML.
- **Harvest the Cloudflare cookie once, then bulk-fetch.** A real (headed)
  browser solves the Cloudflare JS challenge a single time to obtain a
  `cf_clearance` cookie, which is then reused across many fast
  [`curl_cffi`](https://github.com/lexiforest/curl_cffi) requests with a
  browser-grade TLS fingerprint. It transparently re-harvests on a 403.

> **Use responsibly.** This is a research/educational tool. Respect RateMDs'
> Terms of Service and `robots.txt`, scrape at a polite rate (the defaults are
> conservative), and don't republish personal data in ways that violate
> applicable laws. You are responsible for how you use it.

---

## Features

- One row per doctor + one row per review (RateMDs **and** "Our Partners"), with
  per-review sub-ratings, geocoordinates, and clinic affiliations.
- Fully **resumable** — a JSONL ledger records completed search pages and
  doctors; kill it anytime and it picks up where it left off.
- **Cloudflare** handled automatically (headed-browser harvest + `curl_cffi`
  bulk fetch + auto re-harvest), with a Playwright fallback.
- Polite by default: randomized delays, global cooldown on rate-limit, bounded
  retries with exponential backoff.
- Thread-pooled (`--workers`) sharing a single cookie — no browser-per-thread.
- **Distributed mode** to shard a crawl across many machines/IPs and merge the
  results losslessly.
- A `verify` command that reconciles review counts and cross-checks ratings.

## Install

```bash
pip install ratemds-scraper
# optional Playwright fallback browser:
pip install "ratemds-scraper[playwright]" && playwright install chromium
```

The headed-browser harvest needs Chrome/Chromium. On a headless server, run it
under `xvfb-run` (see [Running on a server](#running-on-a-server)).

From source:

```bash
git clone https://github.com/Zycheng114514/ratemds-scraper
cd ratemds-scraper
pip install -e ".[dev]"
```

## Quickstart

```bash
# Everything, every state (writes ./output/doctors.csv + ./output/reviews.csv)
ratemds-scraper run-all

# A focused slice: dermatologists in California and Texas
ratemds-scraper run-all --specialty dermatologist --states ca,tx

# Two specialties at once, women only
ratemds-scraper run-all --specialty dermatologist,family-gp --gender F

# Check what you got
ratemds-scraper verify
```

`--specialty` is just the slug RateMDs uses in `?specialty=` (e.g.
`dermatologist`, `family-gp`, `cardiologist`, `dentist`). Omit it to crawl every
specialty; omit `--gender` for all.

## Commands

| Command | What it does |
| --- | --- |
| `run-all` | discover + scrape every requested facet, then dedupe (the usual entry point) |
| `discover` | build the doctor queue from `/best-doctors/` search facets only |
| `scrape-doctors` | visit each queued doctor and paginate all their reviews |
| `dedupe` | drop duplicate rows left by an interrupted/resumed run |
| `verify` | post-scrape integrity + robustness checks |

Common flags: `--states`, `--workers`, `--delay MIN MAX`, `--target {desktop,server}`,
`--cache`, `--output DIR`. Run `ratemds-scraper <command> -h` for the full list.

Use it as a library, too:

```python
from ratemds_scraper import Fetcher, run_all

run_all(Fetcher(), specialties=["dermatologist"], states=["ca"], workers=4)
```

See [`examples/basic_usage.py`](examples/basic_usage.py) for a runnable
end-to-end demo (a small state, so it finishes quickly).

## Output

Two CSVs in `./output/` (override with `--output` or `RATEMDS_OUTPUT_DIR`):

- **`doctors.csv`** — one row per doctor: id, name, specialty, location,
  latitude/longitude, clinic id + affiliations, overall rating, the four
  aggregate sub-ratings (staff / punctuality / helpfulness / knowledge), and
  review counts.
- **`reviews.csv`** — one row per review: id, stars, title, body, reviewer,
  date, helpful votes, per-review sub-ratings, and `review_type`
  (`RateMDs` or `Our Partners`).

See [`docs/data-elements.md`](docs/data-elements.md) for the full field map and
[`docs/architecture.md`](docs/architecture.md) for how it all fits together.

## Running on a server

Cloudflare detects headless Chrome, so the harvest runs a **headed** browser. On
a machine with no display, give it a virtual one:

```bash
xvfb-run -a ratemds-scraper run-all --states ca --target desktop
```

`--target server` uses a truly headless browser (often detected); prefer
`xvfb-run … --target desktop` on headless boxes.

## Distributed crawling

RateMDs rate-limits per IP, so the one real way to go faster is **more IPs**.
The `fleet` tooling shards a crawl across machines (each owning a disjoint set of
states) and merges their outputs, deduping by id. See
[`docs/distributed.md`](docs/distributed.md). In short:

```bash
# from the scripts dir, on your control machine
cp fleet.conf.example fleet.conf      # list: <ssh-host> <states> per line
./fleet.sh deploy                     # install on each host
./fleet.sh start                      # start every worker
./fleet.sh status                     # progress
./combine.sh                          # pull + merge -> output/combined/
```

The fleet scripts ship inside the package at
`ratemds_scraper/fleet/scripts/`.

## How it handles Cloudflare

1. A real browser ([nodriver](https://github.com/ultrafunkamsterdam/nodriver),
   Playwright fallback) loads the site once and harvests the `cf_clearance`
   cookie + the exact User-Agent.
2. Those are reused on fast `curl_cffi` requests (Chrome TLS impersonation) for
   the bulk of the crawl. The cookie is bound to `{User-Agent, IP}`, not to a
   browser session, so many worker threads can share one cookie.
3. On a `403`, it re-harvests; on `429`/challenge, it sets a **global cooldown**
   so every worker backs off, then re-harvests only if it persists.

## Development

```bash
pip install -e ".[dev]"
pytest            # offline parser tests (no network)
ruff check .
```

## License

[MIT](LICENSE).
