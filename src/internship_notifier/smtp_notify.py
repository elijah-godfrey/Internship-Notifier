"""Optional SMTP notifications using environment variables."""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

DEFAULT_SMTP_PORT = 587
DEFAULT_SUBJECT_PREFIX = "Internship notifier"
SMTP_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class SmtpSettings:
    """SMTP connection and envelope addresses.

    Attributes:
        host: SMTP server hostname.
        port: Server port (465 uses implicit TLS; others use plain SMTP, with
            STARTTLS when the server advertises it).
        user: Login username; empty string if the server needs no auth.
        password: Login password (often an app password for Gmail/Outlook).
        mail_from: RFC5322 From address.
        mail_to: RFC5322 To address (single recipient for v1).
    """

    host: str
    port: int
    user: str
    password: str
    mail_from: str
    mail_to: str


def settings_from_env() -> SmtpSettings | None:
    """Build settings from the process environment.

    If ``SMTP_HOST`` is unset or empty, returns ``None`` (email disabled).

    If ``SMTP_HOST`` is set, ``SMTP_FROM`` and ``SMTP_TO`` must also be set.

    Recognized variables: ``SMTP_HOST``, ``SMTP_PORT`` (default
    :data:`DEFAULT_SMTP_PORT`),
    ``SMTP_USER``, ``SMTP_PASSWORD``, ``SMTP_FROM``, ``SMTP_TO``.

    Returns:
        Frozen :class:`SmtpSettings`, or ``None`` when ``SMTP_HOST`` is absent.

    Raises:
        ValueError: When ``SMTP_HOST`` is set but ``SMTP_FROM`` or ``SMTP_TO``
            is missing, or when ``SMTP_PORT`` is not an integer.
    """
    host = (os.environ.get("SMTP_HOST") or "").strip()
    if not host:
        return None
    mail_from = (os.environ.get("SMTP_FROM") or "").strip()
    mail_to = (os.environ.get("SMTP_TO") or "").strip()
    if not mail_from or not mail_to:
        raise ValueError("SMTP_HOST is set; SMTP_FROM and SMTP_TO are required")
    port_raw = (os.environ.get("SMTP_PORT") or str(DEFAULT_SMTP_PORT)).strip()
    try:
        port = int(port_raw)
    except ValueError as e:
        raise ValueError(f"SMTP_PORT must be an integer, got {port_raw!r}") from e
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = os.environ.get("SMTP_PASSWORD") or ""
    return SmtpSettings(
        host=host,
        port=port,
        user=user,
        password=password,
        mail_from=mail_from,
        mail_to=mail_to,
    )


def send_email(
    *,
    subject: str,
    plain_body: str,
    settings: SmtpSettings,
    html_body: str | None = None,
) -> None:
    """Send a multipart email with a plain-text fallback.

    Uses STARTTLS on port 587 (default), implicit TLS on port 465, otherwise a
    plain SMTP session (still calls ``login`` when ``settings.user`` is set).

    Args:
        subject: Email subject line.
        plain_body: UTF-8 plain-text content.
        settings: Connection and envelope data.
        html_body: Optional HTML alternative.

    Raises:
        smtplib.SMTPException: On SMTP-level failures (auth, relay, etc.).
        OSError: On network errors.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.mail_from
    msg["To"] = settings.mail_to
    msg.set_content(plain_body)
    if html_body is not None:
        msg.add_alternative(html_body, subtype="html")

    if settings.port == 465:
        with smtplib.SMTP_SSL(
            settings.host, settings.port, timeout=SMTP_TIMEOUT_SECONDS
        ) as smtp:
            if settings.user:
                smtp.login(settings.user, settings.password)
            smtp.send_message(msg)
        return

    ctx = ssl.create_default_context()
    with smtplib.SMTP(settings.host, settings.port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        if smtp.has_extn("STARTTLS"):
            smtp.starttls(context=ctx)
            smtp.ehlo()
        if settings.user:
            smtp.login(settings.user, settings.password)
        smtp.send_message(msg)


def send_plaintext_email(*, subject: str, body: str, settings: SmtpSettings) -> None:
    """Backward-compatible wrapper for callers that only have plain text."""
    send_email(
        subject=subject,
        plain_body=body,
        settings=settings,
    )
