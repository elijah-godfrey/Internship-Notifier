"""Unit tests for plain-text and HTML notification formatting."""

from __future__ import annotations

import pytest

from internship_notifier.email_format import (
    NotificationListing,
    graduate_degree_label,
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


class TestGraduateDegreeLabel:
    @pytest.mark.parametrize(
        ("degrees", "title", "expected"),
        [
            (["Bachelor's", "Master's"], "Software Engineer Intern", None),
            (["Associate's"], "Software Engineer Intern", None),
            (["Master's"], "Software Engineer Intern", "Master's required"),
            (["PhD"], "Research Intern", "PhD required"),
            (["Master's", "PhD"], "Quant Intern", "Master's or PhD required"),
            (["MBA"], "Finance Intern", "MBA required"),
            (["MBA", "PhD"], "Research Intern", "Master's or PhD required"),
            ([], "Master Data Management Intern", None),
            (None, "Software Engineer Intern - MS/PhD", "Master's or PhD required"),
            (None, "PhD Research Intern", "PhD required"),
            (None, "Compiler Engineer Intern - Masters", "Master's required"),
            (None, "Global Product Support Intern Master's", "Master's required"),
            (["Bachelor’s"], "PhD Research Intern", None),
            (["Other"], "PhD Research Intern", None),
        ],
    )
    def test_labels_only_graduate_only_roles(
        self,
        degrees: list[str] | None,
        title: str,
        expected: str | None,
    ) -> None:
        listing: dict[str, object] = {"title": title}
        if degrees is not None:
            listing["degrees"] = degrees
        assert graduate_degree_label(listing) == expected

    def test_plaintext_puts_the_label_under_the_title(self) -> None:
        listing = NotificationListing.from_listing(
            {
                "company_name": "Jane Street",
                "title": "Quantitative Research Intern",
                "category": "Quantitative Finance",
                "url": "https://jobs.example/jane",
                "locations": ["New York, NY"],
                "terms": ["Summer 2027"],
                "degrees": ["Master's", "PhD"],
            }
        )
        body = render_plaintext_email([listing])
        title_at = body.index("Jane Street — Quantitative Research Intern")
        label_at = body.index("Master's or PhD required")
        details_at = body.index("Quantitative Finance")
        assert title_at < label_at < details_at

    def test_html_shows_the_status_label_and_keeps_it_on_one_line(self) -> None:
        listing = NotificationListing.from_listing(
            {
                "company_name": "Two Sigma",
                "title": "Research Intern",
                "category": "Software Engineering",
                "url": "https://jobs.example/two",
                "degrees": ["PhD"],
            }
        )
        body = render_html_email([listing])
        assert "PhD required" in body
        assert "white-space:nowrap" in body
        assert 'aria-hidden="true"' in body
        assert "#fff7ed" in body

    def test_undergrad_listing_has_no_degree_label(self) -> None:
        listing = NotificationListing.from_listing(
            {
                "company_name": "Stripe",
                "title": "Software Engineer Intern",
                "degrees": ["Bachelor's", "Master's"],
            }
        )
        body = render_html_email([listing])
        plain = render_plaintext_email([listing])
        assert listing.degree_label is None
        assert "required" not in body
        assert "required" not in plain


class TestListingOrdering:
    def test_higher_prestige_appears_first(self) -> None:
        sorted_listings = sort_notification_listings(
            [
                _listing(company="Lower", score=75),
                _listing(company="Higher", score=95),
            ]
        )
        assert [listing.company for listing in sorted_listings] == ["Higher", "Lower"]
