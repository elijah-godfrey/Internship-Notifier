"""Unit tests for cached, LLM-backed company prestige ranking."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from internship_notifier.config_toml import PrestigeTomlConfig
from internship_notifier.prestige import CompanyPrestige, PrestigeCache
from internship_notifier.prestige_ranker import (
    DEFAULT_PRESTIGE_MODEL,
    OpenAIPrestigeRanker,
    PrestigeAssessmentOutput,
    PrestigeRankingError,
    get_or_rank_company,
    resolve_or_rank_minimum_score,
)


def _assessment(
    *,
    display_name: str = "Microsoft",
    score: int = 88,
    aliases: tuple[str, ...] = ("MSFT",),
) -> CompanyPrestige:
    return CompanyPrestige(
        display_name=display_name,
        prestige_score=score,
        confidence="high",
        reason="Strong software engineering reputation and career signal.",
        reviewed_at=date(2026, 7, 17),
        model="test-model",
        aliases=aliases,
    )


class StubRanker:
    def __init__(self, assessment: CompanyPrestige) -> None:
        self.assessment = assessment
        self.calls: list[str] = []

    def rank_company(
        self,
        company_name: str,
        *,
        reviewed_at: date | None = None,
    ) -> CompanyPrestige:
        self.calls.append(company_name)
        return self.assessment


class TestPrestigeAssessmentOutput:
    def test_rejects_score_outside_1_to_100(self) -> None:
        with pytest.raises(ValidationError):
            PrestigeAssessmentOutput(
                display_name="Example",
                prestige_score=101,
                confidence="high",
                reason="Example",
                aliases=[],
            )

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            PrestigeAssessmentOutput.model_validate(
                {
                    "display_name": "Example",
                    "prestige_score": 70,
                    "confidence": "medium",
                    "reason": "Example",
                    "aliases": [],
                    "unrequested": "value",
                }
            )

    def test_rejects_empty_aliases(self) -> None:
        with pytest.raises(ValidationError, match="aliases"):
            PrestigeAssessmentOutput(
                display_name="Example",
                prestige_score=70,
                confidence="medium",
                reason="Example",
                aliases=[""],
            )


class TestOpenAIPrestigeRanker:
    def test_structured_response_becomes_cache_assessment(self) -> None:
        client = MagicMock()
        client.responses.parse.return_value = SimpleNamespace(
            output_parsed=PrestigeAssessmentOutput(
                display_name="Meta",
                prestige_score=94,
                confidence="high",
                reason="Exceptional global software engineering career signal.",
                aliases=["Meta Platforms"],
            )
        )
        ranker = OpenAIPrestigeRanker(client=client)

        result = ranker.rank_company(
            "Facebook",
            reviewed_at=date(2026, 7, 17),
        )

        assert result.display_name == "Meta"
        assert result.prestige_score == 94
        assert result.reviewed_at == date(2026, 7, 17)
        assert result.model == DEFAULT_PRESTIGE_MODEL
        assert "Facebook" in result.aliases
        kwargs = client.responses.parse.call_args.kwargs
        assert kwargs["text_format"] is PrestigeAssessmentOutput
        assert "Facebook" in kwargs["input"]

    def test_model_can_be_overridden(self) -> None:
        client = MagicMock()
        ranker = OpenAIPrestigeRanker(client=client, model="custom-model")
        assert ranker.model == "custom-model"

    def test_missing_api_key_raises_clear_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(PrestigeRankingError, match="OPENAI_API_KEY"):
            OpenAIPrestigeRanker()

    def test_refusal_or_missing_parse_raises(self) -> None:
        client = MagicMock()
        client.responses.parse.return_value = SimpleNamespace(output_parsed=None)
        ranker = OpenAIPrestigeRanker(client=client)

        with pytest.raises(PrestigeRankingError, match="refused"):
            ranker.rank_company("Example")

    def test_client_error_is_wrapped(self) -> None:
        client = MagicMock()
        client.responses.parse.side_effect = RuntimeError("network unavailable")
        ranker = OpenAIPrestigeRanker(client=client)

        with pytest.raises(PrestigeRankingError, match="network unavailable"):
            ranker.rank_company("Example")


class TestCachedRanking:
    def test_cached_company_skips_ranker(self) -> None:
        cache = PrestigeCache()
        cached = _assessment()
        cache.put(cached)
        ranker = StubRanker(_assessment(score=50))

        result, changed = get_or_rank_company("MSFT", cache, ranker)

        assert result == cached
        assert changed is False
        assert ranker.calls == []

    def test_unknown_company_is_ranked_and_cached_once(self) -> None:
        cache = PrestigeCache()
        assessment = _assessment(display_name="Stripe", score=91)
        ranker = StubRanker(assessment)

        first, first_changed = get_or_rank_company("Stripe", cache, ranker)
        second, second_changed = get_or_rank_company("Stripe Inc.", cache, ranker)

        assert first == second == assessment
        assert first_changed is True
        assert second_changed is False
        assert ranker.calls == ["Stripe"]

    def test_unknown_company_without_ranker_fails_safely(self) -> None:
        with pytest.raises(PrestigeRankingError, match="no ranker"):
            get_or_rank_company("Unknown", PrestigeCache(), None)

    def test_benchmark_is_ranked_and_becomes_threshold(self) -> None:
        cache = PrestigeCache()
        ranker = StubRanker(_assessment(display_name="Stripe", score=91))
        config = PrestigeTomlConfig(benchmark_company="Stripe")

        threshold, changed = resolve_or_rank_minimum_score(config, cache, ranker)

        assert threshold == 91
        assert changed is True
        assert cache.get("Stripe") is not None

    def test_numeric_threshold_does_not_call_ranker(self) -> None:
        ranker = StubRanker(_assessment())
        threshold, changed = resolve_or_rank_minimum_score(
            PrestigeTomlConfig(minimum_score=75),
            PrestigeCache(),
            ranker,
        )
        assert threshold == 75
        assert changed is False
        assert ranker.calls == []
