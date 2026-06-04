# Architecture

A short tour of how `ratemds-scraper` is put together.

## The big idea

RateMDs sits behind a Cloudflare JS challenge and renders from a JSON blob
embedded in each page. So the scraper:

1. solves the challenge **once** with a real browser to get a `cf_clearance`
   cookie + User-Agent, then
2. does the bulk of its fetching with fast `curl_cffi` requests (Chrome TLS
   impersonation) that reuse that cookie, and
3. reads the **embedded JSON blob** rather than scraping fragile HTML.

## Module layout

```
src/ratemds_scraper/
├── config.py     paths, crawl facets, URL builders, politeness defaults
├── fetcher.py    Fetcher: Cloudflare harvest + curl_cffi bulk fetch (thread-safe)
├── parse.py      pure HTML/JSON -> rows (no network; fully unit-testable)
├── ledger.py     Ledger (resume), CsvSink (append-only CSV), anomaly log
├── scraper.py    orchestration: discover / scrape_doctors / dedupe / run_all
├── cli.py        argparse front-end (the `ratemds-scraper` command)
├── verify.py     post-scrape integrity checks + offline parser self-test
└── fleet/        optional multi-machine sharding + lossless merge
```

The dependency direction is one-way: `cli → scraper → {fetcher, parse, ledger}
→ config`. `parse.py` imports nothing from the package except… nothing — it is
pure and offline, which is why the tests can hammer it with no network.

## The two phases

**Discover** walks `/best-doctors/<state>/` search facets (optionally filtered
by specialty/gender), reads each blob's `total_pages`, and writes a deduped
queue of doctor stubs (`doctors_queue.csv`). State and gender are stamped
straight from the facet.

**Scrape** reads the queue, visits each doctor profile, writes the doctor row,
then paginates RateMDs reviews (`?page=n`) and "Our Partners" reviews
(`?third_party_page=n`) independently, writing one row per review. It reconciles
the number of scraped reviews against the doctor's stated total and logs a count
mismatch as an anomaly.

`run-all` does discover-then-scrape for every requested facet and finishes with
a `dedupe`.

## Resumability

A single append-only JSONL ledger (`state/done.jsonl`) records completed work
units — search pages (`search:<facet>:<page>`) and doctors (`doctor:<id>`). On
startup the ledger is loaded into a set; completed units are skipped. CSVs are
append-only, and a `dedupe` pass keyed on stable ids cleans up the few duplicate
rows an interrupted-mid-doctor run can leave behind.

A per-doctor failure (e.g. a transient Cloudflare give-up) is recorded as an
anomaly and the doctor is left **un-done**, so simply re-running picks it up —
the retry is automatic.

## Concurrency

`--workers N` runs a thread pool. The `cf_clearance` cookie is bound to
`{User-Agent, IP}`, not to a browser session, so all N threads share one cookie
and each keeps its own `curl_cffi` session. Throughput is ultimately capped by
**per-IP** rate limiting, not by thread count — which is why going faster means
more IPs (see [distributed.md](distributed.md)), not more workers.

## Politeness & failure handling

Randomized per-request delay, bounded retries with exponential backoff, and a
**global cooldown**: when one worker is told to slow down (429/challenge), every
worker honours it, so the aggregate request rate actually drops. A bare 403
(cookie rejected) triggers an immediate re-harvest; a persistent challenge
triggers a re-harvest after the first retry (a fresh cookie resets Cloudflare's
rate window).
