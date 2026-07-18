"""Company prestige score normalization, caching, and threshold checks."""

from __future__ import annotations

import json
import re
import unicodedata
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from internship_notifier.config_toml import (
    MAX_PRESTIGE_SCORE,
    MIN_PRESTIGE_SCORE,
    PrestigeTomlConfig,
)
from internship_notifier.state import default_state_path

CACHE_SCHEMA_VERSION = 1
PRESTIGE_RUBRIC_VERSION = "prestige-v1"
DEFAULT_REFRESH_AFTER_MONTHS = 4
DEFAULT_MAX_REFRESHES_PER_RUN = 25
Confidence = Literal["low", "medium", "high"]
ALLOWED_CONFIDENCE: frozenset[str] = frozenset({"low", "medium", "high"})

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_PERIODS = re.compile(r"\.")
_LEGAL_SUFFIXES = frozenset(
    {
        "co",
        "company",
        "corp",
        "corporation",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "ltd",
        "plc",
    }
)


def normalize_company_name(name: str) -> str:
    """Return a stable cache key for a company name.

    Normalization is intentionally conservative: it folds case and punctuation,
    treats ``&`` as ``and``, and removes common trailing legal suffixes.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("company name must be a non-empty string")

    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    without_periods = _PERIODS.sub("", folded.casefold())
    tokens = _NON_ALPHANUMERIC.sub(" ", without_periods.replace("&", " and ")).split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    if tokens and tokens[-1] == "and":
        tokens.pop()
    if not tokens:
        raise ValueError("company name must contain letters or numbers")
    return " ".join(tokens)


def validate_prestige_score(score: object) -> int:
    """Validate and return a 1-100 prestige score."""
    if isinstance(score, bool) or not isinstance(score, int):
        raise ValueError("prestige score must be an integer")
    if not MIN_PRESTIGE_SCORE <= score <= MAX_PRESTIGE_SCORE:
        raise ValueError(
            f"prestige score must be between {MIN_PRESTIGE_SCORE} and {MAX_PRESTIGE_SCORE}"
        )
    return score


@dataclass(frozen=True)
class CompanyPrestige:
    """A cached prestige assessment for one company."""

    display_name: str
    prestige_score: int
    confidence: Confidence
    reason: str
    reviewed_at: date
    model: str
    rubric_version: str = PRESTIGE_RUBRIC_VERSION
    aliases: tuple[str, ...] = ()
    manual_override: bool = False

    def __post_init__(self) -> None:
        normalize_company_name(self.display_name)
        validate_prestige_score(self.prestige_score)
        if self.confidence not in ALLOWED_CONFIDENCE:
            raise ValueError(f"confidence must be one of {sorted(ALLOWED_CONFIDENCE)!r}")
        if not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if not self.rubric_version.strip():
            raise ValueError("rubric_version must be a non-empty string")
        for alias in self.aliases:
            normalize_company_name(alias)

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize this assessment for the on-disk cache."""
        return {
            "aliases": list(self.aliases),
            "confidence": self.confidence,
            "display_name": self.display_name,
            "manual_override": self.manual_override,
            "model": self.model,
            "prestige_score": self.prestige_score,
            "reason": self.reason,
            "reviewed_at": self.reviewed_at.isoformat(),
            "rubric_version": self.rubric_version,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> CompanyPrestige:
        """Validate and deserialize one cached assessment."""
        aliases = data.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(x, str) for x in aliases):
            raise ValueError("company prestige aliases must be a list of strings")

        manual_override = data.get("manual_override", False)
        if not isinstance(manual_override, bool):
            raise ValueError("company prestige manual_override must be a boolean")

        reviewed_at = data.get("reviewed_at")
        if not isinstance(reviewed_at, str):
            raise ValueError("company prestige reviewed_at must be an ISO date string")
        try:
            review_date = date.fromisoformat(reviewed_at)
        except ValueError as exc:
            raise ValueError(
                "company prestige reviewed_at must be an ISO date string"
            ) from exc

        required_strings = (
            "display_name",
            "confidence",
            "reason",
            "model",
            "rubric_version",
        )
        for key in required_strings:
            if not isinstance(data.get(key), str):
                raise ValueError(f"company prestige {key} must be a string")

        return cls(
            display_name=data["display_name"],
            prestige_score=validate_prestige_score(data.get("prestige_score")),
            confidence=data["confidence"],
            reason=data["reason"],
            reviewed_at=review_date,
            model=data["model"],
            rubric_version=data["rubric_version"],
            aliases=tuple(aliases),
            manual_override=manual_override,
        )


