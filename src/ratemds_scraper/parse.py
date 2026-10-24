"""
parse.py -- Pure extraction/mapping functions for RateMDs pages.

No network access here. Everything operates on an HTML string (a saved file or a
freshly fetched page), so these functions are fully unit-testable offline (see
``tests/test_parse.py``).

The key insight: both RateMDs page types embed a complete JSON blob in a
``<script>`` tag via ``window.DATA.<name> = JSON.parse("...escaped json...")``.
We decode that blob and read structured data straight out of it instead of
scraping fragile DOM/CSS. DOM selectors are documented in
``docs/data-elements.md`` as a fallback only.

Blob names:
    * search pages : window.DATA.doctor_list_props
    * doctor pages : window.DATA.doctorDetailProps
"""

from __future__ import annotations

import re
import json
from datetime import datetime
from dataclasses import dataclass, asdict, field

SITE = "https://www.ratemds.com"

# --------------------------------------------------------------------------- #
# Output schemas (column order matters for the CSV writers)
# --------------------------------------------------------------------------- #
DOCTOR_COLUMNS = [
    "doctor_id",
    "name",
    "specialty",
    "location",
    "profile_url",
    "gender",
    "doctor_state",
    "latitude",
    "longitude",
    "location_id",
    "hospital_affiliation_name",
    "hospital_affiliation_address",
    "overall_rating",
    "doctor_staff_rating",
    "doctor_punctuality_rating",
    "doctor_helpfulness_rating",
    "doctor_knowledge_rating",
    "total_reviews",
    "ratemds_count",
    "our_partners_count",
]

REVIEW_COLUMNS = [
    "doctor_id",
    "doctor_name",
    "review_id",
    "review_type",      # "RateMDs" | "Our Partners"
    "stars",
    "title",
    "content",
    "reviewer",
    "date",
    "helpful",
    "telehealth",        # RateMDs: appointment_type_display (e.g. "In-person"); partners: NA
    "staff",
    "punctuality",
    "helpfulness",
    "knowledge",
]

NA = "NA"


# --------------------------------------------------------------------------- #
# Blob extraction
# --------------------------------------------------------------------------- #
class BlobNotFound(Exception):
    """Raised when the expected window.DATA.<name> blob is missing.

    Most often this means the page never made it past the Cloudflare JS
    challenge (so we got the "Just a moment..." interstitial instead of the
    real page), or the page layout changed.
    """


def extract_blob(html: str, var_name: str) -> dict:
    """Decode ``window.DATA.<var_name> = JSON.parse("...")`` into a dict.

    The argument to JSON.parse is a JavaScript string literal whose contents are
    themselves JSON, escaped with \\uXXXX sequences. So we decode twice:
      1. json.loads('"' + raw + '"')  -> un-escape the JS string literal
      2. json.loads(<that>)           -> parse the actual JSON object
    """
    pat = re.compile(
        r"window\.DATA\." + re.escape(var_name) + r"\s*=\s*JSON\.parse\(\"(.*?)\"\);",
        re.S,
    )
    m = pat.search(html)
    if not m:
        raise BlobNotFound(var_name)
    raw = m.group(1)
    js_string = json.loads('"' + raw + '"')      # step 1: un-escape
    return json.loads(js_string)                  # step 2: parse json


def looks_like_challenge(html: str) -> bool:
    """Heuristic: did we get a Cloudflare challenge page instead of content?"""
    head = html[:4000].lower()
    return (
        "just a moment" in head
        or "cf-browser-verification" in head
        or "challenge-platform" in head
        or "enable javascript and cookies to continue" in head
    )


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def abs_url(path: str | None) -> str:
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return SITE + path


_STATE_RE = re.compile(r"-([A-Za-z]{2})(?:-us)?(?:\.html)?/?(?:[?#].*)?$")


