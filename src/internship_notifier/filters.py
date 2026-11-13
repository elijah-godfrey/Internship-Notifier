"""Filter raw listings the same way upstream READMEs do (summer vs off-season).

Constants and logic mirror ``list_updater/listings.py`` and ``commands.py`` in
https://github.com/SimplifyJobs/Summer2027-Internships (``dev`` branch).
Country matching for SimplifyJobs reads the location text. Quant-scraper rows
already carry country codes from quant-scraper.com.
"""

from __future__ import annotations

import re
from typing import Any

Listing = dict[str, Any]

# Upstream: list_updater/constants.py — companies blocked from the summer README
BLOCKED_COMPANIES: frozenset[str] = frozenset(
    {
        "https://simplify.jobs/c/Jerry",
    }
)

# Upstream: list_updater/commands.py ``cmd_readme_update`` for the Summer 2027 README.
# earliest_date is 2026-05-01 00:00 Pacific.
DEFAULT_SUMMER_YEAR = "2027"
DEFAULT_SUMMER_EARLIEST_DATE_POSTED = 1777618800

# Substrings matched in ``terms`` entries (mirrors upstream off-season README).
_OFF_SEASON_SEASON_MARKERS: tuple[str, ...] = ("Fall", "Winter", "Spring")

# listings.json mostly stores the short tag. A few rows, and the README, use the long name.
_CATEGORY_ALIASES: dict[str, str] = {
    "hardware": "Hardware Engineering",
    "hardware engineering": "Hardware Engineering",
    "product": "Product Management",
    "product management": "Product Management",
    "software": "Software Engineering",
    "software engineering": "Software Engineering",
    "ai/ml/data": "Data Science, AI & Machine Learning",
    "data science, ai & machine learning": "Data Science, AI & Machine Learning",
    "quant": "Quantitative Finance",
    "quantitative finance": "Quantitative Finance",
}


def _canonical_category(name: object) -> str | None:
    """Map a short or README category name to one canonical name.

    Unknown names keep their stripped spelling and match only that spelling.
    """
    if not isinstance(name, str):
        return None
    stripped = name.strip()
    if not stripped:
        return None
    return _CATEGORY_ALIASES.get(stripped.casefold(), stripped)


def _canonical_category_set(categories: set[str] | frozenset[str]) -> frozenset[str]:
    """Return the canonical names for a configured category list."""
    return frozenset(
        category
        for category in (_canonical_category(name) for name in categories)
        if category is not None
    )


def filter_summer(
    listings: list[Listing],
    year: str = DEFAULT_SUMMER_YEAR,
    earliest_date: int = DEFAULT_SUMMER_EARLIEST_DATE_POSTED,
    blocked_companies: frozenset[str] | None = None,
) -> list[Listing]:
    """Keep active, visible summer listings after ``earliest_date``.

    Mirrors the active section of the upstream summer README, including its
    term, visibility, date, and blocked-company checks.

    Args:
        listings: Full listing dicts from ``listings.json``.
        year: Summer year string, e.g. ``\"2026\"`` (used in ``\"Summer {year}\"``).
        earliest_date: Unix timestamp; listing ``date_posted`` must be strictly greater.
        blocked_companies: Optional override of blocked ``company_url`` substrings;
            defaults to :data:`BLOCKED_COMPANIES`.

    Returns:
        A new list of listings that pass the summer README rules.
    """
    blocked = blocked_companies if blocked_companies is not None else BLOCKED_COMPANIES
    blocked_urls_lower = {url.lower() for url in blocked}
    out: list[Listing] = []
    for listing in listings:
        if not listing.get("is_visible") or not listing.get("active"):
            continue
        terms = listing.get("terms") or []
        if not any(f"Summer {year}" in item for item in terms):
            continue
        if listing.get("date_posted", 0) <= earliest_date:
            continue
        company_url = (listing.get("company_url") or "").lower()
        if any(blocked_url in company_url for blocked_url in blocked_urls_lower):
            continue
        out.append(listing)
    return out


def filter_off_season(listings: list[Listing]) -> list[Listing]:
    """Keep active, visible off-season listings with no Summer term.

    The upstream off-season README also contains a collapsed inactive section;
    notifications intentionally keep only its actionable active rows.

    Args:
        listings: Full listing dicts from ``listings.json``.

    Returns:
        A new list of listings that appear on the off-season README.
    """
    result: list[Listing] = []
    for listing in listings:
        if not listing.get("is_visible") or not listing.get("active"):
            continue
        terms = listing.get("terms") or []
        has_off = any(
            season in term for term in terms for season in _OFF_SEASON_SEASON_MARKERS
        )
        has_summer = any("Summer" in term for term in terms)
        if has_off and not has_summer:
            result.append(listing)
    return result


def filter_by_categories(
    listings: list[Listing],
    categories: set[str] | frozenset[str],
) -> list[Listing]:
    """Keep listings whose ``category`` is in ``categories``.

    Short tags and README names for the same category match each other.
    An unknown name matches only that same spelling.

    Args:
        listings: Already source-filtered listings (summer or off-season).
        categories: Non-empty set of category names to allow. Either the short
            tag or the README name is accepted.

    Returns:
        Listings whose category is allowed. Listings missing ``category``
        are dropped.

    Raises:
        ValueError: If ``categories`` is empty.
    """
    if not categories:
        raise ValueError("categories must be non-empty")
    allowed = _canonical_category_set(categories)
    return [
        listing
        for listing in listings
        if _canonical_category(listing.get("category")) in allowed
    ]


