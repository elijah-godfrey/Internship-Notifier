"""Command-line entry: poll upstream, filter, diff against state, persist."""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from internship_notifier import email_format, filters, github_listings, smtp_notify
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
    normalize_company_name,
    resolve_minimum_score,
    save_prestige_cache,
)
from internship_notifier.prestige_ranker import (
    OpenAIPrestigeRanker,
    PrestigeRankingError,
    get_or_rank_companies,
    refresh_stale_companies,
)
from internship_notifier.state import NotifierState, load_state, save_state

# Leave room in the 10-minute poll gap for the email and the state commit.
DEFAULT_RANKING_BUDGET_SECONDS = 480
IDLE_PRESTIGE_REFRESH_LIMIT = 1


def _apply_source(
    listings: list[dict[str, Any]],
    source: str,
) -> list[dict[str, Any]]:
    if source == "summer2027":
        return filters.filter_summer(listings)
    if source == "offseason":
        return filters.filter_off_season(listings)
    if source == "all":
        return [
            *filters.filter_summer(listings),
            *filters.filter_off_season(listings),
        ]
    raise ValueError(f"unknown source: {source!r}")


def _merge_filter_options(
    ns: argparse.Namespace,
    file_cfg: NotifierTomlConfig | None,
) -> tuple[str, bool, list[str], list[str]]:
    """Combine TOML defaults with optional CLI overrides.

    CLI flags override the file when supplied: ``--source``, ``--all-categories``,
    or any ``--category`` (including an empty override list, which is invalid).
    ``exclude_categories`` always comes from the TOML file.
    """
    excluded = list(file_cfg.exclude_categories) if file_cfg is not None else []
    if file_cfg is None:
        if ns.source is None:
            raise ValueError(
                "Missing filter settings: add notifier.toml in the current directory, "
                "set NOTIFIER_CONFIG to a TOML path, or pass --source and "
                "--category / --all-categories."
            )
        source = ns.source
        if ns.all_categories:
            return source, True, [], excluded
        cats = list(ns.categories) if ns.categories else []
        if not cats:
            raise ValueError(
                "Pass --category (repeatable) or --all-categories when no notifier.toml is used."
            )
        return source, False, cats, excluded

    source = ns.source if ns.source is not None else file_cfg.source
    if ns.all_categories:
        return source, True, [], excluded
    if ns.categories is not None:
        cats = list(ns.categories)
        if not cats:
            raise ValueError("When using --category, pass at least one category name.")
        return source, False, cats, excluded
    if file_cfg.all_categories:
        return source, True, [], excluded
    return source, False, list(file_cfg.categories), excluded


def _seen_id_strings(listings: list[dict[str, Any]]) -> set[str]:
    """Stable string ids for listings that define ``id`` (skip null/missing)."""
    return {str(L["id"]) for L in listings if L.get("id") is not None}


def _closed_listing_ids(listings: list[dict[str, Any]]) -> set[str]:
    """Ids that are not an open, visible application.

    Simplify keeps closed roles in ``listings.json`` and sometimes flips
    ``active`` back on without changing the posting. Remembering those ids
    keeps a later reopen from being emailed as if it were new.
    """
    closed: set[str] = set()
    for listing in listings:
        listing_id = listing.get("id")
        if listing_id is None:
            continue
        if listing.get("active") and listing.get("is_visible"):
            continue
        closed.add(str(listing_id))
    return closed


def _format_listing_line(listing: dict[str, Any]) -> str:
    company = listing.get("company_name", "?")
    title = listing.get("title", "?")
    cat = listing.get("category", "?")
    url = listing.get("url", "")
    return f"{company} | {title} | {cat} | {url}"


def _company_name(row: dict[str, Any]) -> str:
    """Return a listing's company name, or raise when it is missing."""
    company = row.get("company_name")
    if not isinstance(company, str) or not company.strip():
        raise RuntimeError("new listing is missing a company_name for prestige ranking")
    return company.strip()


def _group_rows_by_company(
    rows: list[dict[str, Any]],
) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    """Group listings by normalized company name, preserving first-seen order."""
    order: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = normalize_company_name(_company_name(row))
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(row)
    return order, groups


def _eligible_rows(
    rows: list[dict[str, Any]],
    cache: PrestigeCache,
    threshold: int,
) -> list[dict[str, Any]]:
    """Keep rows whose cached company score meets ``threshold``."""
    eligible: list[dict[str, Any]] = []
    for row in rows:
        assessment = cache.get(_company_name(row))
        if assessment is None:
            continue
        if meets_prestige_threshold(assessment.prestige_score, threshold):
            eligible.append(row)
    return eligible