def state_from_url(url: str) -> str:
    """Extract the 2-letter state code from a profile URL (positional heuristic).

    Handles both URL forms RateMDs uses:
        /doctor-ratings/dr-angelo-ayar-tamarac-fl-us/        -> FL
        /doctor-ratings/2860674/Dr-...-Portland-OR.html/     -> OR

    This is only a *fallback*: the URL pattern is not a guaranteed field (it grabs
    the two letters before ``-us/`` or ``.html``), so prefer ``state_from_location``
    when location data is available. See ``pick_state``.
    """
    if not url:
        return ""
    # strip query/fragment first
    base = url.split("?")[0].split("#")[0]
    m = _STATE_RE.search(base)
    return m.group(1).upper() if m else ""


def state_from_location(loc: dict | None) -> str:
    """The reliable source: the clinic's ``city.province_slug`` from the JSON
    blob (e.g. 'fl' -> 'FL'). Empty when the doctor has no location object."""
    if not loc:
        return ""
    city = loc.get("city") or {}
    return (city.get("province_slug") or "").upper().strip()


def first_with_coords(*locs: dict | None) -> dict | None:
    """First location object that carries usable lat/long, or None.

    RateMDs embeds exact coordinates in ``location.{latitude,longitude}``. We
    prefer the primary clinic but fall back to any displayed affiliation that
    has coordinates."""
    for loc in locs:
        if (
            isinstance(loc, dict)
            and isinstance(loc.get("latitude"), (int, float))
            and isinstance(loc.get("longitude"), (int, float))
        ):
            return loc
    return None


def loc_coords(loc: dict | None) -> tuple:
    """``(latitude, longitude, location_id)`` from a clinic location object.

    Returns ``NA`` for any piece that is missing. Doctors with no usable
    location yield ``(NA, NA, NA)`` and are simply excluded from any
    geographic / nearest-neighbor analysis downstream."""
    if not isinstance(loc, dict):
        return NA, NA, NA
    lat = loc.get("latitude")
    lng = loc.get("longitude")
    lid = loc.get("id")
    return (
        lat if isinstance(lat, (int, float)) else NA,
        lng if isinstance(lng, (int, float)) else NA,
        lid if lid not in (None, "") else NA,
    )


def pick_state(url: str, loc: dict | None) -> str:
    """Doctor state, robustly: prefer the structured province_slug from the JSON,
    fall back to the positional URL regex when the location is missing."""
    return state_from_location(loc) or state_from_url(url)


def _fmt_date(iso: str | None) -> str:
    """ISO timestamp -> 'June 2, 2017'.  Falls back to the raw string."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        # try trimming to date portion
        try:
            dt = datetime.fromisoformat(iso[:19])
        except Exception:
            return iso
    # %-d is non-portable on some platforms; build day without zero-pad manually
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _affiliation_name(loc: dict | None) -> str:
    """The clinic/affiliation NAME (a separate HTML element / JSON field)."""
    if not loc:
        return ""
    return (loc.get("name") or "").strip()


def _affiliation_address(loc: dict | None) -> str:
    """The clinic ADDRESS line (everything except the name), e.g.
    "5636 E La Palma Ave., Suite C, Anaheim, CA, United States, 92807".
    """
    if not loc:
        return ""
    address = (loc.get("address") or "").strip()
    suite = (loc.get("suite") or "").strip()
    city = loc.get("city") or {}
    city_name = (city.get("name") or "").strip()
    province = (city.get("province_slug") or "").upper().strip()
    country = (city.get("country_name") or "").strip()
    postal = (loc.get("postal_code") or "").strip()

    parts = [p for p in [address, suite, city_name, province, country, postal] if p]
    return ", ".join(parts)


# "..., <city>, <ST>, <country>, <postal>" -> capture city / state / postal from
# the tail of an address string (used for the 'location' = "City, ST ZIP" column).
_CITY_STATE_ZIP_RE = re.compile(
    r"([^,]+),\s*([A-Za-z]{2}),\s*[^,]+,\s*([A-Za-z0-9 \-]+?)\s*$"
)


def location_city_state_zip(address: str, loc: dict | None = None) -> str:
    """Regex-extract "City, ST ZIP" (e.g. "Anaheim, CA 92807") from an address
    string. Falls back to the structured city/province/postal fields if the
    regex doesn't match (e.g. an unusual address shape)."""
    m = _CITY_STATE_ZIP_RE.search(address or "")
    if m:
        city = m.group(1).strip()
        state = m.group(2).upper()
        postal = m.group(3).strip()
        return f"{city}, {state} {postal}".strip()
    if loc:
        city = (loc.get("city") or {})
        city_name = (city.get("name") or "").strip()
        province = (city.get("province_slug") or "").upper().strip()
        postal = (loc.get("postal_code") or "").strip()
        if city_name and province:
            return f"{city_name}, {province} {postal}".strip()
    return ""


