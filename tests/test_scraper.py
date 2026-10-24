"""Pagination, completion-ledger, and CLI regression tests; no live requests."""
import csv
import json
import subprocess
import sys

import pytest

from ratemds_scraper import cli, config, scraper
from ratemds_scraper.fetcher import CloudflareError
from ratemds_scraper.ledger import Ledger


def embed(name, data):
    return f"window.DATA.{name} = JSON.parse({json.dumps(json.dumps(data))});"


def profile(ratemds=1, partners=2):
    return {"doctor": {"id": 1, "full_name": "Dr. Test", "specialty_name": "Pediatrician",
                       "gender": "F", "rating": {"count": ratemds + partners}},
            "ratingsPage": {"count": ratemds, "current_page": 1, "total_pages": 1,
                            "results": [{"id": 7, "comment": "RateMDs review"}] if ratemds else []},
            "thirdPartyRatingsPage": {"count": partners, "current_page": 1,
                                      "total_pages": 2 if partners > 1 else 1,
                                      "results": [{"model": "review", "data": {"id": 7}}] if partners else []}}


def read_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def output(tmp_path):
    previous = config.OUTPUT_DIR
    config.set_output_dir(str(tmp_path))
    yield tmp_path
    config.set_output_dir(previous)


def queue():
    with open(config.QUEUE_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, ["doctor_id", "profile_url", "state", "gender", "facet"])
        writer.writeheader()
        writer.writerow({"doctor_id": 1, "profile_url": "https://www.ratemds.com/doctor-ratings/test/",
                         "state": "VT", "gender": "F", "facet": "pediatrician-all-VT"})


class FakeFetcher:
    def __init__(self, data=None, partner_page=2):
        self.data = profile() if data is None else data
        self.partner_page = partner_page
        self.calls = []

    def ensure_clearance(self):
        pass

    def get(self, url, **kwargs):
        self.calls.append(url)
        return embed("doctorDetailProps", self.data)

    def get_json(self, url, **kwargs):
        self.calls.append(url)
        assert url.endswith("?third_party_page=2&json=true")
        return {"current_page": self.partner_page, "count": 2, "total_pages": 2,
                "results": [{"model": "review", "data": {"id": 8}}]}


def test_all_partner_pages_and_source_scoped_ids_are_preserved(output):
    queue()
    fetcher = FakeFetcher()
    scraper.scrape_doctors(fetcher)
    rows = read_rows(config.REVIEWS_CSV)
    assert len(rows) == 3
    assert {(r["review_type"], r["review_id"]) for r in rows} == {
        ("RateMDs", "7"), ("Our Partners", "7"), ("Our Partners", "8")}
    ledger = Ledger(config.DONE_LEDGER)
    assert ledger.has("doctor:1")
    ledger.close()
    previous_calls = list(fetcher.calls)
    scraper.scrape_doctors(fetcher)
    assert fetcher.calls == previous_calls


def test_incomplete_partner_page_remains_retryable(output):
    queue()
    fetcher = FakeFetcher(partner_page=1)
    with pytest.raises(scraper.IncompleteScrapeError):
        scraper.scrape_doctors(fetcher)
    ledger = Ledger(config.DONE_LEDGER)
    assert not ledger.has("doctor:1")
    ledger.close()
    fetcher.partner_page = 2
    scraper.scrape_doctors(fetcher)
    scraper.dedupe()
    assert len(read_rows(config.DOCTORS_CSV)) == 1
    assert len(read_rows(config.REVIEWS_CSV)) == 3


def test_total_count_mismatch_does_not_mark_doctor_done(output):
    queue()
    data = profile(ratemds=1, partners=0)
    data["doctor"]["rating"]["count"] = 9
    with pytest.raises(scraper.IncompleteScrapeError):
        scraper.scrape_doctors(FakeFetcher(data))
    ledger = Ledger(config.DONE_LEDGER)
    assert not ledger.has("doctor:1")
    ledger.close()


@pytest.mark.parametrize("second_page", ["<html>missing blob</html>", None])
def test_missing_or_repeated_ratemds_page_is_incomplete(output, second_page):
    queue()
    data = profile(ratemds=2, partners=0)
    data["ratingsPage"]["total_pages"] = 2
    fetcher = FakeFetcher(data)
    original_get = fetcher.get
    fetcher.get = lambda url, **kw: (second_page or embed("doctorDetailProps", data)) if "?page=" in url else original_get(url)
    with pytest.raises(scraper.IncompleteScrapeError):
        scraper.scrape_doctors(fetcher)
    ledger = Ledger(config.DONE_LEDGER)
    assert not ledger.has("doctor:1")
    ledger.close()


def test_failed_discovery_is_not_an_empty_success(output):
    class Blocked(FakeFetcher):
        def get(self, url, **kwargs):
            raise CloudflareError("HTTP 202")
    with pytest.raises(scraper.IncompleteScrapeError, match="search pages failed"):
        scraper.run_all(Blocked(), specialties=["pediatrician"], states=["vt"])
    assert read_rows(config.QUEUE_CSV) == []


def test_discovery_keeps_successful_states_and_retries_missing_pages(output):
    class Searches(FakeFetcher):
        blocked = True
        def get(self, url, **kwargs):
            second = "page=2" in url
            if second and self.blocked:
                raise CloudflareError("HTTP 202")
            state = "vt" if "/vt/" in url else "ak"
            return embed("doctor_list_props", {"doctorPage": {
                "current_page": 2 if second else 1, "total_pages": 2 if state == "vt" else 1,
                "count": 2 if state == "vt" else 1,
                "results": [{"id": (2 if second else 1) if state == "vt" else 3,
                             "url": "/doctor-ratings/test/"}]}})
    fetcher = Searches()
    with pytest.raises(scraper.IncompleteScrapeError):
        scraper.discover(fetcher, states=["vt", "ak"], workers=2)
    assert len(read_rows(config.QUEUE_CSV)) == 2
    fetcher.blocked = False
    scraper.discover(fetcher, states=["vt", "ak"], workers=2)
    assert len(read_rows(config.QUEUE_CSV)) == 3


def test_cli_defaults_to_curl_and_returns_nonzero_for_incomplete_run(output, monkeypatch):
    transports = []
    monkeypatch.setattr(scraper, "Fetcher", lambda **kw: transports.append(kw["transport"]) or FakeFetcher())
    def incomplete(*args, **kwargs):
        raise scraper.IncompleteScrapeError("pending pages")
    monkeypatch.setattr(scraper, "run_all", incomplete)
    assert cli.main(["run-all", "--output", str(output)]) == 2
    assert transports == ["curl"]


def test_python_module_propagates_failure_exit_status(output):
    result = subprocess.run([sys.executable, "-m", "ratemds_scraper", "scrape-doctors",
                             "--output", str(output)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "Crawl incomplete" in result.stderr
