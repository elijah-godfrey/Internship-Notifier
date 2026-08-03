"""Tests for bounded, checkpointed prestige-cache seeding."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from internship_notifier.prestige import CompanyPrestige, PrestigeCache
from internship_notifier.seed_prestige_cache import (
    load_candidate_names,
    select_uncached_candidates,
)


def _assessment(
    display_name: str,
    *,
    aliases: tuple[str, ...] = (),
) -> CompanyPrestige:
    """Create a valid cache entry for selection tests."""
    return CompanyPrestige(
        display_name=display_name,
        prestige_score=80,
        confidence="high",
        reason="Strong engineering reputation.",
        reviewed_at=date(2026, 8, 2),
        model="test-model",
        aliases=aliases,
    )


def test_load_candidate_names_preserves_source_priority_and_deduplicates(
    tmp_path: Path,
) -> None:
    """Later duplicate names do not displace earlier source-list entries."""
    first = tmp_path / "first.txt"
    first.write_text("# priority list\nCursor\nD. E. Shaw\n", encoding="utf-8")
    second = tmp_path / "second.txt"
    second.write_text("cursor\nD E Shaw Inc.\nDatadog\n", encoding="utf-8")

    assert load_candidate_names([first, second]) == ["Cursor", "D. E. Shaw", "Datadog"]


def test_select_uncached_candidates_skips_cache_entries_and_aliases() -> None:
    """Cached canonical names and aliases do not consume the batch limit."""
    cache = PrestigeCache()
    cache.put(_assessment("Anysphere", aliases=("Cursor",)))

    selected = select_uncached_candidates(
        ["Cursor", "Datadog", "Figma"],
        cache,
        limit=1,
    )

    assert selected == ["Datadog"]


def test_select_uncached_candidates_rejects_non_positive_limit() -> None:
    """A non-positive bound cannot represent a safe seed run."""
    with pytest.raises(ValueError, match="at least 1"):
        select_uncached_candidates(["Datadog"], PrestigeCache(), limit=0)