@dataclass
class PrestigeCache:
    """In-memory company prestige assessments indexed by normalized name."""

    companies: dict[str, CompanyPrestige] = field(default_factory=dict)

    def get(self, company_name: str) -> CompanyPrestige | None:
        """Find a company by its display name, cache key, or alias."""
        requested = normalize_company_name(company_name)
        direct = self.companies.get(requested)
        if direct is not None:
            return direct
        for assessment in self.companies.values():
            names = (assessment.display_name, *assessment.aliases)
            if any(normalize_company_name(name) == requested for name in names):
                return assessment
        return None

    def put(self, assessment: CompanyPrestige) -> bool:
        """Insert or replace an assessment.

        Automatic assessments cannot replace a manual override. Returns whether
        the cache changed.
        """
        key = normalize_company_name(assessment.display_name)
        existing = self.companies.get(key)
        if (
            existing is not None
            and existing.manual_override
            and not assessment.manual_override
        ):
            return False
        if existing == assessment:
            return False
        self.companies[key] = assessment
        return True

    def replace(
        self,
        existing: CompanyPrestige,
        replacement: CompanyPrestige,
    ) -> bool:
        """Replace an automatic assessment while preserving manual overrides."""
        old_key = normalize_company_name(existing.display_name)
        current = self.companies.get(old_key)
        if current is None:
            return self.put(replacement)
        if current.manual_override:
            return False

        new_key = normalize_company_name(replacement.display_name)
        target = self.companies.get(new_key)
        if target is not None and target != current and target.manual_override:
            return False

        self.companies.pop(old_key)
        return self.put(replacement)

    def stale_assessments(
        self,
        *,
        as_of: date | None = None,
        refresh_after_months: int = DEFAULT_REFRESH_AFTER_MONTHS,
        limit: int = DEFAULT_MAX_REFRESHES_PER_RUN,
    ) -> list[CompanyPrestige]:
        """Return oldest stale automatic assessments, capped per run."""
        if refresh_after_months < 1:
            raise ValueError("refresh_after_months must be at least 1")
        if limit < 0:
            raise ValueError("limit must not be negative")
        cutoff = _months_before(as_of or date.today(), refresh_after_months)
        stale = [
            assessment
            for assessment in self.companies.values()
            if not assessment.manual_override and assessment.reviewed_at <= cutoff
        ]
        return sorted(stale, key=lambda assessment: assessment.reviewed_at)[:limit]

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize the complete cache with a schema version."""
        return {
            "companies": {
                key: assessment.to_json_dict()
                for key, assessment in sorted(self.companies.items())
            },
            "schema_version": CACHE_SCHEMA_VERSION,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> PrestigeCache:
        """Validate and deserialize a complete cache."""
        if data.get("schema_version") != CACHE_SCHEMA_VERSION:
            raise ValueError(
                f"prestige cache schema_version must be {CACHE_SCHEMA_VERSION}"
            )
        raw_companies = data.get("companies")
        if not isinstance(raw_companies, dict):
            raise ValueError("prestige cache companies must be an object")

        cache = cls()
        for key, raw_assessment in raw_companies.items():
            if not isinstance(key, str) or not isinstance(raw_assessment, dict):
                raise ValueError("prestige cache entries must be JSON objects")
            assessment = CompanyPrestige.from_json_dict(raw_assessment)
            expected_key = normalize_company_name(assessment.display_name)
            if key != expected_key:
                raise ValueError(
                    f"prestige cache key {key!r} does not match display name "
                    f"(expected {expected_key!r})"
                )
            cache.companies[key] = assessment
        return cache


def default_prestige_cache_path() -> Path:
    """Return the local cache path beside the notifier state file."""
    return default_state_path().with_name("company-prestige-cache.json")


def _months_before(value: date, months: int) -> date:
    """Subtract calendar months while clamping to the target month's last day."""
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def load_prestige_cache(path: Path | None = None) -> PrestigeCache:
    """Load a prestige cache, returning an empty cache when missing."""
    cache_path = path or default_prestige_cache_path()
    if not cache_path.is_file():
        return PrestigeCache()
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("prestige cache must contain a JSON object")
    return PrestigeCache.from_json_dict(data)


def save_prestige_cache(cache: PrestigeCache, path: Path | None = None) -> None:
    """Persist a prestige cache as deterministic, human-readable JSON."""
    cache_path = path or default_prestige_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cache.to_json_dict(), indent=2, sort_keys=True)
    cache_path.write_text(payload + "\n", encoding="utf-8")


def resolve_minimum_score(
    config: PrestigeTomlConfig,
    cache: PrestigeCache,
) -> int | None:
    """Resolve numeric or benchmark configuration into a score threshold.

    Returns ``None`` when prestige filtering is disabled. An unknown benchmark
    also returns ``None`` for now; the LLM-ranking increment will resolve it.
    """
    if config.minimum_score is not None:
        return config.minimum_score
    if config.benchmark_company is None:
        return None
    benchmark = cache.get(config.benchmark_company)
    return benchmark.prestige_score if benchmark is not None else None


def meets_prestige_threshold(prestige_score: int, minimum_score: int) -> bool:
    """Whether a company is equal to or above the configured threshold."""
    return validate_prestige_score(prestige_score) >= validate_prestige_score(
        minimum_score
    )
