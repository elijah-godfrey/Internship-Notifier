"""Unit tests for resumable prestige reranking."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from internship_notifier.prestige import (
    PRESTIGE_RUBRIC_VERSION,
    CompanyPrestige,
    PrestigeCache,
)
from internship_notifier.prestige_ranker import PrestigeRankingError
from internship_notifier.rerank_prestige import (
    rerank_candidates,
    select_rerank_candidates,
)


def _assessment(
    display_name: str,
    *,
    confidence: str = "low",
    rubric_version: str = "prestige-v1",
    manual_override: bool = False,
    aliases: tuple[str, ...] = (),
) -> CompanyPrestige:
    return CompanyPrestige(
        display_name=display_name,
        prestige_score=40,
        confidence=confidence,  # type: ignore[arg-type]
        reason="Older assessment.",
        reviewed_at=date(2026, 8, 1),
        model="old-model",
        rubric_version=rubric_version,
        aliases=aliases,
        manual_override=manual_override,
    )


def _cache() -> PrestigeCache:
    cache = PrestigeCache()
    cache.put(_assessment("Zebra"))
    cache.put(_assessment("Alpha"))
    cache.put(_assessment("Mid", confidence="medium"))
    cache.put(_assessment("Famous", confidence="high"))
    cache.put(_assessment("Manual", manual_override=True))
    cache.put(_assessment("Current", rubric_version=PRESTIGE_RUBRIC_VERSION))
    return cache


class TestSelectRerankCandidates:
    def test_low_comes_before_medium_and_skips_protected_scores(self) -> None:
        cache = _cache()

        low = select_rerank_candidates(cache, confidence="low", limit=20)
        medium = select_rerank_candidates(cache, confidence="medium", limit=20)

        assert [item.display_name for item in low] == ["Alpha", "Zebra"]
        assert [item.display_name for item in medium] == ["Mid"]
        assert [item.display_name for item in [*low, *medium]] == [
            "Alpha",
            "Zebra",
            "Mid",
        ]

    def test_limit_keeps_the_first_names(self) -> None:
        selected = select_rerank_candidates(_cache(), confidence="low", limit=1)
        assert [item.display_name for item in selected] == ["Alpha"]

    def test_rejects_high_confidence_and_non_positive_limit(self) -> None:
        cache = _cache()
        with pytest.raises(ValueError, match="confidence"):
            select_rerank_candidates(cache, confidence="high", limit=1)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="limit"):
            select_rerank_candidates(cache, confidence="low", limit=0)


class TestRerankCandidates:
    def test_saves_after_each_success_and_keeps_old_names(self) -> None:
        cache = PrestigeCache()
        existing = _assessment("Zip", aliases=("ZipHQ",))
        cache.put(existing)
        replacement = CompanyPrestige(
            display_name="ZipHQ",
            prestige_score=78,
            confidence="medium",
            reason="Identified the procurement company from its site.",
            reviewed_at=date(2026, 10, 16),
            model="new-model",
            aliases=(),
        )
        ranker = MagicMock()
        ranker.rank_company.return_value = replacement
        checkpoints: list[int] = []

        refreshed, failures = rerank_candidates(
            cache,
            [existing],
            ranker,
            reviewed_at=date(2026, 10, 16),
            on_cache_change=lambda changed: checkpoints.append(len(changed.companies)),
        )

        assert refreshed == 1
        assert failures == ()
        stored = cache.get("Zip")
        assert stored is not None
        assert stored.display_name == "ZipHQ"
        assert stored.prestige_score == 78
        assert stored.rubric_version == PRESTIGE_RUBRIC_VERSION
        assert cache.get("ZipHQ") == stored
        assert checkpoints == [1]
        ranker.rank_company.assert_called_once_with(
            "Zip",
            reviewed_at=date(2026, 10, 16),
        )

    def test_failure_does_not_checkpoint_and_later_company_still_saves(self) -> None:
        cache = PrestigeCache()
        first = _assessment("Alpha")
        second = _assessment("Beta")
        cache.put(first)
        cache.put(second)
        ranker = MagicMock()
        ranker.rank_company.side_effect = [
            PrestigeRankingError("search failed"),
            CompanyPrestige(
                display_name="Beta",
                prestige_score=70,
                confidence="medium",
                reason="Identified from public coverage.",
                reviewed_at=date(2026, 10, 16),
                model="new-model",
            ),
        ]
        checkpoints: list[str] = []

        refreshed, failures = rerank_candidates(
            cache,
            [first, second],
            ranker,
            reviewed_at=date(2026, 10, 16),
            on_cache_change=lambda changed: checkpoints.append(
                changed.get("Beta").display_name  # type: ignore[union-attr]
            ),
        )

        assert refreshed == 1
        assert failures == ("Alpha: search failed",)
        assert cache.get("Alpha") == first
        assert cache.get("Beta") is not None
        assert cache.get("Beta").prestige_score == 70  # type: ignore[union-attr]
        assert checkpoints == ["Beta"]
