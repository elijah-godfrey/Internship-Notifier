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
) -> Path:
    path = tmp / "notifier.toml"
    ac = "true" if all_categories else "false"
    cats = "" if all_categories else 'categories = ["Software Engineering"]'
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


@pytest.fixture
def clear_smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_FROM",
        "SMTP_TO",
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
            "terms": ["Summer 2026"],
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
            NotifierState(listings_sha="same-sha", seen_ids={"1"}),
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
            NotifierState(listings_sha="same-sha", seen_ids={"1"}),
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
        assert "7" in loaded.seen_ids


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
                with patch("internship_notifier.smtp_notify.send_plaintext_email") as send_mail:
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
        assert "Acme" in kwargs["body"] and "https://jobs.example/apply" in kwargs["body"]
        err = capsys.readouterr().err
        assert "Sent email to to@example.com." in err

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
                    with patch("internship_notifier.smtp_notify.send_plaintext_email") as send_mail:
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
            pytest.raises(PrestigeRankingError, match="API unavailable"),
        ):
            run(
                [
                    "--config",
                    str(cfg),
                    "--state-path",
                    str(state_path),
                ]
            )

        assert load_state(state_path) == original
        assert not cache_path.exists()

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
                "internship_notifier.smtp_notify.send_plaintext_email"
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
        assert "Acme" in message["body"]
        assert "Beta" not in message["body"]
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
