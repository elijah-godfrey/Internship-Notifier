"""Filter raw listings the same way upstream READMEs do (summer vs off-season).

Constants and logic mirror ``list_updater/listings.py`` and ``commands.py`` in
https://github.com/SimplifyJobs/Summer2026-Internships (``dev`` branch).
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

# Upstream: list_updater/commands.py ``cmd_readme_update`` for Summer 2026 README
DEFAULT_SUMMER_YEAR = "2026"
DEFAULT_SUMMER_EARLIEST_DATE_POSTED = 1748761200

# Substrings matched in ``terms`` entries (mirrors upstream off-season README).
_OFF_SEASON_SEASON_MARKERS: tuple[str, ...] = ("Fall", "Winter", "Spring")


def filter_summer(
    listings: list[Listing],
    year: str = DEFAULT_SUMMER_YEAR,
    earliest_date: int = DEFAULT_SUMMER_EARLIEST_DATE_POSTED,
    blocked_companies: frozenset[str] | None = None,
) -> list[Listing]:
    """Keep visible summer listings for ``year`` after ``earliest_date``, minus blocked URLs.

    Matches upstream ``filter_summer`` (same ``terms``, ``is_visible``, and
    company URL checks).

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
        if not listing.get("is_visible"):
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
    """Keep visible off-season listings (Fall/Winter/Spring) with no Summer term.

    Matches upstream ``filter_off_season``.

    Args:
        listings: Full listing dicts from ``listings.json``.

    Returns:
        A new list of listings that appear on the off-season README.
    """
    result: list[Listing] = []
    for listing in listings:
        if not listing.get("is_visible"):
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

    Listing JSON uses full names (e.g. ``\"Software Engineering\"``). Unknown
    names in ``categories`` are ignored for matching but do not widen the set.

    Args:
        listings: Already source-filtered listings (summer or off-season).
        categories: Non-empty set of canonical category names to allow.

    Returns:
        Listings whose ``category`` value is in ``categories``. Listings
        missing ``category`` are dropped.

    Raises:
        ValueError: If ``categories`` is empty.
    """
    if not categories:
        raise ValueError("categories must be non-empty")
    allowed = frozenset(categories)
    return [L for L in listings if L.get("category") in allowed]
