"""Generate a human-readable Markdown report from the prestige cache."""

from __future__ import annotations

import argparse
from pathlib import Path

from internship_notifier.prestige import (
    CompanyPrestige,
    PrestigeCache,
    load_prestige_cache,
    normalize_company_name,
)

MAX_REPORT_COMPANIES = 500
DEFAULT_CACHE_PATH = Path(".github/company-prestige-cache.json")
DEFAULT_OUTPUT_PATH = Path("docs/company-prestige-rankings-top-500.md")

SCORE_BANDS: tuple[tuple[int, int, str], ...] = (
    (90, 100, "Exceptional"),
    (80, 89, "Very prestigious"),
    (70, 79, "Strong"),
    (60, 69, "Solid"),
    (40, 59, "Established or specialized"),
    (20, 39, "Limited SWE prestige"),
    (1, 19, "Minimal SWE prestige"),
)


def render_prestige_report(
    cache: PrestigeCache,
    *,
    max_count: int | None = MAX_REPORT_COMPANIES,
) -> str:
    """Render either the top cached companies or every cached company as Markdown.

    Args:
        cache: Prestige assessments to render.
        max_count: Highest-scored entries to include. ``None`` includes every
            cached company; otherwise the value must be from 1 through 500.
    """
    if max_count is not None and not 1 <= max_count <= MAX_REPORT_COMPANIES:
        raise ValueError(
            f"max_count must be between 1 and {MAX_REPORT_COMPANIES}, or None"
        )

    ranked = sorted(
        cache.companies.values(),
        key=lambda assessment: (
            -assessment.prestige_score,
            assessment.display_name.casefold(),
        ),
    )
    shown = ranked if max_count is None else ranked[:max_count]
    omitted = len(ranked) - len(shown)
    report_scope = (
        "all cached companies"
        if max_count is None
        else f"maximum {MAX_REPORT_COMPANIES}"
    )

    lines = [
        "# Company Prestige Rankings",
        "",
        "> This file is generated from `.github/company-prestige-cache.json`. "
        "Do not edit it manually.",
        "",
        "Scores measure software-engineering internship career prestige only: "
        "technical reputation, selectivity, and career signal. They do not include "
        "pay, work-life balance, location, or return-offer likelihood.",
        "",
        f"Showing **{len(shown)}** of **{len(ranked)}** cached companies "
        f"({report_scope}).",
        "",
    ]
    if omitted:
        lines.extend(
            [
                f"_The {omitted} lowest-scored companies are omitted by the report cap._",
                "",
            ]
        )
    if not shown:
        lines.extend(["_No companies have been ranked yet._", ""])
        return "\n".join(lines)

    for minimum, maximum, label in SCORE_BANDS:
        entries = [
            assessment
            for assessment in shown
            if minimum <= assessment.prestige_score <= maximum
        ]
        if not entries:
            continue
        lines.extend(_render_band(label, minimum, maximum, entries))

    return "\n".join(lines)


def load_report_company_names(path: Path) -> list[str]:
    """Load and normalized-deduplicate a one-company-per-line report scope."""
    companies: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        company = raw_line.strip()
        if not company or company.startswith("#"):
            continue
        try:
            key = normalize_company_name(company)
        except ValueError as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
        companies.setdefault(key, company)
    return list(companies.values())


