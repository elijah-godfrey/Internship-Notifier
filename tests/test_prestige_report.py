"""Unit tests for the generated company prestige Markdown report."""

from __future__ import annotations

from datetime import date

import pytest

from internship_notifier.prestige import CompanyPrestige, PrestigeCache
from internship_notifier.prestige_report import (
    MAX_REPORT_COMPANIES,
    main,
    render_prestige_report,
    write_prestige_report,
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