def _deliver_notifications(
    notify_rows: list[dict[str, Any]],
    *,
    prestige_config: PrestigeTomlConfig,
    prestige_cache: PrestigeCache,
    prestige_threshold: int | None,
    dry_run: bool,
) -> None:
    """Print eligible listings and email them when SMTP is configured."""
    for row in notify_rows:
        print(_format_listing_line(row))
    if not notify_rows:
        return

    settings = smtp_notify.settings_from_env()
    if not settings:
        return
    email_listings = email_format.sort_notification_listings(
        [
            email_format.NotificationListing.from_listing(
                row,
                prestige_cache.get(_company_name(row)) if prestige_config.enabled else None,
            )
            for row in notify_rows
        ]
    )
    threshold_label = None
    if prestige_threshold is not None:
        threshold_label = (
            f"{prestige_config.benchmark_company} benchmark "
            f"({prestige_threshold}/100)"
            if prestige_config.benchmark_company
            else f"{prestige_threshold}/100"
        )
    prefix = (
        os.environ.get("SMTP_SUBJECT_PREFIX") or smtp_notify.DEFAULT_SUBJECT_PREFIX
    ).strip()
    subject = f"{prefix}: {len(notify_rows)} new listing(s)"
    plain_body = email_format.render_plaintext_email(
        email_listings,
        threshold_label=threshold_label,
    )
    html_body = email_format.render_html_email(
        email_listings,
        threshold_label=threshold_label,
    )
    if dry_run:
        print(
            f"Dry-run: would email {settings.mail_to} "
            f"({len(notify_rows)} listing(s)).",
            file=sys.stderr,
        )
        return
    smtp_notify.send_email(
        subject=subject,
        plain_body=plain_body,
        html_body=html_body,
        settings=settings,
    )
    print(f"Sent email to {settings.mail_to}.", file=sys.stderr)


def _refresh_prestige_cache(
    config: PrestigeTomlConfig,
    cache: PrestigeCache,
    cache_path: Path | None,
    *,
    dry_run: bool,
    limit: int,
) -> None:
    """Refresh a few stale scores on a poll that has nothing new to rank."""
    if not config.enabled or dry_run or limit < 1 or not cache.stale_assessments(limit=limit):
        return
    try:
        ranker = OpenAIPrestigeRanker()
    except RuntimeError as exc:
        print(f"Warning: prestige cache refresh skipped: {exc}", file=sys.stderr)
        return

    result = refresh_stale_companies(
        cache,
        ranker,
        limit=limit,
        on_cache_change=lambda changed_cache: save_prestige_cache(
            changed_cache,
            cache_path,
        ),
    )
    print(
        f"Prestige cache refresh: {result.refreshed} updated, "
        f"{len(result.failures)} failed.",
        file=sys.stderr,
    )
    for failure in result.failures:
        print(f"Warning: prestige refresh failed for {failure}", file=sys.stderr)


def _rank_until_budget(
    order: list[str],
    groups: dict[str, list[dict[str, Any]]],
    cache: PrestigeCache,
    *,
    budget_seconds: float,
    dry_run: bool,
    cache_path: Path | None,
) -> tuple[list[str], list[str]]:
    """Score unknown companies until the budget runs out.

    Returns the companies that now have a score, and the companies left unseen.
    A failed lookup stays in the deferred list so the next poll can retry it.
    """
    if not order or budget_seconds <= 0:
        return [], list(order)
    ranker = OpenAIPrestigeRanker()
    deadline = time.monotonic() + budget_seconds
    ranked: list[str] = []
    deferred: list[str] = []
    for index, key in enumerate(order):
        if time.monotonic() >= deadline:
            deferred.extend(order[index:])
            break
        company = _company_name(groups[key][0])
        try:
            get_or_rank_companies(
                [company],
                cache,
                ranker,
                on_cache_change=(
                    None
                    if dry_run
                    else lambda changed_cache: save_prestige_cache(
                        changed_cache,
                        cache_path,
                    )
                ),
            )
        except PrestigeRankingError as exc:
            print(f"Warning: prestige ranking failed for {company}: {exc}", file=sys.stderr)
            deferred.append(key)
            continue
        ranked.append(key)
    return ranked, deferred


