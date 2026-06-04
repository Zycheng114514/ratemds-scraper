"""
Offline tests for the parser and URL helpers. No network access.

The parser is exercised against synthetic JSON blobs embedded exactly the way
RateMDs embeds them (``window.DATA.<var> = JSON.parse("…escaped…")``), so these
prove the extraction is robust to missing/empty fields without hitting the site.
"""

import json

from ratemds_scraper import parse, config, verify


def embed(var: str, obj: dict) -> str:
    """Reproduce a RateMDs page's embedded blob for ``var`` holding ``obj``."""
    inner = json.dumps(obj)            # JSON text
    raw = json.dumps(inner)[1:-1]      # escape as a JS string literal, drop quotes
    return f'<html><script>window.DATA.{var} = JSON.parse("{raw}");</script></html>'


# --------------------------------------------------------------------------- #
# URL / facet helpers
# --------------------------------------------------------------------------- #
def test_state_label():
    assert config.state_label("az") == "AZ"
    assert config.state_label("dc/washington") == "DC"


def test_search_url_optional_filters():
    # No filters: bare per-state URL.
    assert config.search_url("ca") == "https://www.ratemds.com/best-doctors/ca/"
    # Specialty + gender become query params; page 1 omits &page=.
    u = config.search_url("ca", specialty="dermatologist", gender="F")
    assert "specialty=dermatologist" in u and "gender=F" in u and "page=" not in u
    # Page > 1 appends &page=.
    u2 = config.search_url("ca", specialty="dermatologist", page=3)
    assert u2.endswith("page=3")


def test_facet_key_defaults_to_all():
    assert config.facet_key("ca") == "all-all-CA"
    assert config.facet_key("tx", "dermatologist", "M") == "dermatologist-M-TX"


def test_review_page_url():
    base = "https://www.ratemds.com/doctor-ratings/123/Dr-X.html/"
    assert config.review_page_url(base, 2, "ratemds").endswith("?page=2")
    assert config.review_page_url(base, 2, "partners").endswith("?third_party_page=2")


# --------------------------------------------------------------------------- #
# Parser robustness
# --------------------------------------------------------------------------- #
def test_looks_like_challenge():
    assert parse.looks_like_challenge("<html><body>Just a moment...</body></html>")
    assert parse.looks_like_challenge("<div class='challenge-platform'></div>")
    assert not parse.looks_like_challenge(embed("doctor_list_props", {"doctorPage": {}}))


def test_doctor_name_specialty_only():
    html = embed("doctorDetailProps",
                 {"doctor": {"full_name": "Dr. Jane Roe",
                             "specialty_name": "Dermatologist"}})
    dp = parse.parse_doctor_page(html, gender="F")
    row = dp.doctor_row
    assert row["name"] == "Dr. Jane Roe"
    assert row["overall_rating"] == parse.NA
    assert row["total_reviews"] == 0


def test_doctor_empty_fields_does_not_crash():
    # A doctor object present but with no fields filled in -> still a valid row.
    dp = parse.parse_doctor_page(embed("doctorDetailProps", {"doctor": {}}))
    assert dp.doctor_row
    assert dp.doctor_row["total_reviews"] == 0


def test_missing_blob_raises():
    try:
        parse.parse_doctor_page("<html>no blob here</html>")
    except parse.BlobNotFound:
        return
    raise AssertionError("expected BlobNotFound when the blob is absent")


def test_parser_robustness_suite():
    # The packaged offline self-test feeds a battery of empty/minimal blobs
    # through the real parser and asserts none of them crash.
    assert verify.check_robustness() is True


def test_column_schemas_are_stable():
    # Guard against accidental column drift in the public CSV schema.
    assert parse.DOCTOR_COLUMNS[:3] == ["doctor_id", "name", "specialty"]
    assert "latitude" in parse.DOCTOR_COLUMNS and "longitude" in parse.DOCTOR_COLUMNS
    assert "review_type" in parse.REVIEW_COLUMNS