def build_affiliations(doctor: dict) -> tuple[str, str]:
    """Return (names, addresses) for all displayed clinics.

    Source: doctor['doctor_locations_on_display'] -> each item['location'].
    Multiple affiliations are joined with '; ' in the SAME order in both columns,
    so name[i] lines up with address[i].
    """
    names, addrs = [], []
    for dl in doctor.get("doctor_locations_on_display") or []:
        loc = dl.get("location") if isinstance(dl, dict) else None
        if not loc:
            continue
        nm = _affiliation_name(loc)
        ad = _affiliation_address(loc)
        if nm or ad:
            names.append(nm)
            addrs.append(ad)
    return "; ".join(names), "; ".join(addrs)


# --------------------------------------------------------------------------- #
# Search page
# --------------------------------------------------------------------------- #
@dataclass
class DoctorStub:
    doctor_id: int | None
    name: str
    specialty: str
    location: str
    profile_url: str
    state: str
    gender: str = ""          # assigned from the search facet, not the blob


@dataclass
class Pagination:
    current_page: int
    total_pages: int
    count: int


def parse_search_page(html: str, gender: str = "", state: str = "") -> tuple[list[DoctorStub], Pagination]:
    """Parse a /best-doctors/ search results page.

    ``gender`` is the facet we are crawling ('M' or 'F'); the blob itself carries
    gender=None, so we stamp it from the facet, exactly as intended.
    """
    blob = extract_blob(html, "doctor_list_props")
    dp = blob["doctorPage"]
    stubs: list[DoctorStub] = []
    for r in dp.get("results", []):
        loc = r.get("location") or {}
        url = abs_url(r.get("url"))
        stubs.append(
            DoctorStub(
                doctor_id=r.get("id"),
                name=r.get("full_name") or "",
                specialty=r.get("specialty_name") or r.get("specialty") or "",
                location=location_city_state_zip(_affiliation_address(loc), loc),
                profile_url=url,
                state=state or pick_state(url, loc),   # facet state wins when crawling per-state
                gender=gender,
            )
        )
    pg = Pagination(
        current_page=dp.get("current_page", 1),
        total_pages=dp.get("total_pages", 1),
        count=dp.get("count", 0),
    )
    return stubs, pg


# --------------------------------------------------------------------------- #
# Doctor page
# --------------------------------------------------------------------------- #
@dataclass
class DoctorPage:
    doctor_row: dict
    reviews: list[dict] = field(default_factory=list)
    ratemds_pages: int = 1            # ratingsPage.total_pages
    partner_pages: int = 1            # thirdPartyRatingsPage.total_pages
    expected_total: int = 0           # doctor.rating.count


def _map_ratemds_review(doctor_id, doctor_name, r: dict) -> dict:
    return {
        "doctor_id": doctor_id,
        "doctor_name": doctor_name,
        "review_id": r.get("id"),
        "review_type": "RateMDs",
        "stars": r.get("average"),
        "title": NA,                                  # RateMDs reviews have no title
        "content": (r.get("comment") or "").strip(),
        "reviewer": (r.get("authorship_label") or "").strip(),
        "date": _fmt_date(r.get("created")),
        "helpful": (r.get("votes") or {}).get("count", 0),
        "telehealth": r.get("appointment_type_display") or NA,
        "staff": r.get("staff"),
        "punctuality": r.get("punctuality"),
        "helpfulness": r.get("helpfulness"),
        "knowledge": r.get("knowledge"),
    }


