"""Unit tests for company prestige normalization, caching, and thresholds."""

from __future__ import annotations

import json
from datetime import date

import pytest

from internship_notifier.config_toml import PrestigeTomlConfig
from internship_notifier.prestige import (
    CACHE_SCHEMA_VERSION,
    CompanyPrestige,
    PrestigeCache,
    load_prestige_cache,
    meets_prestige_threshold,
    normalize_company_name,
    resolve_minimum_score,
    save_prestige_cache,
    validate_prestige_score,
)


def _assessment(
    *,
    display_name: str = "Microsoft Corporation",
    score: int = 88,
    aliases: tuple[str, ...] = ("Microsoft", "MSFT"),
    manual_override: bool = False,
    reviewed_at: date = date(2026, 7, 17),
) -> CompanyPrestige:
    return CompanyPrestige(
        display_name=display_name,
        prestige_score=score,
        confidence="high",
        reason="Strong and widely recognized software engineering reputation.",
        reviewed_at=reviewed_at,
        model="test-model",
        aliases=aliases,
        manual_override=manual_override,
    )


class TestNormalizeCompanyName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Google LLC", "google"),
            ("  MICROSOFT Corporation ", "microsoft"),
            ("J.P. Morgan & Co.", "jp morgan"),
            ("Procter & Gamble", "procter and gamble"),
            ("Café Inc.", "cafe"),
        ],
    )
    def test_normalizes_company_names(self, raw: str, expected: str) -> None:
        assert normalize_company_name(raw) == expected

    @pytest.mark.parametrize("name", ["", "   ", "!!!"])
    def test_rejects_empty_normalized_name(self, name: str) -> None:
        with pytest.raises(ValueError, match="company name"):
            normalize_company_name(name)


