"""Command-line entry: poll upstream, filter, diff against state, persist."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from internship_notifier import filters
from internship_notifier import github_listings
from internship_notifier.state import load_state, save_state


def _apply_source(
    listings: list[dict[str, Any]],
    source: str,
) -> list[dict[str, Any]]:
    if source == "summer2026":
        return filters.filter_summer(listings)
    if source == "offseason":
        return filters.filter_off_season(listings)
    raise ValueError(f"unknown source: {source!r}")


def _format_listing_line(listing: dict[str, Any]) -> str:
    company = listing.get("company_name", "?")
    title = listing.get("title", "?")
    cat = listing.get("category", "?")
    url = listing.get("url", "")
    return f"{company} | {title} | {cat} | {url}"


def run(argv: list[str] | None = None) -> int:
    """Parse CLI args, run one poll cycle, return process exit code.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        ``0`` on success, ``1`` on usage/runtime errors, ``2`` when the user must
        run ``--bootstrap`` first.
    """
    parser = argparse.ArgumentParser(
        description="Poll SimplifyJobs Summer2026-Internships listings.json, "
        "filter by README rules, and track new IDs in local state.",
    )
    parser.add_argument(
        "--source",
        choices=("summer2026", "offseason"),
        required=True,
        help="Which upstream README logic to mirror.",
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--category",
        action="append",
        dest="categories",
        metavar="NAME",
        help="Restrict to this category (repeatable). Example: --category "
        '"Software Engineering"',
    )
    g.add_argument(
        "--all-categories",
        action="store_true",
        help="Do not filter by category (all roles in the chosen source).",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=None,
        help="JSON state file (default: per-OS app data path).",
    )
    parser.add_argument(
        "--ref",
        default=github_listings.DEFAULT_REF,
        help="Upstream git ref (branch/tag/commit). Default: %(default)s.",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Mark all current filtered listings as seen (no 'new' output). "
        "Use once before normal polling.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions but do not write state.",
    )
    ns = parser.parse_args(argv)

    categories: list[str] | None = None
    if ns.categories:
        categories = ns.categories
    all_categories: bool = bool(ns.all_categories)

    state_path: Path | None = ns.state_path
    state = load_state(state_path)

    meta = github_listings.get_listings_metadata(ref=ns.ref)
    new_sha: str = meta["sha"]

    if (
        not ns.bootstrap
        and state.listings_sha
        and new_sha == state.listings_sha
    ):
        print("No upstream change (listings.json blob sha unchanged).", file=sys.stderr)
        return 0

    raw = github_listings.fetch_listings_json(meta["download_url"])
    filtered = _apply_source(raw, ns.source)
    if not all_categories:
        if not categories:
            raise ValueError("pass at least one --category or use --all-categories")
        filtered = filters.filter_by_categories(filtered, set(categories))

    if ns.bootstrap:
        ids = {str(L["id"]) for L in filtered if "id" in L}
        if not ns.dry_run:
            state.seen_ids |= ids
            state.listings_sha = new_sha
            save_state(state, state_path)
        print(
            f"Bootstrap: marked {len(ids)} listing(s) as seen "
            f"({'dry-run, not saved' if ns.dry_run else 'saved'}).",
            file=sys.stderr,
        )
        return 0

    if not state.seen_ids and not ns.dry_run:
        print(
            "State has no seen_ids yet. Run once with --bootstrap, then poll without it.",
            file=sys.stderr,
        )
        return 2

    new_rows = [L for L in filtered if str(L.get("id", "")) not in state.seen_ids]
    for row in new_rows:
        print(_format_listing_line(row))

    if ns.dry_run:
        print(
            f"Dry-run: {len(new_rows)} new listing(s) (state not saved).",
            file=sys.stderr,
        )
        return 0

    state.seen_ids |= {str(L["id"]) for L in new_rows if L.get("id") is not None}
    state.listings_sha = new_sha
    save_state(state, state_path)
    print(
        f"Saved state ({len(new_rows)} new id(s), sha={new_sha[:12]}...).",
        file=sys.stderr,
    )
    return 0


def main() -> None:
    """Entry point for ``python -m internship_notifier`` / console script."""
    try:
        raise SystemExit(run())
    except (RuntimeError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
