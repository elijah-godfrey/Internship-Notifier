"""Filter raw listings the same way upstream READMEs do (summer vs off-season).

Constants and logic mirror ``list_updater/listings.py`` and ``commands.py`` in
https://github.com/SimplifyJobs/Summer2027-Internships (``dev`` branch).
"""

from __future__ import annotations

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
