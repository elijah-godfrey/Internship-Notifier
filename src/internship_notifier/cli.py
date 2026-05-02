"""Command-line entry: poll upstream, filter, diff against state, persist."""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from internship_notifier import filters
from internship_notifier import github_listings
from internship_notifier import smtp_notify
from internship_notifier.config_toml import (
    NotifierTomlConfig,
    load_notifier_toml,
    resolve_config_path,
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

    settings = smtp_notify.settings_from_env()
    if new_rows and settings:
        prefix = (os.environ.get("SMTP_SUBJECT_PREFIX") or "Internship notifier").strip()
        subject = f"{prefix}: {len(new_rows)} new listing(s)"
        body = "\n".join(_format_listing_line(r) for r in new_rows) + "\n"
        if ns.dry_run:
            print(
                f"Dry-run: would email {settings.mail_to} ({len(new_rows)} listing(s)).",
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
    load_dotenv(override=False)
    alt = (os.environ.get("DOTENV_PATH") or "").strip()
    if alt:
        load_dotenv(dotenv_path=Path(alt), override=False)
    try:
        raise SystemExit(run())
    except (RuntimeError, ValueError, OSError, smtplib.SMTPException) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e
