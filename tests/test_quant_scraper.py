"""Unit tests for quant-scraper README parsing and apply-URL identity."""

from __future__ import annotations

from internship_notifier.quant_scraper import (
    canonical_apply_key,
    keep_for_source,
    parse_readme,
    url_keys,
)

_README = "\n".join(
    [
        "# Sample",
        "",
        "## Where the roles are",
        "",
        "| Company | Role |",
        "|---|---|",
        "| **[Ignore](https://e.test/i)** | [Skip](https://e.test/r) |",
        "",
        "## Internships",
        "",
        "### Trading — internships",
        "",
        "| Company | Role | Location | Focus | Salary | Posted |",
        "|---|---|---|---|---|---|",
        (
            "| **[Maven Securities](https://q.test/m)** | "
            "[Summer Trader](https://gh.test/jobs/111) | Amsterdam | General | — | Nov 11 |"
        ),
        (
            "| ↳ | [Winter Trader](https://boards.greenhouse.io/m/jobs/222?gh_jid=222) "
            "| London | Quant | — | Nov 11 |"
        ),
        "",
        "### Quantitative research — internships",
        "",
        "<details open>",
        "<summary><b>AI / ML</b> (1)</summary>",
        "",
        "| Company | Role | Location | Salary | Posted |",
        "|---|---|---|---|---|",
        (
            "| **[Jump](https://q.test/j)** | [AI Research Intern](https://e.test/ai) "
            "| Chicago | — | Nov 2 |"
        ),
        "",
        "</details>",
        "",
        "<details open>",
        "<summary><b>Software</b> (1)</summary>",
        "",
        "| Company | Role | Location | Salary | Posted |",
        "|---|---|---|---|---|",
        "| **Tower Research Capital** | [C++ Intern](https://e.test/cpp) | Chicago | — | Nov 3 |",
        "",
        "</details>",
        "",
        "### Software & technology — internships",
        "",
        "<details open>",
        "<summary><b>Quant</b> (1)</summary>",
        "",
        "| Company | Role | Location | Salary | Posted |",
        "|---|---|---|---|---|",
        (
            "| **[Point72](https://q.test/p)** | [Quant Dev Intern](https://e.test/qd) "
            "| New York | — | Nov 1 |"
        ),
        (
            "| ↳ | [Winter SWE Intern](https://boards.greenhouse.io/m/jobs/222?gh_jid=222) "
            "| London | — | Nov 1 |"
        ),
        "",
        "</details>",
        "",
        "### Operations, risk & other — internships",
        "",
        "| Company | Role | Location | Focus | Salary | Posted |",
        "|---|---|---|---|---|---|",
        (
            "| **[Ops Co](https://e.test/o)** | [Ops Intern](https://e.test/ops) "
            "| Chicago | General | — | Nov 1 |"
        ),
        "",
        "## Graduate roles",
        "",
        "### Trading — graduate roles",
        "",
        "| Company | Role | Location | Focus | Salary | Posted |",
        "|---|---|---|---|---|---|",
        (
            "| **[Maven Securities](https://q.test/m)** | "
            "[New Grad](https://gh.test/j/999) | London | Quant | — | Nov 1 |"
        ),
    ]
)


class TestUrlKeys:
    def test_greenhouse_path_and_query_share_a_job_id(self) -> None:
        path_keys = url_keys("https://job-boards.greenhouse.io/acme/jobs/111")
        query_keys = url_keys("https://www.example.com/jobs?gh_jid=111")
        assert path_keys & query_keys == {"greenhouse:111"}

    def test_workday_locale_and_requisition_match(self) -> None:
        quant = url_keys(
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/"
            "job/Switzerland-Zurich/GPU-Compiler-Intern_JR2029779"
        )
        simplify = url_keys(
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/"
            "job/US-CA-Santa-Clara/GPU-Compiler-Intern_JR2029779"
        )
        assert "workday:nvidia.wd5.myworkdayjobs.com:JR2029779" in quant & simplify

    def test_canonical_key_prefers_ats_id(self) -> None:
        keys = url_keys("https://boards.greenhouse.io/acme/jobs/111?gh_jid=111")
        assert canonical_apply_key(keys) == "greenhouse:111"


class TestParseReadme:
    def test_parses_internships_and_skips_other_sections(self) -> None:
        rows = parse_readme(_README, repo="quant-scraper/Quant-Internships-New-Grad-2027")
        assert [(row["company_name"], row["title"], row["category"]) for row in rows] == [
            ("Tower Research Capital", "C++ Intern", "Software Engineering"),
            ("Point72", "Quant Dev Intern", "Software Engineering"),
            ("Point72", "Winter SWE Intern", "Software Engineering"),
        ]
        assert rows[0]["locations"] == ["Chicago"]
        assert rows[2]["season"] == "offseason"
        assert rows[2]["id"] == "qs:greenhouse:222"

    def test_source_filter_uses_season(self) -> None:
        rows = parse_readme(_README, repo="quant-scraper/example")
        summer = [row["title"] for row in rows if keep_for_source(row, "summer2027")]
        offseason = [row["title"] for row in rows if keep_for_source(row, "offseason")]
        assert summer == ["C++ Intern", "Quant Dev Intern"]
        assert offseason == ["Winter SWE Intern"]
        assert [row["title"] for row in rows if keep_for_source(row, "all")] == [
            "C++ Intern",
            "Quant Dev Intern",
            "Winter SWE Intern",
        ]
