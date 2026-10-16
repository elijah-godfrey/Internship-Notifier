"""Rerank cached prestige scores that predate the current web-search rubric."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from internship_notifier.prestige import (
    PRESTIGE_RUBRIC_VERSION,
    CompanyPrestige,
    PrestigeCache,
    load_prestige_cache,
    normalize_company_name,
    save_prestige_cache,
)
from internship_notifier.prestige_ranker import (
    CompanyRanker,
    OpenAIPrestigeRanker,
    PrestigeRankingError,
    _merge_refresh_aliases,
)

RerankConfidence = Literal["low", "medium"]
RERANK_CONFIDENCES = ("low", "medium")
DEFAULT_CACHE_PATH = Path(".github/company-prestige-cache.json")
DEFAULT_LIMIT = 20


def select_rerank_candidates(
    cache: PrestigeCache,
    *,
    confidence: RerankConfidence,
    limit: int,
) -> list[CompanyPrestige]:
    """Return automatic, outdated scores of one confidence, in name order.

    Current-rubric scores, high-confidence scores, and manual overrides are
    excluded. Low and medium are separate passes; run low to completion before
    medium.

    Args:
        cache: Existing prestige cache.
        confidence: ``low`` or ``medium``.
        limit: Maximum number of companies to return. Must be at least 1.

    Returns:
        Assessments that still need a current-rubric score.

    Raises:
        ValueError: If ``confidence`` is not rerankable or ``limit`` is below 1.
    """
    if confidence not in RERANK_CONFIDENCES:
        raise ValueError(
            f"confidence must be one of {list(RERANK_CONFIDENCES)}, got {confidence!r}"
        )
    if limit < 1:
        raise ValueError("limit must be at least 1")
    selected = [
        assessment
        for assessment in cache.companies.values()
        if (
            not assessment.manual_override
            and assessment.confidence == confidence
            and assessment.rubric_version != PRESTIGE_RUBRIC_VERSION
        )
    ]
    selected.sort(key=lambda assessment: normalize_company_name(assessment.display_name))
    return selected[:limit]


def rerank_candidates(
    cache: PrestigeCache,
    candidates: list[CompanyPrestige],
    ranker: CompanyRanker,
    *,
    on_cache_change: Callable[[PrestigeCache], None] | None = None,
    reviewed_at: date | None = None,
) -> tuple[int, tuple[str, ...]]:
    """Score each candidate alone and checkpoint the cache after every success.

    Args:
        cache: Cache updated in place.
        candidates: Assessments selected for this pass.
        ranker: Ranker that searches and scores one company per call.
        on_cache_change: Optional checkpoint callback after each saved company.
        reviewed_at: Review date stored on replacements. Defaults to today.

    Returns:
        How many scores were replaced, and failure messages for the rest.
    """
    refreshed = 0
    failures: list[str] = []
    review_date = reviewed_at or date.today()
    for existing in candidates:
        try:
            new_assessment = ranker.rank_company(
                existing.display_name,
                reviewed_at=review_date,
            )
        except PrestigeRankingError as exc:
            failures.append(f"{existing.display_name}: {exc}")
            continue
        replacement = replace(
            new_assessment,
            aliases=_merge_refresh_aliases(existing, new_assessment),
        )
        if not cache.replace(existing, replacement):
            continue
        refreshed += 1
        if on_cache_change is not None:
            on_cache_change(cache)
    return refreshed, tuple(failures)


def run(argv: list[str] | None = None) -> int:
    """Parse arguments and rerank one confidence pass.

    Args:
        argv: Command-line arguments excluding the executable name.

    Returns:
        ``0`` when every selected company is reranked or the pass is a dry run.
        ``1`` when at least one selected company fails.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Rerank cached companies whose prestige score predates the current "
            "rubric. Runs low confidence first, then medium. High confidence is "
            "left unchanged. This command calls OpenAI and never sends email."
        )
    )
    parser.add_argument(
        "--confidence",
        choices=RERANK_CONFIDENCES,
        required=True,
        help="Which outdated scores to rerank. Run low before medium.",
    )
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=DEFAULT_CACHE_PATH,
        help="Prestige cache JSON path. Default: %(default)s",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        metavar="COUNT",
        help=(
            "Maximum companies to rerank this run. Each company is one request. "
            "Default: %(default)s"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the next companies without calling OpenAI or changing the cache.",
    )
    ns = parser.parse_args(argv)
    if ns.limit < 1:
        raise ValueError("limit must be at least 1")

    cache = load_prestige_cache(ns.cache_path)
    selected = select_rerank_candidates(
        cache,
        confidence=ns.confidence,
        limit=ns.limit,
    )
    print(
        f"Selected {len(selected)} {ns.confidence}-confidence company or companies "
        "still on an older rubric, one company per request.",
        file=sys.stderr,
    )
    if ns.dry_run:
        print("\n".join(assessment.display_name for assessment in selected))
        return 0
    if not selected:
        return 0

    refreshed, failures = rerank_candidates(
        cache,
        selected,
        OpenAIPrestigeRanker(),
        on_cache_change=lambda changed_cache: save_prestige_cache(
            changed_cache,
            ns.cache_path,
        ),
    )
    save_prestige_cache(cache, ns.cache_path)
    print(
        f"Reranked {refreshed} company or companies into {ns.cache_path}.",
        file=sys.stderr,
    )
    for failure in failures:
        print(f"Warning: prestige rerank failed for {failure}", file=sys.stderr)
    return 1 if failures else 0


def main() -> None:
    """Load local dotenv settings and run one rerank pass."""
    load_dotenv(override=False)
    try:
        raise SystemExit(run())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