def render_scoped_prestige_report(
    cache: PrestigeCache,
    company_names: list[str],
    *,
    title: str,
    source_path: str,
) -> str:
    """Render cached rankings and unresolved names for one source collection."""
    if not title.strip():
        raise ValueError("title must be non-empty")

    ranked_by_key: dict[str, CompanyPrestige] = {}
    unresolved_by_key: dict[str, str] = {}
    for company_name in company_names:
        source_key = normalize_company_name(company_name)
        assessment = cache.get(company_name)
        if assessment is None:
            unresolved_by_key.setdefault(source_key, company_name.strip())
            continue
        canonical_key = normalize_company_name(assessment.display_name)
        ranked_by_key.setdefault(canonical_key, assessment)

    ranked = sorted(
        ranked_by_key.values(),
        key=lambda assessment: (
            -assessment.prestige_score,
            assessment.display_name.casefold(),
        ),
    )
    unresolved = sorted(unresolved_by_key.values(), key=str.casefold)
    total = len(ranked) + len(unresolved)
    lines = [
        f"# {title.strip()}",
        "",
        f"> This file is generated from `{source_path}` and "
        "`.github/company-prestige-cache.json`. Do not edit it manually.",
        "",
        "Scores measure software-engineering internship career prestige only: "
        "technical reputation, selectivity, and career signal. They do not include "
        "pay, work-life balance, location, role quality, or return-offer likelihood.",
        "",
        f"Showing **{total}** unique organizations: **{len(ranked)} ranked** and "
        f"**{len(unresolved)} awaiting ranking**.",
        "",
    ]
    if not ranked:
        lines.extend(["_No companies in this collection have been ranked yet._", ""])
    else:
        for minimum, maximum, label in SCORE_BANDS:
            entries = [
                assessment
                for assessment in ranked
                if minimum <= assessment.prestige_score <= maximum
            ]
            if entries:
                lines.extend(_render_band(label, minimum, maximum, entries))

    if unresolved:
        lines.extend(
            [
                "## Awaiting ranking",
                "",
                "These organizations are not yet in the prestige cache and are excluded "
                "from the ordered rankings above.",
                "",
                *[f"- {_escape_markdown(company)}" for company in unresolved],
                "",
            ]
        )
    return "\n".join(lines)


def _render_band(
    label: str,
    minimum: int,
    maximum: int,
    entries: list[CompanyPrestige],
) -> list[str]:
    """Render one score band as a Markdown table."""
    lines = [
        f"## {label} ({minimum}-{maximum})",
        "",
        "| Company | Score | Confidence | Reviewed | Model | Manual | Aliases | Reason |",
        "| --- | ---: | --- | --- | --- | :---: | --- | --- |",
    ]
    for assessment in entries:
        aliases = ", ".join(assessment.aliases) or "-"
        manual = "Yes" if assessment.manual_override else "No"
        lines.append(
            "| "
            + " | ".join(
                (
                    _escape_markdown(assessment.display_name),
                    str(assessment.prestige_score),
                    assessment.confidence.title(),
                    assessment.reviewed_at.isoformat(),
                    _escape_markdown(assessment.model),
                    manual,
                    _escape_markdown(aliases),
                    _escape_markdown(assessment.reason),
                )
            )
            + " |"
        )
    lines.append("")
    return lines


def _escape_markdown(value: str) -> str:
    """Keep arbitrary cache text inside a Markdown table cell."""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def write_prestige_report(
    cache_path: Path = DEFAULT_CACHE_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    *,
    max_count: int | None = MAX_REPORT_COMPANIES,
) -> None:
    """Load the cache and write a top-500 or complete Markdown report.

    Args:
        cache_path: Existing prestige-cache JSON file.
        output_path: Markdown report destination.
        max_count: Report cap, or ``None`` to include every cached company.
    """
    report = render_prestige_report(
        load_prestige_cache(cache_path),
        max_count=max_count,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")


def write_scoped_prestige_report(
    cache_path: Path,
    companies_path: Path,
    output_path: Path,
    *,
    title: str,
) -> None:
    """Write a generated prestige report for companies from one source list."""
    report = render_scoped_prestige_report(
        load_prestige_cache(cache_path),
        load_report_company_names(companies_path),
        title=title,
        source_path=companies_path.as_posix(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for report generation."""
    parser = argparse.ArgumentParser(
        description="Generate Markdown documentation from the company prestige cache."
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include every cached company instead of only the highest-scored 500.",
    )
    parser.add_argument(
        "--companies",
        type=Path,
        help="Generate a scoped report from a one-company-per-line source list.",
    )
    parser.add_argument(
        "--title",
        default="Company Prestige Rankings",
        help="Heading for a scoped report. Default: %(default)s",
    )
    args = parser.parse_args(argv)
    if args.companies is not None:
        if args.all:
            parser.error("--all cannot be combined with --companies")
        write_scoped_prestige_report(
            args.cache,
            args.companies,
            args.output,
            title=args.title,
        )
        return
    write_prestige_report(
        args.cache,
        args.output,
        max_count=None if args.all else MAX_REPORT_COMPANIES,
    )


if __name__ == "__main__":
    main()
