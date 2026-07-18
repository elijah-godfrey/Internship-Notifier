"""Unit tests for internship_notifier.smtp_notify (SMTP settings from env)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from internship_notifier.smtp_notify import (
    DEFAULT_SMTP_PORT,
    SmtpSettings,
    send_email,
    settings_from_env,
)

SMTP_KEYS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_FROM",
    "SMTP_TO",
    "SMTP_USER",
    "SMTP_PASSWORD",
)


def _clear_smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in SMTP_KEYS:
        monkeypatch.delenv(key, raising=False)


class TestSettingsFromEnv:
    def test_returns_none_when_host_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_smtp_env(monkeypatch)
        assert settings_from_env() is None

    def test_returns_none_when_host_whitespace_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_smtp_env(monkeypatch)
        monkeypatch.setenv("SMTP_HOST", "   ")
        assert settings_from_env() is None

    def test_builds_settings_with_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_smtp_env(monkeypatch)
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "from@example.com")
        monkeypatch.setenv("SMTP_TO", "to@example.com")
        got = settings_from_env()
        assert got == SmtpSettings(
            host="smtp.example.com",
            port=DEFAULT_SMTP_PORT,
            user="",
            password="",
            mail_from="from@example.com",
            mail_to="to@example.com",
        )

    def test_strips_host_from_to(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_smtp_env(monkeypatch)
        monkeypatch.setenv("SMTP_HOST", " smtp.example.com ")
        monkeypatch.setenv("SMTP_FROM", " a@b.com ")
        monkeypatch.setenv("SMTP_TO", " c@d.com ")
        got = settings_from_env()
        assert got is not None
        assert got.host == "smtp.example.com"
        assert got.mail_from == "a@b.com"
        assert got.mail_to == "c@d.com"

    def test_requires_from_when_host_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_smtp_env(monkeypatch)
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_TO", "to@example.com")
        with pytest.raises(ValueError, match="SMTP_FROM and SMTP_TO are required"):
            settings_from_env()

    def test_requires_to_when_host_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_smtp_env(monkeypatch)
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "from@example.com")
        with pytest.raises(ValueError, match="SMTP_FROM and SMTP_TO are required"):
            settings_from_env()

    def test_invalid_port_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_smtp_env(monkeypatch)
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "from@example.com")
        monkeypatch.setenv("SMTP_TO", "to@example.com")
        monkeypatch.setenv("SMTP_PORT", "not-a-number")
        with pytest.raises(ValueError, match="SMTP_PORT must be an integer"):
            settings_from_env()

    def test_custom_port_user_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_smtp_env(monkeypatch)
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "from@example.com")
        monkeypatch.setenv("SMTP_TO", "to@example.com")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        got = settings_from_env()
        assert got == SmtpSettings(
            host="smtp.example.com",
            port=465,
            user="user@example.com",
            password="secret",
            mail_from="from@example.com",
            mail_to="to@example.com",
        )


class TestSendEmail:
    def test_sends_plain_and_html_alternatives(self) -> None:
        settings = SmtpSettings(
            host="smtp.example.com",
            port=587,
            user="user@example.com",
            password="secret",
            mail_from="from@example.com",
            mail_to="to@example.com",
        )
        smtp = MagicMock()
        smtp.has_extn.return_value = False

        with patch("internship_notifier.smtp_notify.smtplib.SMTP") as smtp_class:
            smtp_class.return_value.__enter__.return_value = smtp
            send_email(
                subject="New internships",
                plain_body="Plain fallback",
                html_body="<strong>HTML</strong>",
                settings=settings,
            )

        message = smtp.send_message.call_args.args[0]
        assert message.is_multipart()
        parts = list(message.iter_parts())
        assert parts[0].get_content_type() == "text/plain"
        assert "Plain fallback" in parts[0].get_content()
        assert parts[1].get_content_type() == "text/html"
        assert "<strong>HTML</strong>" in parts[1].get_content()
