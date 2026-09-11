"""Unit tests for the generated company prestige Markdown report."""

from __future__ import annotations

from datetime import date

import pytest

from internship_notifier.prestige import CompanyPrestige, PrestigeCache
from internship_notifier.prestige_report import (
    MAX_REPORT_COMPANIES,
    load_report_company_names,
    main,
    render_prestige_report,
    render_scoped_prestige_report,
    write_prestige_report,
    write_scoped_prestige_report,
)


def _assessment(
    company: str,
    score: int,
    *,
    reason: str = "Test reason.",
    aliases: tuple[str, ...] = (),
    manual_override: bool = False,
) -> CompanyPrestige:
    return CompanyPrestige(
        display_name=company,
        prestige_score=score,
        confidence="high",
        reason=reason,
        reviewed_at=date(2026, 7, 17),
        model="test-model",
        aliases=aliases,
        manual_override=manual_override,
    )


class TestRenderPrestigeReport:
    def test_empty_cache_has_helpful_message(self) -> None:
        report = render_prestige_report(PrestigeCache())
        assert "Showing **0** of **0**" in report
        assert "No companies have been ranked yet" in report

    def test_sorts_by_score_then_company_name(self) -> None:
        cache = PrestigeCache()
        cache.put(_assessment("Zulu", 80))
        cache.put(_assessment("Beta", 90))
        cache.put(_assessment("Alpha", 90))

        report = render_prestige_report(cache)

        assert report.index("| Alpha | 90") < report.index("| Beta | 90")
        assert report.index("| Beta | 90") < report.index("| Zulu | 80")
        assert "## Exceptional (90-100)" in report
        assert "## Very prestigious (80-89)" in report

    def test_includes_metadata_and_escapes_table_content(self) -> None:
        cache = PrestigeCache()
        cache.put(
            _assessment(
                "Example | Labs",
                75,
                reason="Line one\nLine | two",
                aliases=("Example\\Labs",),
                manual_override=True,
            )
        )

        report = render_prestige_report(cache)

        assert "Example \\| Labs" in report
        assert "Line one Line \\| two" in report
        assert "Example\\\\Labs" in report
        assert "| Yes |" in report

    def test_report_is_capped_at_500_companies(self) -> None:
        cache = PrestigeCache()
        for index in range(MAX_REPORT_COMPANIES + 1):
            cache.put(_assessment(f"Company {index:03}", 50))

        report = render_prestige_report(cache)

        assert "Showing **500** of **501**" in report
        assert "1 lowest-scored companies" in report
        assert report.count("| Company ") == MAX_REPORT_COMPANIES + 1

    def test_all_report_includes_lowest_scored_companies(self) -> None:
        cache = PrestigeCache()
        for index in range(MAX_REPORT_COMPANIES + 1):
            cache.put(_assessment(f"Company {index:03}", 50))

        report = render_prestige_report(cache, max_count=None)

        assert "Showing **501** of **501** cached companies (all cached companies)." in report
        assert "lowest-scored companies are omitted" not in report

    @pytest.mark.parametrize("max_count", [0, MAX_REPORT_COMPANIES + 1])
    def test_rejects_count_outside_cap(self, max_count: int) -> None:
        with pytest.raises(ValueError, match="max_count"):
            render_prestige_report(PrestigeCache(), max_count=max_count)


class TestWritePrestigeReport:
    def test_writes_report_and_creates_parent_directory(self, tmp_path) -> None:
        output = tmp_path / "docs" / "rankings.md"

        write_prestige_report(tmp_path / "missing-cache.json", output)

        assert output.is_file()
        assert "No companies have been ranked yet" in output.read_text(encoding="utf-8")

    def test_cli_accepts_custom_paths(self, tmp_path) -> None:
        output = tmp_path / "custom" / "rankings.md"

        main(
            [
                "--cache",
                str(tmp_path / "missing-cache.json"),
                "--output",
                str(output),
            ]
        )

        assert output.is_file()

    def test_cli_generates_an_all_companies_report(self, tmp_path) -> None:
        output = tmp_path / "custom" / "all-rankings.md"

        main(
            [
                "--cache",
                str(tmp_path / "missing-cache.json"),
                "--output",
                str(output),
                "--all",
            ]
        )

        assert "all cached companies" in output.read_text(encoding="utf-8")


class TestScopedPrestigeReport:
    def test_reuses_cached_names_and_aliases_without_omitting_unknowns(self) -> None:
        cache = PrestigeCache()
        cache.put(_assessment("Apple", 98, aliases=("Apple Inc",)))
        cache.put(_assessment("Snowflake", 90, aliases=("Snowflake Computing Inc",)))

        report = render_scoped_prestige_report(
            cache,
            ["Snowflake Computing Inc", "New Startup Inc", "Apple Inc", "Apple"],
            title="Winter 2027 WaterlooWorks Companies",
            source_path="data/waterlooworks-winter-2027-companies.txt",
        )

        assert (
            "Showing **3** unique organizations: **2 ranked** and **1 awaiting ranking**"
            in report
        )
        assert report.index("| Apple | 98") < report.index("| Snowflake | 90")
        assert report.count("| Apple | 98") == 1
        assert "- New Startup Inc" in report

    def test_loads_comments_blanks_and_normalized_duplicates_once(self, tmp_path) -> None:
        source = tmp_path / "companies.txt"
        source.write_text(
            "# Winter companies\nApple Inc\n\napple incorporated\nSnowflake\n",
            encoding="utf-8",
        )

        assert load_report_company_names(source) == ["Apple Inc", "Snowflake"]

    def test_writes_scoped_report_and_cli_accepts_company_source(self, tmp_path) -> None:
        cache_path = tmp_path / "cache.json"
        companies_path = tmp_path / "companies.txt"
        direct_output = tmp_path / "direct.md"
        cli_output = tmp_path / "cli.md"
        companies_path.write_text("Unknown Company\n", encoding="utf-8")

        write_scoped_prestige_report(
            cache_path,
            companies_path,
            direct_output,
            title="Direct Report",
        )
        main(
            [
                "--cache",
                str(cache_path),
                "--companies",
                str(companies_path),
                "--output",
                str(cli_output),
                "--title",
                "CLI Report",
            ]
        )

        assert "# Direct Report" in direct_output.read_text(encoding="utf-8")
        assert "# CLI Report" in cli_output.read_text(encoding="utf-8")
        assert "- Unknown Company" in cli_output.read_text(encoding="utf-8")