def _map_partner_review(doctor_id, doctor_name, item: dict) -> dict:
    """Map an "Our Partners" review to a row.

    RateMDs ships partner reviews in (at least) two shapes, distinguished by the
    wrapper's ``model`` field -- both seen in the example pages:

      model == "rating"  (e.g. Tesmer):  reviewer_name / title / description /
                                          rating + many extra sub-ratings.
      model == "review"  (e.g. McClelland): star_rating / comment only; no title
                                          and no reviewer_name -- the site renders
                                          the author as "Verified Patient".

    We read whichever fields are present so no content is dropped. Unknown future
    models still get best-effort content/stars via the fallback chains below.
    """
    model = item.get("model")
    d = item.get("data", item)

    # stars: "rating" model uses `rating`; "review" model uses `star_rating`.
    stars = d.get("rating")
    if stars is None:
        stars = d.get("star_rating")

    # content: `description` (rating model) or `comment` (review model).
    content = d.get("description")
    if content is None or content == "":
        content = d.get("comment") or ""

    title = d.get("title")

    # reviewer: explicit name wins; "review" model with no name renders as
    # "Verified Patient" on the site (per the brief).
    reviewer = d.get("reviewer_name")
    if not reviewer:
        reviewer = "Verified Patient" if model == "review" else ""

    return {
        "doctor_id": doctor_id,
        "doctor_name": doctor_name,
        "review_id": d.get("id"),
        "review_type": "Our Partners",
        "stars": stars,
        "title": title if title else NA,             # italic title; null -> NA
        "content": (content or "").strip(),
        "reviewer": reviewer.strip(),
        "date": _fmt_date(d.get("date_added")),
        "helpful": NA,                                # partners expose no vote count
        "telehealth": NA,                             # not exposed for partner reviews
        "staff": NA,                                  # sub-ratings only for RateMDs
        "punctuality": NA,
        "helpfulness": NA,
        "knowledge": NA,
    }


