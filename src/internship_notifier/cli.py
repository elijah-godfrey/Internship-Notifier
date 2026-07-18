"""Command-line entry: poll upstream, filter, diff against state, persist."""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from internship_notifier import filters, github_listings, smtp_notify
from internship_notifier.config_toml import (
    NotifierTomlConfig,
    PrestigeTomlConfig,
    load_notifier_toml,
    resolve_config_path,
)
from internship_notifier.prestige import (
    PrestigeCache,
    load_prestige_cache,
    meets_prestige_threshold,
    resolve_minimum_score,
    save_prestige_cache,
)
from internship_notifier.prestige_ranker import (
    OpenAIPrestigeRanker,
    get_or_rank_companies,
    refresh_stale_companies,
)
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


def _merge_filter_options(
    ns: argparse.Namespace,
    file_cfg: NotifierTomlConfig | None,
) -> tuple[str, bool, list[str]]:
    """Combine TOML defaults with optional CLI overrides.

    CLI flags override the file when supplied: ``--source``, ``--all-categories``,
    or any ``--category`` (including an empty override list, which is invalid).
    """
    if file_cfg is None:
        if ns.source is None:
            raise ValueError(
                "Missing filter settings: add notifier.toml in the current directory, "
                "set NOTIFIER_CONFIG to a TOML path, or pass --source and "
                "--category / --all-categories."
            )
        source = ns.source
        if ns.all_categories:
            return source, True, []
        cats = list(ns.categories) if ns.categories else []
        if not cats:
            raise ValueError(
                "Pass --category (repeatable) or --all-categories when no notifier.toml is used."
            )
        return source, False, cats

    source = ns.source if ns.source is not None else file_cfg.source
    if ns.all_categories:
        return source, True, []
    if ns.categories is not None:
        cats = list(ns.categories)
        if not cats:
            raise ValueError("When using --category, pass at least one category name.")
        return source, False, cats
    if file_cfg.all_categories:
        return source, True, []
    return source, False, list(file_cfg.categories)


def _seen_id_strings(listings: list[dict[str, Any]]) -> set[str]:
    """Stable string ids for listings that define ``id`` (skip null/missing)."""
    return {str(L["id"]) for L in listings if L.get("id") is not None}


def _format_listing_line(listing: dict[str, Any]) -> str:
    company = listing.get("company_name", "?")
    title = listing.get("title", "?")
    cat = listing.get("category", "?")
    url = listing.get("url", "")
    return f"{company} | {title} | {cat} | {url}"


def _filter_new_rows_by_prestige(
    rows: list[dict[str, Any]],
    config: PrestigeTomlConfig,
    cache: PrestigeCache,
) -> tuple[list[dict[str, Any]], bool, int | None]:
    """Rank unknown companies and keep rows meeting the prestige threshold."""
    if not config.enabled or not rows:
        return rows, False, None

    row_companies: list[str] = []
    for row in rows:
        company = row.get("company_name")
        if not isinstance(company, str) or not company.strip():
            raise RuntimeError("new listing is missing a company_name for prestige ranking")
        row_companies.append(company)

    requested_companies = (
        [config.benchmark_company, *row_companies]
        if config.benchmark_company is not None
        else row_companies
    )
    has_unknown = any(cache.get(company) is None for company in requested_companies)
    assessments, cache_changed = get_or_rank_companies(
        requested_companies,
        cache,
        OpenAIPrestigeRanker() if has_unknown else None,
    )
    threshold = resolve_minimum_score(config, cache)
    if threshold is None:
        raise RuntimeError("prestige filtering is enabled but has no score threshold")

    row_assessments = assessments[-len(rows) :]
    eligible: list[dict[str, Any]] = []
    for row, assessment in zip(rows, row_assessments, strict=True):
        if meets_prestige_threshold(assessment.prestige_score, threshold):
            eligible.append(row)

    return eligible, cache_changed, threshold


