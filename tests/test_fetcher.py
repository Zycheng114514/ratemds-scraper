"""Transport regressions; all subprocesses and network operations are mocked."""
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from ratemds_scraper import config
from ratemds_scraper.fetcher import CloudflareError, Fetcher


def response(body="page", status=200):
    return SimpleNamespace(stdout=f"{body}\n{status}".encode())


def test_default_curl_skips_browser_and_cached_cookie(monkeypatch):
    calls = []
    monkeypatch.setattr(Fetcher, "_load_cf_state", lambda *_: pytest.fail("loaded browser cookie"))
    monkeypatch.setattr(Fetcher, "_refresh_clearance", lambda *a, **kw: pytest.fail("started browser"))
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or response())
    fetcher = Fetcher(delay=(0, 0))
    fetcher.ensure_clearance()
    assert fetcher.get("https://www.ratemds.com/example/") == "page"
    args, kwargs = calls[0]
    assert args[0] == "curl"
    assert args[-2:] == ["--url", "https://www.ratemds.com/example/"]
    assert "shell" not in kwargs


def test_http_202_is_retried_and_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MAX_RETRIES", 2)
    monkeypatch.setattr(config, "BACKOFF_BASE", 0)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: response("challenge", 202))
    fetcher = Fetcher(delay=(0, 0), cache_dir=str(tmp_path))
    with pytest.raises(CloudflareError, match="HTTP 202"):
        fetcher.get("https://www.ratemds.com/example/", use_cache=True)
    assert not list(tmp_path.iterdir())


def test_curl_retries_then_caches_success(monkeypatch, tmp_path):
    replies = iter([response("challenge", 202), response("real data")])
    monkeypatch.setattr(config, "BACKOFF_BASE", 0)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: next(replies))
    fetcher = Fetcher(delay=(0, 0), cache_dir=str(tmp_path))
    url = "https://www.ratemds.com/example/"
    assert fetcher.get(url, use_cache=True) == "real data"
    assert fetcher.get(url, use_cache=True) == "real data"


def test_missing_system_curl_has_actionable_error(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("curl")
    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(CloudflareError, match="install curl or use --transport browser"):
        Fetcher(delay=(0, 0)).get("https://www.ratemds.com/example/")


@pytest.mark.parametrize("body", ["<html>not JSON</html>", "[]"])
def test_json_response_must_be_an_object(monkeypatch, body):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: response(body))
    with pytest.raises(CloudflareError):
        Fetcher(delay=(0, 0)).get_json("https://www.ratemds.com/example/?json=true")


def test_public_json_response(monkeypatch):
    page = {"current_page": 2, "results": []}
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: response(json.dumps(page)))
    assert Fetcher(delay=(0, 0)).get_json("https://www.ratemds.com/example/?json=true") == page


def test_curl_request_interval_is_shared_between_threads(monkeypatch):
    clock = [0.0]
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr("ratemds_scraper.fetcher.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("ratemds_scraper.fetcher.time.sleep", sleep)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: response())
    fetcher = Fetcher(delay=(2, 2))
    with ThreadPoolExecutor(max_workers=3) as executor:
        assert list(executor.map(fetcher.get, ["https://www.ratemds.com/example/"] * 3)) == ["page"] * 3
    assert waits == [0.0, 2.0, 2.0]


def test_browser_transport_remains_available(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CF_STATE", str(tmp_path / "cf.json"))
    fetcher = Fetcher(delay=(0, 0), transport="browser")
    def refresh(*args, **kwargs):
        fetcher._cf_clearance = "test-cookie"
    monkeypatch.setattr(fetcher, "_refresh_clearance", refresh)
    session = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(text="page", status_code=200))
    monkeypatch.setattr(fetcher, "_ensure_session", lambda: session)
    assert fetcher.get("https://www.ratemds.com/example/") == "page"


def test_unknown_transport_is_rejected():
    with pytest.raises(ValueError, match="transport"):
        Fetcher(transport="unknown")