def parse_doctor_page(html: str, gender: str = "", profile_url: str = "", state: str = "") -> DoctorPage:
    """Parse a doctor profile/ratings page into a doctor row + its reviews.

    Reads from window.DATA.doctorDetailProps:
      doctor                 -> identity, gender, rating aggregate, affiliations
      ratingsPage            -> "RateMDs" reviews   (results / count / total_pages)
      thirdPartyRatingsPage  -> "Our Partners" reviews
    """
    blob = extract_blob(html, "doctorDetailProps")
    doctor = blob["doctor"]
    ratings = blob.get("ratingsPage") or {}
    partners = blob.get("thirdPartyRatingsPage") or {}

    doctor_id = doctor.get("id")
    name = doctor.get("full_name") or ""
    url = abs_url(doctor.get("url")) or profile_url

    rating = doctor.get("rating") or {}
    total = rating.get("count", 0) or 0
    avg = rating.get("average")
    overall = round(avg, 2) if (avg and total) else NA

    # Doctor-level aggregate sub-ratings (averages over all reviews), straight
    # from the blob. NA when the doctor has no reviews.
    def _sub_avg(key: str):
        v = rating.get(key)
        # Sub-ratings are on a 1-5 scale; a 0 means "no sub-rating average
        # available" (e.g. all reviews are partner reviews), not a real score.
        return round(v, 2) if (total and isinstance(v, (int, float)) and v > 0) else NA

    doc_staff = _sub_avg("staff")
    doc_punctuality = _sub_avg("punctuality")
    doc_helpfulness = _sub_avg("helpfulness")
    doc_knowledge = _sub_avg("knowledge")

    ratemds_count = ratings.get("count", 0) or 0
    partner_count = partners.get("count", 0) or 0

    # 'location' column: "City, ST ZIP" regex-extracted from the primary clinic's
    # address (falls back to structured fields if the regex misses).
    primary_loc = doctor.get("default_location") or doctor.get("location")
    primary_loc = primary_loc if isinstance(primary_loc, dict) else None
    location_str = location_city_state_zip(_affiliation_address(primary_loc), primary_loc)

    # State: prefer the structured province_slug; fall back to the URL regex.
    # If the default/primary clinic has no city, try any displayed affiliation.
    state_loc = primary_loc
    if not state_from_location(state_loc):
        for dl in doctor.get("doctor_locations_on_display") or []:
            cand = dl.get("location") if isinstance(dl, dict) else None
            if state_from_location(cand):
                state_loc = cand
                break

    # Coordinates: prefer the primary clinic, then the state-bearing affiliation,
    # then any displayed location that has lat/long. NA when none do.
    displayed = [
        dl.get("location")
        for dl in (doctor.get("doctor_locations_on_display") or [])
        if isinstance(dl, dict)
    ]
    coord_loc = first_with_coords(primary_loc, state_loc, *displayed)
    latitude, longitude, location_id = loc_coords(coord_loc)

    aff_name, aff_address = build_affiliations(doctor)
    doctor_row = {
        "doctor_id": doctor_id,
        "name": name,
        "specialty": doctor.get("specialty_name") or doctor.get("specialty") or "",
        "location": location_str,
        "profile_url": url,
        "gender": gender or (doctor.get("gender") or ""),
        "doctor_state": state or pick_state(url, state_loc),   # facet state wins
        "latitude": latitude,
        "longitude": longitude,
        "location_id": location_id,

        "hospital_affiliation_name": aff_name,
        "hospital_affiliation_address": aff_address,
        "overall_rating": overall,
        "doctor_staff_rating": doc_staff,
        "doctor_punctuality_rating": doc_punctuality,
        "doctor_helpfulness_rating": doc_helpfulness,
        "doctor_knowledge_rating": doc_knowledge,
        "total_reviews": total,
        "ratemds_count": ratemds_count,
        "our_partners_count": partner_count,
    }

    reviews: list[dict] = []
    for r in ratings.get("results", []) or []:
        reviews.append(_map_ratemds_review(doctor_id, name, r))
    for item in partners.get("results", []) or []:
        reviews.append(_map_partner_review(doctor_id, name, item))

    return DoctorPage(
        doctor_row=doctor_row,
        reviews=reviews,
        ratemds_pages=ratings.get("total_pages", 1) or 1,
        partner_pages=partners.get("total_pages", 1) or 1,
        expected_total=total,
    )


def parse_review_page(html: str, doctor_id, doctor_name: str, kind: str) -> list[dict]:
    """Parse reviews from a paginated review page (page 2..N).

    ``kind`` is 'ratemds' (uses ?page=n -> ratingsPage) or
    'partners' (uses ?third_party_page=n -> thirdPartyRatingsPage).
    Returns only that page's review rows.
    """
    blob = extract_blob(html, "doctorDetailProps")
    field = "ratingsPage" if kind == "ratemds" else "thirdPartyRatingsPage"
    return parse_review_data(blob.get(field) or {}, doctor_id, doctor_name, kind)


def parse_review_data(page: dict, doctor_id, doctor_name: str, kind: str) -> list[dict]:
    """Map a review page object, including the public partner JSON response."""
    results = page.get("results", []) or []
    if kind == "ratemds":
        return [_map_ratemds_review(doctor_id, doctor_name, r) for r in results]
    elif kind == "partners":
        return [_map_partner_review(doctor_id, doctor_name, r) for r in results]
    raise ValueError(f"unknown kind: {kind}")


# convenience for tests / debugging
def stub_to_dict(s: DoctorStub) -> dict:
    return asdict(s)
