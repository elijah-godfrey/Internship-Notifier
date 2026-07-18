"""Unit tests for plain-text and HTML notification formatting."""

from __future__ import annotations

from internship_notifier.email_format import (
    NotificationListing,
    render_html_email,
    render_plaintext_email,
    sort_notification_listings,
)


def _listing(
    *,
    company: str = "Acme",
    score: int | None = 85,
    url: str = "https://jobs.example/apply",
) -> NotificationListing:
    return NotificationListing(
        company=company,
        title="Software Engineering Intern",
        category="Software Engineering",
        url=url,
        locations=("New York, NY",),
        terms=("Summer 2026",),
        prestige_score=score,
        prestige_confidence="high" if score is not None else None,
        prestige_reason="Strong engineering reputation." if score is not None else None,
    )


class TestPlaintextEmail:
    def test_includes_listing_and_prestige_details(self) -> None:
        body = render_plaintext_email(
            [_listing()],
            threshold_label="Carta benchmark (78/100)",
        )
        assert "1 new internship opportunity" in body
        assert "Acme — Software Engineering Intern" in body
        assert "Prestige: 85/100 (high confidence)" in body
        assert "Carta benchmark (78/100)" in body
        assert "https://jobs.example/apply" in body

    def test_works_without_prestige(self) -> None:
        body = render_plaintext_email([_listing(score=None)])
        assert "Prestige:" not in body


class TestHtmlEmail:
    def test_contains_card_apply_button_and_prestige(self) -> None:
        body = render_html_email(
            [_listing()],
            threshold_label="Carta benchmark (78/100)",
        )
        assert "<!doctype html>" in body
        assert "Software Engineering Intern" in body
        assert "Prestige: 85/100" in body
        assert 'href="https://jobs.example/apply"' in body
        assert ">Apply</a>" in body

    def test_escapes_untrusted_listing_text(self) -> None:
        listing = NotificationListing(
            company="<script>alert(1)</script>",
            title="R&D | Intern",
            category="Software Engineering",
            url="javascript:alert(1)",
            locations=(),
            terms=(),
            prestige_score=70,
            prestige_confidence="low",
            prestige_reason="<b>reason</b>",
        )
        body = render_html_email([listing])
        assert "<script>" not in body
        assert "&lt;script&gt;" in body
        assert "&lt;b&gt;reason&lt;/b&gt;" in body
        assert "javascript:" not in body
        assert ">Apply</a>" not in body


class TestListingOrdering:
    def test_higher_prestige_appears_first(self) -> None:
        sorted_listings = sort_notification_listings(
            [
                _listing(company="Lower", score=75),
                _listing(company="Higher", score=95),
            ]
        )
        assert [listing.company for listing in sorted_listings] == ["Higher", "Lower"]
