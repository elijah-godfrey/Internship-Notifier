"""Manually seed the prestige cache from one or more plain-text company lists."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from internship_notifier.prestige import (
    PrestigeCache,
    load_prestige_cache,
    normalize_company_name,
    save_prestige_cache,
)
from internship_notifier.prestige_ranker import (
    DEFAULT_RANKING_BATCH_SIZE,
    OpenAIPrestigeRanker,
    get_or_rank_companies,
)

DEFAULT_CACHE_PATH = Path(".github/company-prestige-cache.json")
DEFAULT_MAX_COMPANIES = 100


def load_candidate_names(paths: list[Path]) -> list[str]:
    """Load unique company names from plain-text files in their supplied order.

    Empty lines and lines beginning with ``#`` are ignored. Duplicate names are
    identified with the same normalization used by the prestige cache.

    Args:
        paths: One or more UTF-8 files containing one company name per line.

    Returns:
        Ordered, normalized-deduplicated display names.

    Raises:
        ValueError: If a non-comment line is not a valid company name.
        OSError: If an input file cannot be read.
    """
    candidates: dict[str, str] = {}
    for path in paths:
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            name = raw_line.strip()
            if not name or name.startswith("#"):
                continue
            try:
                key = normalize_company_name(name)
            except ValueError as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
            candidates.setdefault(key, name)
    return list(candidates.values())


def select_uncached_candidates(
    candidates: list[str],
    cache: PrestigeCache,
    *,
    limit: int,
) -> list[str]:
    """Keep at most ``limit`` candidates that do not resolve in ``cache``.

    Args:
        candidates: Ordered names from one or more source lists.
        cache: Existing cache, including aliases.
        limit: Maximum number to rank this invocation.

    Returns:
        Ordered names that need an assessment.

    Raises:
        ValueError: If ``limit`` is less than one.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")

    selected: list[str] = []
    for name in candidates:
        if cache.get(name) is not None:
            continue
        selected.append(name)
        if len(selected) == limit:
            break
    return selected


def seed_cache(
    candidates: list[str],
    cache: PrestigeCache,
    *,
    limit: int = DEFAULT_MAX_COMPANIES,
    on_cache_change: Callable[[PrestigeCache], None] | None = None,
) -> tuple[list[str], bool]:
    """Rank a bounded ordered slice of uncached candidates and checkpoint batches.

    ``get_or_rank_companies`` sends at most 20 names per request and invokes
    ``on_cache_change`` after each successful batch. Therefore a later failed
    batch does not discard earlier completed work.

    Args:
        candidates: Ordered candidate company names.
        cache: Cache to update in place.
        limit: Maximum candidates to rank in this run; defaults to 100.
        on_cache_change: Optional checkpoint callback after each changed batch.

    Returns:
        The selected uncached names and whether the cache changed.
    """
    selected = select_uncached_candidates(candidates, cache, limit=limit)
    if not selected:
        return [], False
    _, changed = get_or_rank_companies(
        selected,
        cache,
        OpenAIPrestigeRanker(),
        batch_size=DEFAULT_RANKING_BATCH_SIZE,
        on_cache_change=on_cache_change,
    )
    return selected, changed


def run(argv: list[str] | None = None) -> int:
    """Parse arguments and seed a bounded set of prestige-cache entries.

    Args:
        argv: Command-line arguments excluding the executable name.

    Returns:
        ``0`` after a successful seed or dry-run preview.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Rank a bounded, ordered set of uncached companies into the prestige "
            "cache. This manual command calls OpenAI; it never sends email."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        action="append",
        required=True,
        metavar="PATH",
        help="One-company-per-line source file; repeat to merge files by priority.",
    )
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=DEFAULT_CACHE_PATH,
        help="Prestige cache JSON path. Default: %(default)s",
    )
    parser.add_argument(
        "--max-companies",
        type=int,
        default=DEFAULT_MAX_COMPANIES,
        metavar="COUNT",
        help="Maximum uncached companies to rank this run. Default: %(default)s",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the next candidates without calling OpenAI or changing the cache.",
    )
    ns = parser.parse_args(argv)

    candidates = load_candidate_names(ns.input)
    cache = load_prestige_cache(ns.cache_path)
    selected = select_uncached_candidates(
        candidates,
        cache,
        limit=ns.max_companies,
    )
    batch_count = (len(selected) + DEFAULT_RANKING_BATCH_SIZE - 1) // DEFAULT_RANKING_BATCH_SIZE
    print(
        f"Selected {len(selected)} uncached company or companies from "
        f"{len(candidates)} candidate(s) in {batch_count} batch(es).",
        file=sys.stderr,
    )
    if ns.dry_run:
        print("\n".join(selected))
        return 0
    if not selected:
        return 0

    _, changed = seed_cache(
        candidates,
        cache,
        limit=ns.max_companies,
        on_cache_change=lambda changed_cache: save_prestige_cache(
            changed_cache,
            ns.cache_path,
        ),
    )
    if changed:
        save_prestige_cache(cache, ns.cache_path)
    print(
        f"Seeded {len(selected)} company or companies into {ns.cache_path}.",
        file=sys.stderr,
    )
    return 0


def main() -> None:
    """Load local dotenv settings and run the seed command."""
    load_dotenv(override=False)
    try:
        raise SystemExit(run())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