def _ensure_prestige_threshold(
    config: PrestigeTomlConfig,
    cache: PrestigeCache,
    cache_path: Path | None,
    *,
    dry_run: bool,
) -> int | None:
    """Return the prestige cutoff, ranking the benchmark company when it is new."""
    benchmark = config.benchmark_company
    if benchmark is not None and cache.get(benchmark) is None:
        try:
            get_or_rank_companies(
                [benchmark],
                cache,
                OpenAIPrestigeRanker(),
                on_cache_change=(
                    None
                    if dry_run
                    else lambda changed_cache: save_prestige_cache(changed_cache, cache_path)
                ),
            )
        except (PrestigeRankingError, RuntimeError) as exc:
            print(f"Warning: could not rank benchmark company: {exc}", file=sys.stderr)
            return None
    threshold = resolve_minimum_score(config, cache)
    if threshold is None:
        print(
            "Warning: prestige filtering is enabled but has no score threshold",
            file=sys.stderr,
        )
    return threshold


def _finish_poll(
    state: NotifierState,
    state_path: Path | None,
    *,
    new_sha: str,
    decided_rows: list[dict[str, Any]],
    dry_run: bool,
    complete: bool,
    closed_ids: set[str] | None = None,
) -> int:
    """Save decided listing ids. The upstream SHA changes only when none remain.

    Closed ids are remembered on every save. They are not emailed, and they
    do not count as newly decided listings.
    """
    if dry_run:
        return 0
    closed = closed_ids or set()
    newly_closed = closed - state.seen_ids
    state.seen_ids |= _seen_id_strings(decided_rows)
    state.seen_ids |= closed
    if complete:
        state.listings_sha = new_sha
        state.closed_ids_sha = new_sha
    save_state(state, state_path)
    if newly_closed:
        print(
            f"Remembered {len(newly_closed)} closed listing(s) without emailing.",
            file=sys.stderr,
        )
    print(
        f"Saved state ({len(decided_rows)} new id(s), sha={state.listings_sha[:12]}...).",
        file=sys.stderr,
    )
    return 0


