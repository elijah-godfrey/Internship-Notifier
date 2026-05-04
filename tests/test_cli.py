"""Unit tests for internship_notifier.cli.run (mocked GitHub + state)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from internship_notifier.cli import run
from internship_notifier.state import NotifierState, load_state


def _offseason_row(**overrides: object) -> dict:
    row = {
        "id": "100",
        "is_visible": True,
        "terms": ["Fall 2025"],
        "category": "Software Engineering",
        "company_name": "Acme",
        "title": "Intern",
        "url": "https://jobs.example/apply",
    }
    row.update(overrides)
    return row


def _write_notifier(tmp: Path, *, all_categories: bool = True) -> Path:
    path = tmp / "notifier.toml"
    ac = "true" if all_categories else "false"
    cats = "" if all_categories else 'categories = ["Software Engineering"]'
    path.write_text(
        textwrap.dedent(
            f"""
            source = "offseason"
            all_categories = {ac}
            {cats}
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return path


def _write_state(path: Path, state: NotifierState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_json_dict(), indent=2) + "\n", encoding="utf-8")


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
