# Architecture

A short tour of how `ratemds-scraper` is put together.

## The big idea

RateMDs renders from JSON embedded in public pages. The scraper fetches these
pages using ordinary system curl by default and reads the embedded JSON rather
than DOM selectors. Partner pagination requests the public profile's JSON
representation, exactly as the site's pagination widget does.

An explicit `--transport browser` retains the earlier path: harvest a
`cf_clearance` cookie and User-Agent with a headed browser, then reuse them in
`curl_cffi` requests. Curl mode needs neither cookies nor a display.

## Module layout

```
src/ratemds_scraper/
├── config.py     paths, crawl facets, URL builders, politeness defaults
├── fetcher.py    system curl or optional browser/curl_cffi transport (thread-safe)
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
(`?third_party_page=n&json=true`) independently, writing one row per review.
It validates page numbers and reconciles the total and per-source counts. A
missing page or count mismatch is logged as an anomaly and leaves that doctor
pending. Dedupe keys include the source because numeric IDs can overlap.

`run-all` does discover-then-scrape for every requested facet and finishes with
a `dedupe`. Discovery failures do not prevent it from scraping doctors already
queued; after saving the available data it raises `IncompleteScrapeError` if
any requested work remains unfinished. The CLI maps this to exit status 2,
including when invoked with `python -m ratemds_scraper`.

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

`--workers N` runs a thread pool. In curl mode, workers share one randomized
request interval, so adding threads does not multiply the configured request
rate. In browser mode, workers share a clearance cookie and each keeps its own
`curl_cffi` session. Distributed workers use disjoint state assignments (see
[distributed.md](distributed.md)).

## Politeness & failure handling

Randomized per-request delay, bounded retries with exponential backoff, and a
**global cooldown**: when one worker is told to slow down, every worker honours
it. HTTP 202, other non-200 statuses, and recognized challenge pages are rejected.
In browser mode, a bare 403
(cookie rejected) triggers an immediate re-harvest; a persistent challenge
triggers a re-harvest after the first retry (a fresh cookie resets Cloudflare's
rate window).