class TestCompanyPrestige:
    @pytest.mark.parametrize("score", [1, 50, 100])
    def test_accepts_scores_in_range(self, score: int) -> None:
        assert validate_prestige_score(score) == score

    @pytest.mark.parametrize("score", [0, 101, 1.5, True, "90"])
    def test_rejects_invalid_scores(self, score: object) -> None:
        with pytest.raises(ValueError, match="prestige score"):
            validate_prestige_score(score)

    def test_json_roundtrip(self) -> None:
        assessment = _assessment(manual_override=True)
        assert CompanyPrestige.from_json_dict(assessment.to_json_dict()) == assessment

    def test_rejects_invalid_confidence(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            CompanyPrestige(
                display_name="Example",
                prestige_score=70,
                confidence="certain",  # type: ignore[arg-type]
                reason="Example reason",
                reviewed_at=date(2026, 7, 17),
                model="manual",
            )


class TestPrestigeCache:
    def test_put_and_lookup_by_display_name_or_alias(self) -> None:
        assessment = _assessment()
        cache = PrestigeCache()
        cache.put(assessment)

        assert cache.get("Microsoft Corp.") == assessment
        assert cache.get("MSFT") == assessment
        assert cache.get("Unknown") is None

    def test_put_replaces_same_normalized_company(self) -> None:
        cache = PrestigeCache()
        cache.put(_assessment(score=80))
        cache.put(_assessment(display_name="Microsoft", score=90))
        assert len(cache.companies) == 1
        stored = cache.get("Microsoft")
        assert stored is not None
        assert stored.prestige_score == 90

    def test_automatic_score_does_not_replace_manual_override(self) -> None:
        cache = PrestigeCache()
        manual = _assessment(score=92, manual_override=True)
        cache.put(manual)

        changed = cache.put(_assessment(score=70))

        assert changed is False
        assert cache.get("Microsoft") == manual

    def test_replace_can_change_canonical_name(self) -> None:
        cache = PrestigeCache()
        existing = _assessment(display_name="Facebook", aliases=())
        replacement = _assessment(display_name="Meta", aliases=("Facebook",))
        cache.put(existing)

        assert cache.replace(existing, replacement) is True
        assert cache.get("Facebook") == replacement
        assert "facebook" not in cache.companies

    def test_stale_assessments_are_oldest_first_and_limited(self) -> None:
        cache = PrestigeCache()
        cache.put(
            _assessment(
                display_name="Oldest",
                aliases=(),
                reviewed_at=date(2025, 1, 1),
            )
        )
        cache.put(
            _assessment(
                display_name="Boundary",
                aliases=(),
                reviewed_at=date(2026, 3, 17),
            )
        )
        cache.put(
            _assessment(
                display_name="Fresh",
                aliases=(),
                reviewed_at=date(2026, 3, 18),
            )
        )

        all_stale = cache.stale_assessments(as_of=date(2026, 7, 17))
        limited = cache.stale_assessments(as_of=date(2026, 7, 17), limit=1)

        assert [entry.display_name for entry in all_stale] == [
            "Oldest",
            "Boundary",
        ]
        assert [entry.display_name for entry in limited] == ["Oldest"]

    def test_stale_assessments_skip_manual_overrides(self) -> None:
        cache = PrestigeCache()
        cache.put(
            _assessment(
                display_name="Manual",
                aliases=(),
                reviewed_at=date(2020, 1, 1),
                manual_override=True,
            )
        )
        assert cache.stale_assessments(as_of=date(2026, 7, 17)) == []

    def test_stale_assessments_default_limit_is_25(self) -> None:
        cache = PrestigeCache()
        for index in range(30):
            cache.put(
                _assessment(
                    display_name=f"Company {index}",
                    aliases=(),
                    reviewed_at=date(2025, 1, 1),
                )
            )
        assert len(cache.stale_assessments(as_of=date(2026, 7, 17))) == 25

    def test_save_and_load_roundtrip(self, tmp_path) -> None:
        path = tmp_path / "cache.json"
        cache = PrestigeCache()
        cache.put(_assessment())

        save_prestige_cache(cache, path)

        assert load_prestige_cache(path) == cache
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["schema_version"] == CACHE_SCHEMA_VERSION

    def test_missing_file_returns_empty_cache(self, tmp_path) -> None:
        assert load_prestige_cache(tmp_path / "missing.json") == PrestigeCache()

    def test_rejects_wrong_schema_version(self, tmp_path) -> None:
        path = tmp_path / "cache.json"
        path.write_text(
            json.dumps({"schema_version": 99, "companies": {}}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="schema_version"):
            load_prestige_cache(path)

    def test_rejects_cache_key_that_does_not_match_name(self) -> None:
        data = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "companies": {"wrong-key": _assessment().to_json_dict()},
        }
        with pytest.raises(ValueError, match="does not match"):
            PrestigeCache.from_json_dict(data)


class TestPrestigeThreshold:
    def test_numeric_threshold_resolves_without_cache(self) -> None:
        config = PrestigeTomlConfig(minimum_score=75)
        assert resolve_minimum_score(config, PrestigeCache()) == 75

    def test_benchmark_threshold_uses_cached_company_score(self) -> None:
        cache = PrestigeCache()
        cache.put(_assessment(score=88))
        config = PrestigeTomlConfig(benchmark_company="MSFT")
        assert resolve_minimum_score(config, cache) == 88

    def test_unknown_benchmark_is_unresolved(self) -> None:
        config = PrestigeTomlConfig(benchmark_company="Unknown Company")
        assert resolve_minimum_score(config, PrestigeCache()) is None

    def test_disabled_prestige_has_no_threshold(self) -> None:
        assert resolve_minimum_score(PrestigeTomlConfig(), PrestigeCache()) is None

    def test_equal_score_meets_threshold(self) -> None:
        assert meets_prestige_threshold(75, 75) is True

    def test_lower_score_does_not_meet_threshold(self) -> None:
        assert meets_prestige_threshold(74, 75) is False
