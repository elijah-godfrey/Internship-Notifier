"""Unit tests for internship_notifier.cli.run (mocked GitHub + state)."""

from __future__ import annotations

import json
import textwrap
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from internship_notifier.cli import _apply_source, run
from internship_notifier.prestige import (
    CompanyPrestige,
    PrestigeCache,
    load_prestige_cache,
    save_prestige_cache,
)
from internship_notifier.prestige_ranker import PrestigeRankingError
from internship_notifier.quant_scraper import QuantScraperPoll
from internship_notifier.state import NotifierState, load_state


def _offseason_row(**overrides: object) -> dict:
    row = {
        "id": "100",
        "is_visible": True,
        "active": True,
        "terms": ["Fall 2025"],
        "category": "Software Engineering",
        "company_name": "Acme",
        "title": "Intern",
        "url": "https://jobs.example/apply",
    }
    row.update(overrides)
    return row


def _write_notifier(
    tmp: Path,
    *,
    all_categories: bool = True,
    minimum_score: int | None = None,
    benchmark_company: str | None = None,
    exclude_categories: list[str] | None = None,
    countries: list[str] | None = None,
) -> Path:
    path = tmp / "notifier.toml"
    ac = "true" if all_categories else "false"
    cats = "" if all_categories else 'categories = ["Software Engineering"]'
    excluded = ""
    if exclude_categories:
        names = ", ".join(f'"{name}"' for name in exclude_categories)
        excluded = f"exclude_categories = [{names}]"
    if countries:
        names = ", ".join(f'"{name}"' for name in countries)
        excluded = f"{excluded}\ncountries = [{names}]"
    prestige = ""
    if minimum_score is not None:
        prestige = f"[prestige]\nminimum_score = {minimum_score}"
    elif benchmark_company is not None:
        prestige = (
            "[prestige]\n"
            f'benchmark_company = "{benchmark_company}"'
        )
    path.write_text(
        textwrap.dedent(
            f"""
            source = "offseason"
            all_categories = {ac}
            {cats}
            {excluded}
            {prestige}
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return path


def _write_state(path: Path, state: NotifierState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_json_dict(), indent=2) + "\n", encoding="utf-8")


def _prestige_assessment(
    company: str,
    score: int,
    *,
    aliases: tuple[str, ...] = (),
    reviewed_at: date = date(2026, 7, 17),
    manual_override: bool = False,
) -> CompanyPrestige:
    return CompanyPrestige(
        display_name=company,
        prestige_score=score,
        confidence="high",
        reason="Test prestige assessment.",
        reviewed_at=reviewed_at,
        model="test-model",
        aliases=aliases,
        manual_override=manual_override,
    )


def _write_prestige_cache(path: Path, *assessments: CompanyPrestige) -> None:
    cache = PrestigeCache()
    for assessment in assessments:
        cache.put(assessment)
    save_prestige_cache(cache, path)


@pytest.fixture(autouse=True)
def _quiet_quant_scraper() -> object:
    """Keep existing poll tests off the quant-scraper network."""
    snapshot = QuantScraperPoll(shas={}, listings=[], changed=False)
    with patch("internship_notifier.cli.fetch_quant_scraper", return_value=snapshot):
        yield


@pytest.fixture
def clear_smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_FROM",
        "SMTP_TO",
        "SMTP_QUANT_TO",
        "SMTP_USER",
        "SMTP_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)


class TestApplySource:
    def test_all_combines_summer_and_offseason(self) -> None:
        summer = {
            "id": "summer",
            "is_visible": True,
            "active": True,
            "terms": ["Summer 2027"],
            "date_posted": 9_999_999_999,
            "company_url": "https://example.com",
        }
        offseason = {
            "id": "offseason",
            "is_visible": True,
            "active": True,
            "terms": ["Fall 2026"],
        }
        unrelated = {
            "id": "unrelated",
            "is_visible": True,
            "active": True,
            "terms": ["Co-op"],
        }

        result = _apply_source([summer, offseason, unrelated], "all")

        assert [row["id"] for row in result] == ["summer", "offseason"]


class TestRunShaShortCircuit:
    def test_skips_fetch_when_sha_unchanged(self, tmp_path, clear_smtp_env) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="same-sha",
                seen_ids={"1"},
                closed_ids_sha="same-sha",
            ),
        )
        meta = {"sha": "same-sha", "size": 1, "download_url": "https://example.com/raw"}

        fetch = MagicMock()
        with (
            patch("internship_notifier.github_listings.get_listings_metadata", return_value=meta),
            patch("internship_notifier.github_listings.fetch_listings_json", fetch),
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                ]
            )
        assert code == 0
        fetch.assert_not_called()

    def test_refreshes_stale_prestige_cache_before_sha_short_circuit(
        self, tmp_path, clear_smtp_env
    ) -> None:
        cfg = _write_notifier(tmp_path, minimum_score=75)
        state_path = tmp_path / "state.json"
        cache_path = tmp_path / "company-prestige-cache.json"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="same-sha",
                seen_ids={"1"},
                closed_ids_sha="same-sha",
            ),
        )
        _write_prestige_cache(
            cache_path,
            _prestige_assessment(
                "Acme",
                70,
                reviewed_at=date(2025, 1, 1),
            ),
        )
        meta = {"sha": "same-sha", "size": 1, "download_url": "https://example.com/raw"}
        refreshed = _prestige_assessment("Acme", 80)
        ranker = MagicMock()
        ranker.rank_company.return_value = refreshed
        ranker.rank_companies.return_value = [refreshed]
        fetch = MagicMock()

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch("internship_notifier.github_listings.fetch_listings_json", fetch),
            patch(
                "internship_notifier.cli.OpenAIPrestigeRanker",
                return_value=ranker,
            ),
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                ]
            )

        assert code == 0
        fetch.assert_not_called()
        cached = load_prestige_cache(cache_path).get("Acme")
        assert cached is not None
        assert cached.prestige_score == 80


class TestRunBootstrap:
    def test_bootstrap_merges_ids_and_sha(self, tmp_path, clear_smtp_env) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(state_path, NotifierState(listings_sha="", seen_ids=set()))
        listing = _offseason_row(id="7")
        meta = {"sha": "blobsha", "size": 2, "download_url": "https://example.com/raw"}

        with patch("internship_notifier.github_listings.get_listings_metadata", return_value=meta):
            with patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[listing],
            ):
                code = run(
                    [
                        "--config",
                        str(cfg),
                        "--state-path",
                        str(state_path),
                        "--bootstrap",
                    ]
                )
        assert code == 0
        loaded = load_state(state_path)
        assert loaded.listings_sha == "blobsha"
        assert loaded.closed_ids_sha == "blobsha"
        assert "7" in loaded.seen_ids


class TestClosedListings:
    def test_unchanged_blob_remembers_closed_ids_without_email(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(listings_sha="same-sha", seen_ids={"1"}),
        )
        closed = _offseason_row(id="closed", company_name="Goldman Sachs", active=False)
        meta = {"sha": "same-sha", "size": 1, "download_url": "https://example.com/raw"}

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row(id="1"), closed],
            ),
        ):
            code = run(
                ["--config", str(cfg), "--state-path", str(state_path)]
            )

        assert code == 0
        assert "Goldman Sachs" not in capsys.readouterr().out
        loaded = load_state(state_path)
        assert "closed" in loaded.seen_ids
        assert loaded.closed_ids_sha == "same-sha"
        assert loaded.listings_sha == "same-sha"

    def test_reopened_listing_is_not_emailed(self, tmp_path, clear_smtp_env, capsys) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="old-sha",
                seen_ids={"1", "closed"},
                closed_ids_sha="old-sha",
            ),
        )
        reopened = _offseason_row(id="closed", company_name="Goldman Sachs", active=True)
        meta = {"sha": "fresh-sha", "size": 2, "download_url": "https://example.com/raw"}

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row(id="1"), reopened],
            ),
        ):
            code = run(
                ["--config", str(cfg), "--state-path", str(state_path)]
            )

        assert code == 0
        assert "Goldman Sachs" not in capsys.readouterr().out
        loaded = load_state(state_path)
        assert loaded.listings_sha == "fresh-sha"
        assert "closed" in loaded.seen_ids

    def test_new_active_listing_is_still_emailed(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(listings_sha="old-sha", seen_ids={"1"}, closed_ids_sha="old-sha"),
        )
        rows = [
            _offseason_row(id="1"),
            _offseason_row(id="closed", company_name="Old Co", active=False),
            _offseason_row(id="new", company_name="Qualcomm"),
        ]
        meta = {"sha": "fresh-sha", "size": 3, "download_url": "https://example.com/raw"}

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=rows,
            ),
        ):
            code = run(
                ["--config", str(cfg), "--state-path", str(state_path)]
            )

        assert code == 0
        output = capsys.readouterr().out
        assert "Qualcomm" in output
        assert "Old Co" not in output
        loaded = load_state(state_path)
        assert loaded.seen_ids >= {"1", "closed", "new"}
        assert loaded.closed_ids_sha == "fresh-sha"


class TestRunBootstrapFirst:
    def test_requires_bootstrap_when_seen_empty(self, tmp_path, clear_smtp_env) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(state_path, NotifierState(listings_sha="", seen_ids=set()))
        meta = {"sha": "new", "size": 1, "download_url": "https://example.com/raw"}

        with patch("internship_notifier.github_listings.get_listings_metadata", return_value=meta):
            with patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row()],
            ):
                code = run(
                    [
                        "--config",
                        str(cfg),
                        "--state-path",
                        str(state_path),
                    ]
                )
        assert code == 2


class TestRunNewRows:
    def test_prints_new_row_and_updates_state(self, tmp_path, clear_smtp_env, capsys) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(listings_sha="old-sha", seen_ids={"1"}),
        )
        rows = [
            _offseason_row(id="1"),
            _offseason_row(id="2", company_name="Beta"),
        ]
        meta = {"sha": "fresh-sha", "size": 3, "download_url": "https://example.com/raw"}

        with patch("internship_notifier.github_listings.get_listings_metadata", return_value=meta):
            with patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=rows,
            ):
                code = run(
                    [
                        "--config",
                        str(cfg),
                        "--state-path",
                        str(state_path),
                    ]
                )
        assert code == 0
        out = capsys.readouterr().out.strip()
        assert "Beta" in out and "https://jobs.example/apply" in out
        loaded = load_state(state_path)
        assert loaded.listings_sha == "fresh-sha"
        assert loaded.seen_ids >= {"1", "2"}

    def test_excluded_category_is_not_notified_or_marked_seen(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path, exclude_categories=["Hardware Engineering"])
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(listings_sha="old-sha", seen_ids={"1"}),
        )
        rows = [
            _offseason_row(id="2", company_name="Beta"),
            _offseason_row(
                id="3",
                company_name="ChipCo",
                category="Hardware Engineering",
            ),
            _offseason_row(
                id="4",
                company_name="ChipShort",
                category="Hardware",
            ),
        ]
        meta = {"sha": "fresh-sha", "size": 3, "download_url": "https://example.com/raw"}

        with patch("internship_notifier.github_listings.get_listings_metadata", return_value=meta):
            with patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=rows,
            ):
                code = run(
                    [
                        "--config",
                        str(cfg),
                        "--state-path",
                        str(state_path),
                    ]
                )
        assert code == 0
        out = capsys.readouterr().out
        assert "Beta" in out
        assert "ChipCo" not in out
        assert "ChipShort" not in out
        loaded = load_state(state_path)
        assert "2" in loaded.seen_ids
        assert "3" not in loaded.seen_ids
        assert "4" not in loaded.seen_ids

    def test_country_filter_drops_other_countries(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(
            tmp_path,
            countries=["United States", "Canada", "United Kingdom"],
        )
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="old-sha",
                seen_ids={"1"},
                quant_scraper_bootstrapped=True,
            ),
        )
        domestic = _quant_row(
            company="Domestic",
            url="https://jobs.example/us",
            listing_id="qs:url:jobs.example/us",
            keys=["url:jobs.example/us"],
        )
        domestic["countries"] = ["US"]
        foreign = _quant_row(
            company="ForeignDesk",
            url="https://jobs.example/sg",
            listing_id="qs:url:jobs.example/sg",
            keys=["url:jobs.example/sg"],
        )
        foreign["countries"] = ["SG"]
        foreign["locations"] = ["Singapore"]
        unlabeled = _quant_row(
            company="Unlabeled",
            url="https://jobs.example/missing",
            listing_id="qs:url:jobs.example/missing",
            keys=["url:jobs.example/missing"],
        )
        unlabeled["countries"] = []
        unlabeled["locations"] = ["New York"]
        rows = [
            _offseason_row(id="1", locations=["New York, NY"]),
            _offseason_row(id="2", company_name="Beta", locations=["London, UK"]),
            _offseason_row(id="3", company_name="Abroad", locations=["Singapore"]),
        ]
        meta = {"sha": "fresh-sha", "size": 3, "download_url": "https://example.com/raw"}
        snapshot = QuantScraperPoll(
            shas={"quant-scraper/Quant-Internships-New-Grad-2027": "readme-sha"},
            listings=[domestic, foreign, unlabeled],
            changed=True,
        )

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=rows,
            ),
            patch("internship_notifier.cli.fetch_quant_scraper", return_value=snapshot),
        ):
            code = run(["--config", str(cfg), "--state-path", str(state_path)])

        assert code == 0
        output = capsys.readouterr().out
        assert "Beta" in output
        assert "Domestic" in output
        assert "Abroad" not in output
        assert "ForeignDesk" not in output
        assert "Unlabeled" not in output
        loaded = load_state(state_path)
        assert loaded.seen_ids >= {"1", "2", "qs:url:jobs.example/us"}
        assert "3" not in loaded.seen_ids
        assert "qs:url:jobs.example/sg" not in loaded.seen_ids
        assert "qs:url:jobs.example/missing" not in loaded.seen_ids

    def test_sends_email_when_smtp_configured(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(listings_sha="a", seen_ids={"9"}),
        )
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "from@example.com")
        monkeypatch.setenv("SMTP_TO", "to@example.com")

        meta = {"sha": "b", "size": 1, "download_url": "https://example.com/raw"}
        new_row = _offseason_row(id="50")

        with patch("internship_notifier.github_listings.get_listings_metadata", return_value=meta):
            with patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[new_row],
            ):
                with patch("internship_notifier.smtp_notify.send_email") as send_mail:
                    code = run(
                        [
                            "--config",
                            str(cfg),
                            "--state-path",
                            str(state_path),
                        ]
                    )
        assert code == 0
        send_mail.assert_called_once()
        kwargs = send_mail.call_args.kwargs
        assert "1 new listing(s)" in kwargs["subject"]
        assert "Acme" in kwargs["plain_body"]
        assert "https://jobs.example/apply" in kwargs["plain_body"]
        assert "Acme" in kwargs["html_body"]
        err = capsys.readouterr().err
        assert "Sent email to to@example.com." in err

    def test_quant_scraper_mail_goes_to_the_beta_recipient(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="old-sha",
                seen_ids={"1"},
                quant_scraper_bootstrapped=True,
            ),
        )
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "from@example.com")
        monkeypatch.setenv("SMTP_TO", "group@example.com")
        monkeypatch.setenv("SMTP_QUANT_TO", "me@example.com")
        quant = _quant_row(
            company="Domestic",
            url="https://jobs.example/us",
            listing_id="qs:url:jobs.example/us",
            keys=["url:jobs.example/us"],
        )
        meta = {"sha": "fresh-sha", "size": 1, "download_url": "https://example.com/raw"}
        snapshot = QuantScraperPoll(
            shas={"quant-scraper/Quant-Internships-New-Grad-2027": "readme-sha"},
            listings=[quant],
            changed=True,
        )

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row(id="2", company_name="Beta")],
            ),
            patch("internship_notifier.cli.fetch_quant_scraper", return_value=snapshot),
            patch("internship_notifier.smtp_notify.send_email") as send_mail,
        ):
            code = run(["--config", str(cfg), "--state-path", str(state_path)])

        assert code == 0
        assert send_mail.call_count == 2
        group, beta = send_mail.call_args_list
        assert group.kwargs["settings"].mail_to == "group@example.com"
        assert "Beta" in group.kwargs["plain_body"]
        assert "Domestic" in group.kwargs["plain_body"]
        assert "2 new listing(s)" in group.kwargs["subject"]
        assert beta.kwargs["settings"].mail_to == "me@example.com"
        assert "Domestic" in beta.kwargs["plain_body"]
        assert "Beta" not in beta.kwargs["plain_body"]
        assert "1 new quant-scraper listing(s)" in beta.kwargs["subject"]
        loaded = load_state(state_path)
        assert "2" in loaded.seen_ids
        assert "qs:url:jobs.example/us" in loaded.seen_ids
        err = capsys.readouterr().err
        assert "Sent email to group@example.com." in err
        assert "Sent email to me@example.com." in err

    def test_quant_preview_includes_a_role_simplify_is_also_sending(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="old-sha",
                seen_ids={"1"},
                quant_scraper_bootstrapped=True,
            ),
        )
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "from@example.com")
        monkeypatch.setenv("SMTP_TO", "group@example.com")
        monkeypatch.setenv("SMTP_QUANT_TO", "me@example.com")
        shared_url = "https://boards.greenhouse.io/acme/jobs/111"
        quant = _quant_row(
            company="Acme Quant",
            url=shared_url + "?gh_jid=111",
            listing_id="qs:greenhouse:111",
            keys=["greenhouse:111"],
        )
        meta = {"sha": "fresh-sha", "size": 1, "download_url": "https://example.com/raw"}
        snapshot = QuantScraperPoll(
            shas={"quant-scraper/Quant-Internships-New-Grad-2027": "readme-sha"},
            listings=[quant],
            changed=True,
        )

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[
                    _offseason_row(id="2", company_name="Acme", url=shared_url),
                ],
            ),
            patch("internship_notifier.cli.fetch_quant_scraper", return_value=snapshot),
            patch("internship_notifier.smtp_notify.send_email") as send_mail,
        ):
            code = run(["--config", str(cfg), "--state-path", str(state_path)])

        assert code == 0
        group, beta = send_mail.call_args_list
        assert "Acme" in group.kwargs["plain_body"]
        assert "Acme Quant" not in group.kwargs["plain_body"]
        assert "1 new listing(s)" in group.kwargs["subject"]
        assert "Acme Quant" in beta.kwargs["plain_body"]
        assert beta.kwargs["settings"].mail_to == "me@example.com"
        loaded = load_state(state_path)
        assert "2" in loaded.seen_ids
        assert "qs:greenhouse:111" in loaded.seen_ids
        assert "greenhouse:111" not in loaded.seen_apply_keys

    def test_dry_run_does_not_save_or_send(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(listings_sha="a", seen_ids={"9"}),
        )
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "from@example.com")
        monkeypatch.setenv("SMTP_TO", "to@example.com")

        meta = {"sha": "b", "size": 1, "download_url": "https://example.com/raw"}
        new_row = _offseason_row(id="50")

        with patch("internship_notifier.github_listings.get_listings_metadata", return_value=meta):
            with patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[new_row],
            ):
                with patch("internship_notifier.cli.save_state") as save_mock:
                    with patch("internship_notifier.smtp_notify.send_email") as send_mail:
                        code = run(
                            [
                                "--config",
                                str(cfg),
                                "--state-path",
                                str(state_path),
                                "--dry-run",
                            ]
                        )
        assert code == 0
        save_mock.assert_not_called()
        send_mail.assert_not_called()
        loaded = load_state(state_path)
        assert "50" not in loaded.seen_ids


class TestRunPrestigeFiltering:
    def test_cached_scores_gate_output_but_all_new_rows_become_seen(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path, minimum_score=75)
        state_path = tmp_path / "state.json"
        cache_path = tmp_path / "company-prestige-cache.json"
        _write_state(
            state_path,
            NotifierState(listings_sha="old", seen_ids={"existing"}),
        )
        _write_prestige_cache(
            cache_path,
            _prestige_assessment("Acme", 82),
            _prestige_assessment("Beta", 70),
        )
        rows = [
            _offseason_row(id="1", company_name="Acme"),
            _offseason_row(id="2", company_name="Beta"),
        ]
        meta = {"sha": "new", "size": 2, "download_url": "https://example.com/raw"}

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=rows,
            ),
            patch("internship_notifier.cli.OpenAIPrestigeRanker") as ranker_class,
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                ]
            )

        assert code == 0
        ranker_class.assert_not_called()
        captured = capsys.readouterr()
        assert "Acme" in captured.out
        assert "Beta" not in captured.out
        assert "1 of 2" in captured.err
        assert load_state(state_path).seen_ids >= {"1", "2"}

    def test_unknown_company_is_ranked_and_cache_is_saved(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path, minimum_score=75)
        state_path = tmp_path / "state.json"
        cache_path = tmp_path / "company-prestige-cache.json"
        _write_state(state_path, NotifierState(listings_sha="old", seen_ids={"old"}))
        meta = {"sha": "new", "size": 1, "download_url": "https://example.com/raw"}
        ranker = MagicMock()
        ranker.rank_company.return_value = _prestige_assessment("Acme", 85)
        ranker.rank_companies.return_value = [ranker.rank_company.return_value]

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row(id="new", company_name="Acme")],
            ),
            patch(
                "internship_notifier.cli.OpenAIPrestigeRanker",
                return_value=ranker,
            ),
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                ]
            )

        assert code == 0
        ranker.rank_companies.assert_called_once_with(
            ["Acme"],
            reviewed_at=None,
        )
        assert load_prestige_cache(cache_path).get("Acme") is not None
        assert "Acme" in capsys.readouterr().out

    def test_cached_benchmark_company_sets_threshold(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path, benchmark_company="Microsoft")
        state_path = tmp_path / "state.json"
        cache_path = tmp_path / "company-prestige-cache.json"
        _write_state(state_path, NotifierState(listings_sha="old", seen_ids={"old"}))
        _write_prestige_cache(
            cache_path,
            _prestige_assessment("Microsoft", 88),
            _prestige_assessment("Acme", 90),
            _prestige_assessment("Beta", 87),
        )
        rows = [
            _offseason_row(id="1", company_name="Acme"),
            _offseason_row(id="2", company_name="Beta"),
        ]
        meta = {"sha": "new", "size": 2, "download_url": "https://example.com/raw"}

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=rows,
            ),
            patch("internship_notifier.cli.OpenAIPrestigeRanker") as ranker_class,
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                ]
            )

        assert code == 0
        ranker_class.assert_not_called()
        output = capsys.readouterr().out
        assert "Acme" in output
        assert "Beta" not in output

    def test_ranking_failure_does_not_mark_listing_seen(
        self, tmp_path, clear_smtp_env
    ) -> None:
        cfg = _write_notifier(tmp_path, minimum_score=75)
        state_path = tmp_path / "state.json"
        cache_path = tmp_path / "company-prestige-cache.json"
        original = NotifierState(listings_sha="old", seen_ids={"old"})
        _write_state(state_path, original)
        meta = {"sha": "new", "size": 1, "download_url": "https://example.com/raw"}
        ranker = MagicMock()
        ranker.rank_company.side_effect = PrestigeRankingError("API unavailable")
        ranker.rank_companies.side_effect = PrestigeRankingError("API unavailable")

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row(id="new", company_name="Acme")],
            ),
            patch(
                "internship_notifier.cli.OpenAIPrestigeRanker",
                return_value=ranker,
            ),
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                ]
            )

        assert code == 0
        assert load_state(state_path) == original
        assert not cache_path.exists()

    def test_known_company_is_emailed_while_an_unscored_one_waits(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path, minimum_score=75)
        state_path = tmp_path / "state.json"
        cache_path = tmp_path / "company-prestige-cache.json"
        _write_state(state_path, NotifierState(listings_sha="old", seen_ids={"old"}))
        _write_prestige_cache(cache_path, _prestige_assessment("Acme", 90))
        rows = [
            _offseason_row(id="1", company_name="Acme"),
            _offseason_row(id="2", company_name="NewCo"),
        ]
        meta = {"sha": "new", "size": 2, "download_url": "https://example.com/raw"}

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=rows,
            ),
            patch("internship_notifier.cli.OpenAIPrestigeRanker") as ranker_class,
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                    "--ranking-budget-seconds",
                    "0",
                ]
            )

        assert code == 0
        ranker_class.assert_not_called()
        output = capsys.readouterr().out
        assert "Acme" in output
        assert "NewCo" not in output
        loaded = load_state(state_path)
        assert "1" in loaded.seen_ids
        assert "2" not in loaded.seen_ids
        assert loaded.listings_sha == "old"

    def test_failed_lookup_does_not_block_the_next_company(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path, minimum_score=75)
        state_path = tmp_path / "state.json"
        cache_path = tmp_path / "company-prestige-cache.json"
        _write_state(state_path, NotifierState(listings_sha="old", seen_ids={"old"}))
        meta = {"sha": "new", "size": 2, "download_url": "https://example.com/raw"}
        ranker = MagicMock()

        def rank_companies(names: list[str], reviewed_at: object = None) -> list:
            if names == ["Acme"]:
                raise PrestigeRankingError("API unavailable")
            return [_prestige_assessment(names[0], 85)]

        ranker.rank_companies.side_effect = rank_companies
        rows = [
            _offseason_row(id="1", company_name="Acme"),
            _offseason_row(id="2", company_name="Beta"),
        ]

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=rows,
            ),
            patch(
                "internship_notifier.cli.OpenAIPrestigeRanker",
                return_value=ranker,
            ),
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                ]
            )

        assert code == 0
        assert "Beta" in capsys.readouterr().out
        loaded = load_state(state_path)
        assert "1" not in loaded.seen_ids
        assert "2" in loaded.seen_ids
        assert loaded.listings_sha == "old"
        assert load_prestige_cache(cache_path).get("Beta") is not None

    def test_idle_poll_refreshes_only_one_stale_company(
        self, tmp_path, clear_smtp_env
    ) -> None:
        cfg = _write_notifier(tmp_path, minimum_score=75)
        state_path = tmp_path / "state.json"
        cache_path = tmp_path / "company-prestige-cache.json"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="same-sha",
                seen_ids={"1"},
                closed_ids_sha="same-sha",
            ),
        )
        _write_prestige_cache(
            cache_path,
            _prestige_assessment("Older", 10, reviewed_at=date(2024, 1, 1)),
            _prestige_assessment("Newer", 10, reviewed_at=date(2025, 6, 1)),
        )
        meta = {"sha": "same-sha", "size": 1, "download_url": "https://example.com/raw"}
        ranker = MagicMock()

        def rank_companies(names: list[str], reviewed_at: object = None) -> list:
            assert names == ["Older"]
            return [_prestige_assessment("Older", 80)]

        ranker.rank_companies.side_effect = rank_companies

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch("internship_notifier.github_listings.fetch_listings_json") as fetch,
            patch(
                "internship_notifier.cli.OpenAIPrestigeRanker",
                return_value=ranker,
            ),
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                ]
            )

        assert code == 0
        fetch.assert_not_called()
        cache = load_prestige_cache(cache_path)
        assert cache.get("Older") is not None
        assert cache.get("Older").prestige_score == 80
        assert cache.get("Newer") is not None
        assert cache.get("Newer").prestige_score == 10

    def test_only_eligible_rows_are_emailed(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _write_notifier(tmp_path, minimum_score=75)
        state_path = tmp_path / "state.json"
        cache_path = tmp_path / "company-prestige-cache.json"
        _write_state(state_path, NotifierState(listings_sha="old", seen_ids={"old"}))
        _write_prestige_cache(
            cache_path,
            _prestige_assessment("Acme", 82),
            _prestige_assessment("Beta", 70),
        )
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "from@example.com")
        monkeypatch.setenv("SMTP_TO", "to@example.com")
        rows = [
            _offseason_row(id="1", company_name="Acme"),
            _offseason_row(id="2", company_name="Beta"),
        ]
        meta = {"sha": "new", "size": 2, "download_url": "https://example.com/raw"}

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=rows,
            ),
            patch(
                "internship_notifier.smtp_notify.send_email"
            ) as send_mail,
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                ]
            )

        assert code == 0
        send_mail.assert_called_once()
        message = send_mail.call_args.kwargs
        assert "Acme" in message["plain_body"]
        assert "Beta" not in message["plain_body"]
        assert "Acme" in message["html_body"]
        assert "Beta" not in message["html_body"]
        assert "1 new listing(s)" in message["subject"]

    def test_dry_run_does_not_save_new_prestige_assessment(
        self, tmp_path, clear_smtp_env
    ) -> None:
        cfg = _write_notifier(tmp_path, minimum_score=75)
        state_path = tmp_path / "state.json"
        cache_path = tmp_path / "company-prestige-cache.json"
        _write_state(state_path, NotifierState(listings_sha="old", seen_ids={"old"}))
        meta = {"sha": "new", "size": 1, "download_url": "https://example.com/raw"}
        ranker = MagicMock()
        ranker.rank_company.return_value = _prestige_assessment("Acme", 85)
        ranker.rank_companies.return_value = [ranker.rank_company.return_value]

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row(id="new", company_name="Acme")],
            ),
            patch(
                "internship_notifier.cli.OpenAIPrestigeRanker",
                return_value=ranker,
            ),
        ):
            code = run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                    "--dry-run",
                ]
            )

        assert code == 0
        assert not cache_path.exists()
        assert "new" not in load_state(state_path).seen_ids


def _quant_row(
    *,
    company: str,
    url: str,
    listing_id: str,
    keys: list[str],
) -> dict:
    return {
        "id": listing_id,
        "apply_keys": keys,
        "company_name": company,
        "title": "Winter Intern",
        "category": "Software Engineering",
        "url": url,
        "locations": ["New York"],
        "terms": ["Off-season"],
        "season": "offseason",
        "listing_kind": "internship",
        "active": True,
        "is_visible": True,
        "date_posted": 0,
        "company_url": "",
        "source": "quant-scraper/Quant-Internships-New-Grad-2027",
    }


class TestQuantScraperPoll:
    def test_skips_role_already_listed_in_simplify(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="old-sha",
                seen_ids={"1"},
                closed_ids_sha="old-sha",
                quant_scraper_bootstrapped=True,
            ),
        )
        simplify_url = "https://job-boards.greenhouse.io/acme/jobs/111"
        overlap_url = "https://boards.greenhouse.io/acme/jobs/111?gh_jid=111"
        new_url = "https://jobs.ashbyhq.com/newco/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        rows = [
            _offseason_row(id="1", url=simplify_url),
            _quant_row(
                company="Acme",
                url=overlap_url,
                listing_id="qs:greenhouse:111",
                keys=["greenhouse:111", "url:boards.greenhouse.io/acme/jobs/111"],
            ),
            _quant_row(
                company="NewCo",
                url=new_url,
                listing_id="qs:ashby:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                keys=["ashby:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"],
            ),
        ]
        meta = {"sha": "fresh-sha", "size": 1, "download_url": "https://example.com/raw"}
        snapshot = QuantScraperPoll(
            shas={"quant-scraper/Quant-Internships-New-Grad-2027": "readme-sha"},
            listings=rows[1:],
            changed=True,
        )

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[rows[0]],
            ),
            patch("internship_notifier.cli.fetch_quant_scraper", return_value=snapshot),
        ):
            code = run(["--config", str(cfg), "--state-path", str(state_path)])

        assert code == 0
        output = capsys.readouterr().out
        assert "NewCo" in output
        assert "Acme" not in output
        loaded = load_state(state_path)
        assert "qs:ashby:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" in loaded.seen_ids
        assert "qs:greenhouse:111" not in loaded.seen_ids
        assert loaded.quant_scraper_shas == {
            "quant-scraper/Quant-Internships-New-Grad-2027": "readme-sha"
        }

    def test_first_poll_marks_current_quant_roles_without_email(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(listings_sha="old-sha", seen_ids={"1"}, closed_ids_sha="old-sha"),
        )
        quant = _quant_row(
            company="NewCo",
            url="https://jobs.example/new",
            listing_id="qs:url:jobs.example/new",
            keys=["url:jobs.example/new"],
        )
        quant["posted_on"] = "2026-11-01"
        meta = {"sha": "fresh-sha", "size": 1, "download_url": "https://example.com/raw"}
        snapshot = QuantScraperPoll(
            shas={"quant-scraper/Quant-Internships-New-Grad-2027": "readme-sha"},
            listings=[quant],
            changed=True,
        )

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row(id="1")],
            ),
            patch("internship_notifier.cli.fetch_quant_scraper", return_value=snapshot),
        ):
            code = run(["--config", str(cfg), "--state-path", str(state_path)])

        assert code == 0
        assert "NewCo" not in capsys.readouterr().out
        loaded = load_state(state_path)
        assert loaded.quant_scraper_bootstrapped is True
        assert loaded.quant_scraper_posted_baselined is True
        assert loaded.quant_scraper_posted == {"qs:url:jobs.example/new": "2026-11-01"}
        assert "qs:url:jobs.example/new" in loaded.seen_ids
        assert "url:jobs.example/new" in loaded.seen_apply_keys

    def test_simplify_row_is_not_resent_after_quant_scraper(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="old-sha",
                seen_ids={"1"},
                closed_ids_sha="old-sha",
                quant_scraper_bootstrapped=True,
                seen_apply_keys={"greenhouse:111"},
            ),
        )
        meta = {"sha": "fresh-sha", "size": 1, "download_url": "https://example.com/raw"}
        new_simplify = _offseason_row(
            id="2",
            company_name="Acme",
            url="https://job-boards.greenhouse.io/acme/jobs/111",
        )

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row(id="1"), new_simplify],
            ),
        ):
            code = run(["--config", str(cfg), "--state-path", str(state_path)])

        assert code == 0
        assert "Acme" not in capsys.readouterr().out
        assert "2" in load_state(state_path).seen_ids

    def test_later_posted_date_emails_a_quant_repost(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        listing_id = "qs:url:jobs.example/repost"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="old-sha",
                seen_ids={"1", listing_id},
                closed_ids_sha="old-sha",
                quant_scraper_bootstrapped=True,
                quant_scraper_posted_baselined=True,
                quant_scraper_posted={listing_id: "2026-11-01"},
                seen_apply_keys={"url:jobs.example/repost"},
            ),
        )
        quant = _quant_row(
            company="Jane Street",
            url="https://jobs.example/repost",
            listing_id=listing_id,
            keys=["url:jobs.example/repost"],
        )
        quant["posted_on"] = "2026-11-13"
        meta = {"sha": "fresh-sha", "size": 1, "download_url": "https://example.com/raw"}
        snapshot = QuantScraperPoll(
            shas={"quant-scraper/Quant-Internships-New-Grad-2027": "readme-sha"},
            listings=[quant],
            changed=True,
        )

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row(id="1")],
            ),
            patch("internship_notifier.cli.fetch_quant_scraper", return_value=snapshot),
        ):
            code = run(["--config", str(cfg), "--state-path", str(state_path)])

        assert code == 0
        output = capsys.readouterr().out
        assert "Jane Street | Winter Intern (reposted) |" in output
        assert "https://jobs.example/repost" in output
        loaded = load_state(state_path)
        assert loaded.quant_scraper_posted[listing_id] == "2026-11-13"
        assert "url:jobs.example/repost" in loaded.seen_apply_keys

    def test_same_or_missing_posted_date_stays_quiet(
        self, tmp_path, clear_smtp_env, capsys
    ) -> None:
        cfg = _write_notifier(tmp_path)
        state_path = tmp_path / "state.json"
        same_id = "qs:url:jobs.example/same"
        older_id = "qs:url:jobs.example/older"
        blank_id = "qs:url:jobs.example/blank"
        unseen_date_id = "qs:url:jobs.example/baseline"
        _write_state(
            state_path,
            NotifierState(
                listings_sha="old-sha",
                seen_ids={"1", same_id, older_id, blank_id, unseen_date_id},
                closed_ids_sha="old-sha",
                quant_scraper_bootstrapped=True,
                quant_scraper_posted_baselined=True,
                quant_scraper_posted={
                    same_id: "2026-11-10",
                    older_id: "2026-11-10",
                    blank_id: "2026-11-10",
                },
                seen_apply_keys={
                    "url:jobs.example/same",
                    "url:jobs.example/older",
                    "url:jobs.example/blank",
                    "url:jobs.example/baseline",
                },
            ),
        )
        rows = []
        for listing_id, url, posted in (
            (same_id, "https://jobs.example/same", "2026-11-10"),
            (older_id, "https://jobs.example/older", "2026-11-01"),
            (blank_id, "https://jobs.example/blank", ""),
            (unseen_date_id, "https://jobs.example/baseline", "2026-11-12"),
        ):
            row = _quant_row(
                company="Quiet Co",
                url=url,
                listing_id=listing_id,
                keys=[url.removeprefix("https://")],
            )
            row["posted_on"] = posted
            row["apply_keys"] = [f"url:{url.removeprefix('https://')}"]
            rows.append(row)
        meta = {"sha": "fresh-sha", "size": 1, "download_url": "https://example.com/raw"}
        snapshot = QuantScraperPoll(
            shas={"quant-scraper/Quant-Internships-New-Grad-2027": "readme-sha"},
            listings=rows,
            changed=True,
        )

        with (
            patch(
                "internship_notifier.github_listings.get_listings_metadata",
                return_value=meta,
            ),
            patch(
                "internship_notifier.github_listings.fetch_listings_json",
                return_value=[_offseason_row(id="1")],
            ),
            patch("internship_notifier.cli.fetch_quant_scraper", return_value=snapshot),
        ):
            code = run(["--config", str(cfg), "--state-path", str(state_path)])

        assert code == 0
        assert "Quiet Co" not in capsys.readouterr().out
        loaded = load_state(state_path)
        assert loaded.quant_scraper_posted == {
            same_id: "2026-11-10",
            older_id: "2026-11-10",
            blank_id: "2026-11-10",
            unseen_date_id: "2026-11-12",
        }