def _refresh_prestige_cache(
    config: PrestigeTomlConfig,
    cache: PrestigeCache,
    cache_path: Path | None,
    *,
    dry_run: bool,
) -> None:
    """Refresh a bounded stale-cache batch without blocking normal polling."""
    if not config.enabled or dry_run or not cache.stale_assessments():
        return
    try:
        ranker = OpenAIPrestigeRanker()
    except RuntimeError as exc:
        print(f"Warning: prestige cache refresh skipped: {exc}", file=sys.stderr)
        return

    result = refresh_stale_companies(cache, ranker)
    if result.refreshed:
        save_prestige_cache(cache, cache_path)
    print(
        f"Prestige cache refresh: {result.refreshed} updated, "
        f"{len(result.failures)} failed.",
        file=sys.stderr,
    )
    for failure in result.failures:
        print(f"Warning: prestige refresh failed for {failure}", file=sys.stderr)


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
        epilog="Filter defaults: notifier.toml in the current directory, or "
        "NOTIFIER_CONFIG, unless --no-config-file. Override with --source / "
        "--category / --all-categories. Email: SMTP_HOST, SMTP_FROM, SMTP_TO; "
        "optional SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_SUBJECT_PREFIX.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="TOML file with source and categories (default: NOTIFIER_CONFIG or "
        "./notifier.toml when that file exists).",
    )
    parser.add_argument(
        "--no-config-file",
        action="store_true",
        help="Ignore notifier.toml and NOTIFIER_CONFIG even if they exist.",
    )
    parser.add_argument(
        "--source",
        choices=("summer2026", "offseason"),
        default=None,
        help="Which upstream README logic to mirror (overrides notifier.toml).",
    )
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        default=None,
        metavar="NAME",
        help="Restrict to this category (repeatable); overrides notifier.toml.",
    )
    parser.add_argument(
        "--all-categories",
        action="store_true",
        help="Include every category for the chosen source (overrides notifier.toml).",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=None,
        help="JSON state file (default: per-OS app data path).",
    )
    parser.add_argument(
        "--prestige-cache-path",
        type=Path,
        default=None,
        help="Company prestige JSON cache (default: beside the local state file).",
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

    try:
        cfg_path = None if ns.no_config_file else resolve_config_path(ns.config)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    file_cfg: NotifierTomlConfig | None = None
    if cfg_path is not None:
        file_cfg = load_notifier_toml(cfg_path)
        print(f"Using notifier config: {cfg_path.resolve()}", file=sys.stderr)

    try:
        source, all_categories, categories = _merge_filter_options(ns, file_cfg)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    state_path: Path | None = ns.state_path
    state = load_state(state_path)
    prestige_cache_path: Path | None = ns.prestige_cache_path
    if prestige_cache_path is None and state_path is not None:
        prestige_cache_path = state_path.with_name("company-prestige-cache.json")
    prestige_config = (
        file_cfg.prestige if file_cfg is not None else PrestigeTomlConfig()
    )
    prestige_cache = (
        load_prestige_cache(prestige_cache_path)
        if prestige_config.enabled
        else PrestigeCache()
    )
    _refresh_prestige_cache(
        prestige_config,
        prestige_cache,
        prestige_cache_path,
        dry_run=ns.dry_run,
    )

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
    filtered = _apply_source(raw, source)
    if not all_categories:
        filtered = filters.filter_by_categories(filtered, set(categories))

    if ns.bootstrap:
        ids = _seen_id_strings(filtered)
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
    notify_rows, prestige_cache_changed, prestige_threshold = (
        _filter_new_rows_by_prestige(
            new_rows,
            prestige_config,
            prestige_cache,
        )
    )
    if prestige_threshold is not None:
        print(
            f"Prestige filter: {len(notify_rows)} of {len(new_rows)} new listing(s) "
            f"met minimum score {prestige_threshold}.",
            file=sys.stderr,
        )
    if prestige_cache_changed and not ns.dry_run:
        save_prestige_cache(prestige_cache, prestige_cache_path)

    for row in notify_rows:
        print(_format_listing_line(row))

    settings = smtp_notify.settings_from_env()
    if notify_rows and settings:
        prefix = (
            os.environ.get("SMTP_SUBJECT_PREFIX") or smtp_notify.DEFAULT_SUBJECT_PREFIX
        ).strip()
        subject = f"{prefix}: {len(notify_rows)} new listing(s)"
        body = "\n".join(_format_listing_line(r) for r in notify_rows) + "\n"
        if ns.dry_run:
            print(
                f"Dry-run: would email {settings.mail_to} "
                f"({len(notify_rows)} listing(s)).",
                file=sys.stderr,
            )
        else:
            smtp_notify.send_plaintext_email(
                subject=subject,
                body=body,
                settings=settings,
            )
            print(f"Sent email to {settings.mail_to}.", file=sys.stderr)

    if ns.dry_run:
        print(
            f"Dry-run: {len(new_rows)} new listing(s), {len(notify_rows)} eligible "
            "(state and prestige cache not saved).",
            file=sys.stderr,
        )
        return 0

    state.seen_ids |= _seen_id_strings(new_rows)
    state.listings_sha = new_sha
    save_state(state, state_path)
    print(
        f"Saved state ({len(new_rows)} new id(s), sha={new_sha[:12]}...).",
        file=sys.stderr,
    )
    return 0


def main() -> None:
    """Entry point for ``python -m internship_notifier`` / console script."""
    load_dotenv(override=False)
    alt = (os.environ.get("DOTENV_PATH") or "").strip()
    if alt:
        load_dotenv(dotenv_path=Path(alt), override=False)
    try:
        raise SystemExit(run())
    except (RuntimeError, ValueError, OSError, smtplib.SMTPException) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
