# Contributing

Thanks for your interest in improving `ratemds-scraper`.

## Getting set up

```bash
git clone https://github.com/Zycheng114514/ratemds-scraper
cd ratemds-scraper
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before you open a PR

- `pytest` passes (the parser tests are offline and need no network).
- `ruff check .` is clean.
- New behaviour has a test. The parser is the heart of this project — if you
  touch `parse.py`, add a synthetic-blob case to `tests/test_parse.py` rather
  than relying on live pages.
- Keep the crawl **polite**: don't lower the default delays or raise default
  concurrency in a PR; those are per-IP safety limits.

## Design notes

- **Parse the JSON blob, not the DOM.** Both page types embed
  `window.DATA.<name> = JSON.parse("…")`; decode that. DOM selectors are
  documented only as a fallback.
- **Everything is resumable.** New work units must be recorded in the ledger so
  an interrupted run never repeats or loses them.
- **Fail soft on data, loud on bugs.** A missing field or a count mismatch is an
  *anomaly* (logged, crawl continues); a programming error should surface.

See [`docs/architecture.md`](docs/architecture.md) for the module layout.

## Scope & ethics

This is a research/educational scraper. Please keep contributions aligned with
responsible use: respect the site's Terms and `robots.txt`, and don't add
features whose primary purpose is evading rate limits at scale or harvesting
personal data for abuse.

By contributing you agree your contributions are licensed under the project's
[MIT License](LICENSE).
