"""Generate a human-readable Markdown report from the prestige cache."""

from __future__ import annotations

import argparse
from pathlib import Path

from internship_notifier.prestige import CompanyPrestige, PrestigeCache, load_prestige_cache

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
    args = parser.parse_args(argv)
    write_prestige_report(
        args.cache,
        args.output,
        max_count=None if args.all else MAX_REPORT_COMPANIES,
    )


if __name__ == "__main__":
    main()
