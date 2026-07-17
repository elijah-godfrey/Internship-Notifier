"""Load filter settings from ``notifier.toml`` (TOML)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ALLOWED_SOURCES = frozenset({"summer2026", "offseason"})
MIN_PRESTIGE_SCORE = 1
MAX_PRESTIGE_SCORE = 100


@dataclass(frozen=True)
class PrestigeTomlConfig:
    """Optional company-prestige threshold configuration.

    Exactly one threshold may be configured: a numeric score or one benchmark
    company whose cached score will become the threshold.
    """

    minimum_score: int | None = None
    benchmark_company: str | None = None

    @property
    def enabled(self) -> bool:
        """Whether prestige filtering was configured."""
        return self.minimum_score is not None or self.benchmark_company is not None


@dataclass(frozen=True)
class NotifierTomlConfig:
    """Filter selection as read from a TOML file.

    Attributes:
        source: ``summer2026`` or ``offseason`` (README parity).
        all_categories: When ``True``, do not filter by category.
        categories: Canonical category names when ``all_categories`` is ``False``.
        prestige: Optional company-prestige threshold settings.
    """

    source: str
    all_categories: bool
    categories: list[str]
    prestige: PrestigeTomlConfig = field(default_factory=PrestigeTomlConfig)


def _load_prestige_config(data: dict[str, Any]) -> PrestigeTomlConfig:
    """Parse the optional ``[prestige]`` table."""
    raw = data.get("prestige")
    if raw is None:
        return PrestigeTomlConfig()
    if not isinstance(raw, dict):
        raise ValueError("notifier.toml: 'prestige' must be a TOML table")

    minimum_score = raw.get("minimum_score")
    benchmark_company = raw.get("benchmark_company")

    if minimum_score is not None:
        if isinstance(minimum_score, bool) or not isinstance(minimum_score, int):
            raise ValueError("notifier.toml: prestige.minimum_score must be an integer")
        if not MIN_PRESTIGE_SCORE <= minimum_score <= MAX_PRESTIGE_SCORE:
            raise ValueError(
                "notifier.toml: prestige.minimum_score must be between "
                f"{MIN_PRESTIGE_SCORE} and {MAX_PRESTIGE_SCORE}"
            )

    if benchmark_company is not None:
        if not isinstance(benchmark_company, str) or not benchmark_company.strip():
            raise ValueError(
                "notifier.toml: prestige.benchmark_company must be a non-empty string"
            )
        benchmark_company = benchmark_company.strip()

    if minimum_score is not None and benchmark_company is not None:
        raise ValueError(
            "notifier.toml: set either prestige.minimum_score or "
            "prestige.benchmark_company, not both"
        )
    if minimum_score is None and benchmark_company is None:
        raise ValueError(
            "notifier.toml: [prestige] must set minimum_score or benchmark_company"
        )

    return PrestigeTomlConfig(
        minimum_score=minimum_score,
        benchmark_company=benchmark_company,
    )


def load_notifier_toml(path: Path) -> NotifierTomlConfig:
    """Parse and validate ``notifier.toml`` at ``path``.

    Args:
        path: Readable file path.

    Returns:
        Validated :class:`NotifierTomlConfig`.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the TOML is invalid or inconsistent.
    """
    if not path.is_file():
        raise FileNotFoundError(str(path))
    raw = path.read_text(encoding="utf-8")
    data: dict[str, Any] = tomllib.loads(raw)
    source = data.get("source")
    if not isinstance(source, str) or source not in ALLOWED_SOURCES:
        raise ValueError(
            f"notifier.toml: 'source' must be one of {sorted(ALLOWED_SOURCES)!r}, got {source!r}"
        )
    all_categories = data.get("all_categories", False)
    if not isinstance(all_categories, bool):
        raise ValueError("notifier.toml: 'all_categories' must be a boolean")

    categories_raw = data.get("categories", [])
    if all_categories:
        categories: list[str] = []
    else:
        cats_ok = isinstance(categories_raw, list) and all(
            isinstance(x, str) for x in categories_raw
        )
        if not cats_ok:
            raise ValueError(
                "notifier.toml: 'categories' must be an array of strings when "
                "all_categories is false"
            )
        categories = [str(x).strip() for x in categories_raw if str(x).strip()]
        if not categories:
            raise ValueError(
                "notifier.toml: 'categories' must be non-empty when all_categories is false "
                "(or set all_categories = true)"
            )
    return NotifierTomlConfig(
        source=source,
        all_categories=all_categories,
        categories=categories,
        prestige=_load_prestige_config(data),
    )


def resolve_config_path(explicit: Path | None) -> Path | None:
    """Pick which TOML path to load, if any.

    Precedence: ``explicit`` argument (must exist when provided), then
    ``NOTIFIER_CONFIG`` environment variable, then ``./notifier.toml`` in the
    current working directory.

    Args:
        explicit: ``--config`` path from the CLI, or ``None``.

    Returns:
        A path that exists as a file, or ``None`` when no automatic candidate
        exists.

    Raises:
        FileNotFoundError: When ``explicit`` is set but is not a readable file.
    """
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(f"notifier config not found: {explicit}")
        return explicit
    env_path = (os.environ.get("NOTIFIER_CONFIG") or "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
    cwd_file = Path.cwd() / "notifier.toml"
    if cwd_file.is_file():
        return cwd_file
    return None
