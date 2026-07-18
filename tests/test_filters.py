"""Unit tests for internship_notifier.filters."""

from __future__ import annotations

import pytest

from internship_notifier.filters import (
    DEFAULT_SUMMER_EARLIEST_DATE_POSTED,
    filter_by_categories,
    filter_off_season,
    filter_summer,
)


def _summer_listing(
    *,
    visible: bool = True,
    active: bool = True,
    terms: list[str] | None = None,
    date_posted: int = DEFAULT_SUMMER_EARLIEST_DATE_POSTED + 1,
    company_url: str = "https://example.com",
    listing_id: str = "1",
) -> dict:
    return {
        "id": listing_id,
        "is_visible": visible,
        "active": active,
        "terms": terms or ["Summer 2026"],
        "date_posted": date_posted,
        "company_url": company_url,
        "category": "Software Engineering",
    }


class TestFilterSummer:
    def test_keeps_visible_summer_after_cutoff_not_blocked(self) -> None:
        row = _summer_listing()
        assert filter_summer([row]) == [row]

    def test_drops_not_visible(self) -> None:
        row = _summer_listing(visible=False)
        assert filter_summer([row]) == []

    def test_drops_inactive(self) -> None:
        row = _summer_listing(active=False)
        assert filter_summer([row]) == []

    def test_drops_without_summer_year_term(self) -> None:
        row = _summer_listing(terms=["Fall 2025"])
        assert filter_summer([row]) == []

    def test_drops_on_or_before_earliest_date(self) -> None:
        row = _summer_listing(date_posted=DEFAULT_SUMMER_EARLIEST_DATE_POSTED)
        assert filter_summer([row]) == []

    def test_drops_blocked_company_url(self) -> None:
        row = _summer_listing(company_url="https://simplify.jobs/c/Jerry/about")
        assert filter_summer([row]) == []

    def test_custom_blocked_override_can_allow_default_blocked(self) -> None:
        row = _summer_listing(company_url="https://simplify.jobs/c/Jerry/about")
        out = filter_summer([row], blocked_companies=frozenset())
        assert out == [row]


class TestFilterOffSeason:
    def test_keeps_visible_off_season_without_summer(self) -> None:
        row = {
            "id": "a",
            "is_visible": True,
            "active": True,
            "terms": ["Fall 2025"],
            "category": "Software Engineering",
        }
        assert filter_off_season([row]) == [row]

    def test_drops_not_visible(self) -> None:
        row = {
            "id": "a",
            "is_visible": False,
            "active": True,
            "terms": ["Fall 2025"],
        }
        assert filter_off_season([row]) == []

    def test_drops_inactive(self) -> None:
        row = {
            "id": "a",
            "is_visible": True,
            "active": False,
            "terms": ["Fall 2025"],
        }
        assert filter_off_season([row]) == []

    def test_drops_when_no_fall_winter_spring_in_terms(self) -> None:
        row = {
            "id": "a",
            "is_visible": True,
            "active": True,
            "terms": ["Co-op"],
        }
        assert filter_off_season([row]) == []

    def test_drops_when_any_term_contains_summer(self) -> None:
        row = {
            "id": "a",
            "is_visible": True,
            "active": True,
            "terms": ["Fall 2025", "Summer 2026"],
        }
        assert filter_off_season([row]) == []


class TestFilterByCategories:
    def test_empty_categories_raises(self) -> None:
        with pytest.raises(ValueError, match="categories must be non-empty"):
            filter_by_categories([], set())

    def test_keeps_only_allowed_categories(self) -> None:
        rows = [
            {"id": "1", "category": "Software Engineering"},
            {"id": "2", "category": "Data Science"},
        ]
        out = filter_by_categories(rows, {"Software Engineering"})
        assert out == [rows[0]]

    def test_drops_missing_category(self) -> None:
        rows = [{"id": "1"}]
        assert filter_by_categories(rows, {"Software Engineering"}) == []