def run(argv: list[str] | None = None) -> int:
    """Parse CLI args, run one poll cycle, return process exit code.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        ``0`` on success, ``1`` on usage/runtime errors, ``2`` when the user must
        run ``--bootstrap`` first.
    """
    parser = argparse.ArgumentParser(
        description="Poll SimplifyJobs Summer2027-Internships listings.json, "
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
        choices=("summer2027", "offseason", "all"),
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
    parser.add_argument(
        "--ranking-budget-seconds",
        type=float,
        default=DEFAULT_RANKING_BUDGET_SECONDS,
        help="Stop starting new company lookups after this many seconds "
        "(default: %(default)s). Companies that already have a score are still emailed.",
    )
    ns = parser.parse_args(argv)
    if ns.ranking_budget_seconds < 0:
        print("error: --ranking-budget-seconds must be zero or greater", file=sys.stderr)
        return 1

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
        source, all_categories, categories, exclude_categories = _merge_filter_options(
            ns, file_cfg
        )
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

    meta = github_listings.get_listings_metadata(ref=ns.ref)
    new_sha: str = meta["sha"]

    sha_unchanged = bool(state.listings_sha) and new_sha == state.listings_sha
    closed_recorded = state.closed_ids_sha == new_sha
    if not ns.bootstrap and sha_unchanged and closed_recorded:
        print("No upstream change (listings.json blob sha unchanged).", file=sys.stderr)
        _refresh_prestige_cache(
            prestige_config,
            prestige_cache,
            prestige_cache_path,
            dry_run=ns.dry_run,
            limit=IDLE_PRESTIGE_REFRESH_LIMIT,
        )
        return 0

    raw = github_listings.fetch_listings_json(meta["download_url"])
    closed_ids = _closed_listing_ids(raw)
    if not ns.bootstrap and sha_unchanged:
        print(
            "No upstream change (listings.json blob sha unchanged). "
            f"Remembering {len(closed_ids)} closed listing(s).",
            file=sys.stderr,
        )
        if not ns.dry_run:
            state.seen_ids |= closed_ids
            state.closed_ids_sha = new_sha
            save_state(state, state_path)
        _refresh_prestige_cache(
            prestige_config,
            prestige_cache,
            prestige_cache_path,
            dry_run=ns.dry_run,
            limit=IDLE_PRESTIGE_REFRESH_LIMIT,
        )
        return 0

    filtered = _apply_source(raw, source)
    if not all_categories:
        filtered = filters.filter_by_categories(filtered, set(categories))
    if exclude_categories:
        filtered = filters.filter_excluding_categories(filtered, set(exclude_categories))

    if ns.bootstrap:
        ids = _seen_id_strings(filtered) | closed_ids
        if not ns.dry_run:
            state.seen_ids |= ids
            state.listings_sha = new_sha
            state.closed_ids_sha = new_sha
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
    if not new_rows:
        _refresh_prestige_cache(
            prestige_config,
            prestige_cache,
            prestige_cache_path,
            dry_run=ns.dry_run,
            limit=IDLE_PRESTIGE_REFRESH_LIMIT,
        )
        return _finish_poll(
            state,
            state_path,
            new_sha=new_sha,
            decided_rows=[],
            dry_run=ns.dry_run,
            complete=True,
            closed_ids=closed_ids,
        )

    if not prestige_config.enabled:
        _deliver_notifications(
            new_rows,
            prestige_config=prestige_config,
            prestige_cache=prestige_cache,
            prestige_threshold=None,
            dry_run=ns.dry_run,
        )
        if ns.dry_run:
            print(
                f"Dry-run: {len(new_rows)} new listing(s), {len(new_rows)} eligible "
                "(state and prestige cache not saved).",
                file=sys.stderr,
            )
            return 0
        return _finish_poll(
            state,
            state_path,
            new_sha=new_sha,
            decided_rows=new_rows,
            dry_run=False,
            complete=True,
            closed_ids=closed_ids,
        )

    threshold = _ensure_prestige_threshold(
        prestige_config,
        prestige_cache,
        prestige_cache_path,
        dry_run=ns.dry_run,
    )
    if threshold is None:
        return _finish_poll(
            state,
            state_path,
            new_sha=new_sha,
            decided_rows=[],
            dry_run=ns.dry_run,
            complete=False,
            closed_ids=closed_ids,
        )

    order, groups = _group_rows_by_company(new_rows)
    known = [key for key in order if prestige_cache.get(_company_name(groups[key][0]))]
    unknown = [key for key in order if key not in set(known)]
    known_rows = [row for key in known for row in groups[key]]
    known_eligible = _eligible_rows(known_rows, prestige_cache, threshold)
    _deliver_notifications(
        known_eligible,
        prestige_config=prestige_config,
        prestige_cache=prestige_cache,
        prestige_threshold=threshold,
        dry_run=ns.dry_run,
    )
    if not ns.dry_run and known_rows:
        _finish_poll(
            state,
            state_path,
            new_sha=new_sha,
            decided_rows=known_rows,
            dry_run=False,
            complete=False,
            closed_ids=closed_ids,
        )

    ranked: list[str] = []
    deferred: list[str] = []
    if unknown:
        print(
            f"Prestige ranking: {len(unknown)} uncached company or companies, "
            "one company per request.",
            file=sys.stderr,
        )
        try:
            ranked, deferred = _rank_until_budget(
                unknown,
                groups,
                prestige_cache,
                budget_seconds=ns.ranking_budget_seconds,
                dry_run=ns.dry_run,
                cache_path=prestige_cache_path,
            )
        except RuntimeError as exc:
            print(f"Warning: prestige ranking skipped: {exc}", file=sys.stderr)
            deferred = list(unknown)

    ranked_rows = [row for key in ranked for row in groups[key]]
    ranked_eligible = _eligible_rows(ranked_rows, prestige_cache, threshold)
    _deliver_notifications(
        ranked_eligible,
        prestige_config=prestige_config,
        prestige_cache=prestige_cache,
        prestige_threshold=threshold,
        dry_run=ns.dry_run,
    )
    decided = [*known_rows, *ranked_rows]
    print(
        f"Prestige filter: {len(known_eligible) + len(ranked_eligible)} of "
        f"{len(decided)} decided listing(s) met minimum score {threshold}.",
        file=sys.stderr,
    )
    if deferred:
        print(
            f"Prestige ranking deferred {len(deferred)} company or companies "
            "to the next poll.",
            file=sys.stderr,
        )
    if ns.dry_run:
        print(
            f"Dry-run: {len(new_rows)} new listing(s), "
            f"{len(known_eligible) + len(ranked_eligible)} eligible "
            "(state and prestige cache not saved).",
            file=sys.stderr,
        )
        return 0
    return _finish_poll(
        state,
        state_path,
        new_sha=new_sha,
        decided_rows=ranked_rows,
        dry_run=False,
        complete=not deferred,
        closed_ids=closed_ids,
    )


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