def filter_excluding_categories(
    listings: list[Listing],
    categories: set[str] | frozenset[str],
) -> list[Listing]:
    """Drop listings whose ``category`` is in ``categories``.

    Short tags and README names for the same category match each other.
    An unknown name matches only that same spelling.

    Args:
        listings: Already source-filtered listings.
        categories: Category names to remove. Either the short tag or the
            README name is accepted. An empty set keeps every listing.

    Returns:
        Listings whose category is not excluded. Listings missing
        ``category`` are kept.
    """
    if not categories:
        return list(listings)
    excluded = _canonical_category_set(categories)
    return [
        listing
        for listing in listings
        if _canonical_category(listing.get("category")) not in excluded
    ]


# Two-letter codes as written after a city: "Santa Clara, CA", "Toronto, ON".
_US_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
_CA_CODES = frozenset("AB BC MB NB NL NS NT NU ON PE QC SK YT".split())
_PLACE_NAMES: dict[str, str] = {
    "alabama": "US",
    "alaska": "US",
    "arizona": "US",
    "arkansas": "US",
    "california": "US",
    "colorado": "US",
    "connecticut": "US",
    "delaware": "US",
    "florida": "US",
    "georgia": "US",
    "hawaii": "US",
    "idaho": "US",
    "illinois": "US",
    "indiana": "US",
    "iowa": "US",
    "kansas": "US",
    "kentucky": "US",
    "louisiana": "US",
    "maine": "US",
    "maryland": "US",
    "massachusetts": "US",
    "michigan": "US",
    "minnesota": "US",
    "mississippi": "US",
    "missouri": "US",
    "montana": "US",
    "nebraska": "US",
    "nevada": "US",
    "new hampshire": "US",
    "new jersey": "US",
    "new mexico": "US",
    "new york": "US",
    "new york city": "US",
    "north carolina": "US",
    "north dakota": "US",
    "ohio": "US",
    "oklahoma": "US",
    "oregon": "US",
    "pennsylvania": "US",
    "rhode island": "US",
    "south carolina": "US",
    "south dakota": "US",
    "tennessee": "US",
    "texas": "US",
    "utah": "US",
    "vermont": "US",
    "virginia": "US",
    "washington": "US",
    "washington dc": "US",
    "west virginia": "US",
    "wisconsin": "US",
    "wyoming": "US",
    "district of columbia": "US",
    "nyc": "US",
    "sf": "US",
    "la": "US",
    "south sf": "US",
    "dc": "US",
    "us": "US",
    "usa": "US",
    "alberta": "CA",
    "british columbia": "CA",
    "manitoba": "CA",
    "new brunswick": "CA",
    "newfoundland and labrador": "CA",
    "newfoundland": "CA",
    "nova scotia": "CA",
    "northwest territories": "CA",
    "nunavut": "CA",
    "ontario": "CA",
    "prince edward island": "CA",
    "quebec": "CA",
    "québec": "CA",
    "saskatchewan": "CA",
    "yukon": "CA",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
}
_US_PHRASE = re.compile(
    r"\b(?:usa|u\.s\.a\.?|u\.s\.|united states(?: of america)?)\b|\bremote in us\b",
    re.IGNORECASE,
)
_CANADA_PHRASE = re.compile(r"\bcanada\b", re.IGNORECASE)
_UK_PHRASE = re.compile(r"\b(?:uk|u\.k\.|united kingdom|great britain)\b", re.IGNORECASE)
_PLUS_SUFFIX = re.compile(r"\s*\+\s*\d+\s*$")


def infer_country_codes(locations: object) -> set[str]:
    """Return ``US``, ``CA``, and ``GB`` codes found in location strings.

    SimplifyJobs writes ``City, ST`` or a country name, not a separate country
    field. A string that does not say one of these three countries is ignored.
    """
    if not isinstance(locations, list):
        return set()
    codes: set[str] = set()
    for location in locations:
        if not isinstance(location, str):
            continue
        code = _country_of_location(location)
        if code is not None:
            codes.add(code)
    return codes


def filter_by_countries(
    listings: list[Listing],
    countries: set[str] | frozenset[str],
) -> list[Listing]:
    """Keep listings that have a location in ``countries``.

    Codes are ``US``, ``CA``, and ``GB``. A row that already has a
    ``countries`` list uses that list, including when the list is empty.
    Other rows are classified from their location text. An empty
    ``countries`` argument keeps every listing.

    Args:
        listings: Source-filtered listings.
        countries: Allowed country codes.

    Returns:
        Listings with at least one allowed country.
    """
    if not countries:
        return list(listings)
    allowed = set(countries)
    kept: list[Listing] = []
    for listing in listings:
        if "countries" in listing:
            raw = listing.get("countries") or []
            codes = {code for code in raw if isinstance(code, str)}
        else:
            codes = infer_country_codes(listing.get("locations"))
        if codes & allowed:
            kept.append(listing)
    return kept


def _country_of_location(location: str) -> str | None:
    text = _PLUS_SUFFIX.sub("", location).strip()
    if not text or text == "—":
        return None
    if _UK_PHRASE.search(text):
        return "GB"
    if _CANADA_PHRASE.search(text):
        return "CA"
    if _US_PHRASE.search(text):
        return "US"
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        return None
    for part in (parts[-1], text):
        code = _country_of_token(part)
        if code is not None:
            return code
    return None


def _country_of_token(token: str) -> str | None:
    compact = " ".join(token.casefold().replace(".", "").split())
    named = _PLACE_NAMES.get(compact)
    if named is not None:
        return named
    if len(compact) == 2 and compact.isalpha():
        code = compact.upper()
        if code in _US_CODES:
            return "US"
        if code in _CA_CODES:
            return "CA"
    return None
