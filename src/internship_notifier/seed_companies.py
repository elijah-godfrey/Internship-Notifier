"""Create a plain-text queue of upstream companies missing from the prestige cache.

This module intentionally does not call OpenAI. It prepares a deterministic,
one-company-per-line input file for a future ranking script.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from internship_notifier.github_listings import (
    DEFAULT_REF,
    fetch_listings_json,
    get_listings_metadata,
)
from internship_notifier.prestige import (
    PrestigeCache,
    load_prestige_cache,
    normalize_company_name,
)

DEFAULT_CACHE_PATH = Path(".github/company-prestige-cache.json")
DEFAULT_OUTPUT_PATH = Path("data/uncached-upstream-companies.txt")


def uncached_company_names(
    listings: list[dict[str, Any]],
    cache: PrestigeCache,
) -> list[str]:
    """Return unique upstream company names that do not have a cached assessment.

    Every listing row is considered, including inactive and hidden rows. This
    intentionally matches "all companies currently stored upstream," rather
    than the smaller set that happens to match a notifier's filters.

    Args:
        listings: Parsed upstream ``listings.json`` rows.
        cache: Existing prestige cache, including aliases.

    Returns:
        One stable display name per normalized, uncached company, sorted
        case-insensitively. Blank or malformed company names are skipped.
    """
    names_by_key: dict[str, str] = {}
    for listing in listings:
        company_name = listing.get("company_name")
        if not isinstance(company_name, str) or not company_name.strip():
            continue

        display_name = company_name.strip()
        try:
            normalized = normalize_company_name(display_name)
        except ValueError:
            continue
        if cache.get(display_name) is not None:
            continue

        existing = names_by_key.get(normalized)
        if existing is None or display_name.casefold() < existing.casefold():
            names_by_key[normalized] = display_name

    return sorted(names_by_key.values(), key=str.casefold)


def write_company_seed_file(companies: list[str], output_path: Path) -> None:
    """Write companies to ``output_path`` as one UTF-8 name per line.

    Args:
        companies: Already deduplicated display names.
        output_path: Destination text file. Missing parent directories are made.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(companies)
    output_path.write_text(f"{content}\n" if content else "", encoding="utf-8")


def run(argv: list[str] | None = None) -> int:
    """Fetch upstream listings and write the currently uncached company names.

    Args:
        argv: Command-line arguments, excluding the executable name.

    Returns:
        ``0`` after a successful write or dry run.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Write all company names in upstream listings.json that are absent "
            "from the local prestige cache. This command never calls OpenAI."
        )
    )
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=DEFAULT_CACHE_PATH,
        help="Prestige cache JSON path. Default: %(default)s",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="One-company-per-line output path. Default: %(default)s",
    )
    parser.add_argument(
        "--ref",
        default=DEFAULT_REF,
        help="Upstream branch, tag, or commit SHA. Default: %(default)s",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the output to stdout instead of writing a file.",
    )
    ns = parser.parse_args(argv)

    cache = load_prestige_cache(ns.cache_path)
    metadata = get_listings_metadata(ref=ns.ref)
    listings = fetch_listings_json(metadata["download_url"])
    companies = uncached_company_names(listings, cache)

    if ns.dry_run:
        print("\n".join(companies))
    else:
        write_company_seed_file(companies, ns.output)
        print(
            f"Wrote {len(companies)} uncached company name(s) to {ns.output}.",
            file=sys.stderr,
        )
    return 0


def main() -> None:
    """Load dotenv settings and run the command-line entry point."""
    load_dotenv(override=False)
    try:
        raise SystemExit(run())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
