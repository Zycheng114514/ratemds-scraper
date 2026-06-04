# Data elements — field map

Exactly what gets extracted and where it comes from. The **primary source** for
every field is the embedded JSON blob; DOM selectors exist only as a documented
fallback and are not used on the happy path.

- Search blob: `window.DATA.doctor_list_props`
- Doctor blob: `window.DATA.doctorDetailProps`

Implemented in `src/ratemds_scraper/parse.py`.

## `doctors.csv` — one row per doctor

| Column | JSON source (doctor blob unless noted) | Notes |
| --- | --- | --- |
| `doctor_id` | `doctor.id` | — |
| `name` | `doctor.full_name` | — |
| `specialty` | `doctor.specialty_name` | human-readable, e.g. "Dermatologist" |
| `location` | regex `"City, ST ZIP"` from the primary clinic address | falls back to structured `city`/`province_slug`/`postal_code` |
| `profile_url` | `doctor.url` | site-prefixed |
| `gender` | the crawled `?gender=` facet, else `doctor.gender` | — |
| `doctor_state` | crawled state facet → `province_slug` → URL regex | hybrid; the URL is not a stable state carrier |
| `latitude` | `location.latitude` (primary clinic → first affiliation with coords) | `NA` when the doctor has no located clinic |
| `longitude` | `location.longitude` | `NA` when none |
| `location_id` | `location.id` | stable clinic id; groups doctors at the same address |
| `hospital_affiliation_name` | `doctor.doctor_locations_on_display[].location.name` | clinic name(s), `"; "`-joined |
| `hospital_affiliation_address` | same objects, address tail | address(es), `"; "`-joined, same order as names |
| `overall_rating` | `doctor.rating.average` (2 dp) | `N/A` when 0 reviews |
| `doctor_staff_rating` | `doctor.rating.staff` | aggregate sub-rating; a `0` means "no average available" → `NA` |
| `doctor_punctuality_rating` | `doctor.rating.punctuality` | " |
| `doctor_helpfulness_rating` | `doctor.rating.helpfulness` | " |
| `doctor_knowledge_rating` | `doctor.rating.knowledge` | " |
| `total_reviews` | `doctor.rating.count` | == ratemds_count + our_partners_count |
| `ratemds_count` | `ratingsPage.count` | number of **RateMDs** reviews |
| `our_partners_count` | `thirdPartyRatingsPage.count` | number of **Our Partners** reviews |

Affiliation name/address are parallel `"; "`-joined columns kept in the same
order, so `name[i]` lines up with `address[i]`.

## `reviews.csv` — one row per review

Two sources, distinguished by `review_type`.

**RateMDs reviews** — `ratingsPage.results[]` (`review_type = "RateMDs"`):

| Column | JSON field | Notes |
| --- | --- | --- |
| `review_id` | `id` | — |
| `stars` | `average` | overall stars for the review |
| `title` | — | always `NA` (RateMDs reviews have no title) |
| `content` | `comment` | the review body |
| `reviewer` | `authorship_label` | e.g. "Verified User from San Jose, CA" |
| `date` | `created` → "Month D, YYYY" | — |
| `helpful` | `votes.count` | thumbs-up count |
| `telehealth` | `appointment_type_display` | e.g. "In-person" (RateMDs only) |
| `staff` / `punctuality` / `helpfulness` / `knowledge` | same-named fields | per-review sub-ratings (RateMDs only) |

**Our Partners reviews** — `thirdPartyRatingsPage.results[].data`
(`review_type = "Our Partners"`):

| Column | JSON field | Notes |
| --- | --- | --- |
| `review_id` | `id` | — |
| `stars` | `rating` | integer 1–5 |
| `title` | `title` or `NA` | the italic title; `null` → `NA` |
| `content` | `description` | the body |
| `reviewer` | `reviewer_name` | "Anonymous", a name, or "Verified Patient" |
| `date` | `date_added` → "Month D, YYYY" | — |
| `helpful` / `telehealth` / sub-ratings | — | `NA` (not exposed for partner reviews) |

Each review row also carries `doctor_id` and `doctor_name` for joining.

## The review layout cases

The site renders reviews several ways, but they all reduce to the two arrays
above:

1. **Separate "RateMDs (n)" + "Our Partners (m)" tabs** → read both pages.
2. **Only RateMDs reviews** → `thirdPartyRatingsPage.count == 0`; full
   sub-ratings present.
3. **Partner reviews with an italic title + body** → `title` and `description`
   are separate fields; both captured.
4. **No reviews** → both counts 0; the doctor row is still written with
   `overall_rating = N/A`, `total_reviews = 0`, and no review rows.

"Our Partners" results come in two shapes by `model`: `model:"rating"`
(reviewer_name/title/description/rating) and `model:"review"`
(comment/star_rating only). The parser reads both, so neither is silently
dropped. Any review-count mismatch against the stated total is logged to
`state/anomalies.log`.
