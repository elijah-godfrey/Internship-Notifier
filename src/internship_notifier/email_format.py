"""Plain-text and HTML presentation for internship notification emails."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from internship_notifier.prestige import CompanyPrestige

# Upstream ``degrees`` values. A bachelor's or associate's entry means an
# undergraduate can apply, so those listings stay unmarked.
_UNDERGRAD_DEGREES = frozenset({"bachelor's", "associate's"})
_MASTERS_DEGREES = frozenset({"master's"})
_PHD_DEGREES = frozenset({"phd"})
_MBA_DEGREES = frozenset({"mba"})
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'"})
_TITLE_PHD = re.compile(r"\b(?:ph\.?\s*d\.?|doctorate|doctoral)\b", re.IGNORECASE)
_TITLE_MBA = re.compile(r"\bmba\b", re.IGNORECASE)
_TITLE_MASTERS = re.compile(
    r"\b(?:master's|masters|m\.s\.?|m\.sc\.?|ms)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NotificationListing:
    """Email-ready fields for one internship listing."""

    company: str
    title: str
    category: str
    url: str
    locations: tuple[str, ...]
    terms: tuple[str, ...]
    prestige_score: int | None = None
    prestige_confidence: str | None = None
    prestige_reason: str | None = None
    degree_label: str | None = None

    @classmethod
    def from_listing(
        cls,
        listing: dict[str, Any],
        assessment: CompanyPrestige | None = None,
    ) -> NotificationListing:
        """Build presentation fields from an upstream listing and ranking."""
        return cls(
            company=str(listing.get("company_name") or "Unknown company"),
            title=str(listing.get("title") or "Internship"),
            category=str(listing.get("category") or "Uncategorized"),
            url=str(listing.get("url") or ""),
            locations=_string_tuple(listing.get("locations")),
            terms=_string_tuple(listing.get("terms")),
            prestige_score=(
                assessment.prestige_score if assessment is not None else None
            ),
            prestige_confidence=(
                assessment.confidence if assessment is not None else None
            ),
            prestige_reason=(
                assessment.reason if assessment is not None else None
            ),
            degree_label=graduate_degree_label(listing),
        )


def sort_notification_listings(
    listings: list[NotificationListing],
) -> list[NotificationListing]:
    """Sort ranked opportunities first, then by company and title."""
    return sorted(
        listings,
        key=lambda listing: (
            -(listing.prestige_score or 0),
            listing.company.casefold(),
            listing.title.casefold(),
        ),
    )


def render_plaintext_email(
    listings: list[NotificationListing],
    *,
    threshold_label: str | None = None,
) -> str:
    """Render a readable plain-text fallback."""
    noun = "opportunity" if len(listings) == 1 else "opportunities"
    lines = [f"{len(listings)} new internship {noun}"]
    if threshold_label:
        lines.append(f"Prestige threshold: {threshold_label}")
    lines.append("")

    for listing in listings:
        lines.append(f"{listing.company} — {listing.title}")
        if listing.degree_label:
            lines.append(listing.degree_label)
        details = [listing.category]
        if listing.locations:
            details.append(", ".join(listing.locations))
        if listing.terms:
            details.append(", ".join(listing.terms))
        lines.append(" | ".join(details))
        if listing.prestige_score is not None:
            confidence = listing.prestige_confidence or "unknown"
            lines.append(
                f"Prestige: {listing.prestige_score}/100 ({confidence} confidence)"
            )
        if listing.prestige_reason:
            lines.append(listing.prestige_reason)
        if listing.url:
            lines.append(listing.url)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_html_email(
    listings: list[NotificationListing],
    *,
    threshold_label: str | None = None,
) -> str:
    """Render conservative, inline-CSS HTML compatible with major email clients."""
    noun = "opportunity" if len(listings) == 1 else "opportunities"
    threshold = (
        f'<p style="margin:8px 0 0;color:#cbd5e1;font-size:14px;">'
        f"Prestige threshold: {html.escape(threshold_label)}</p>"
        if threshold_label
        else ""
    )
    cards = "".join(_render_listing_card(listing) for listing in listings)
    return f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,sans-serif;color:#0f172a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
         style="background:#f1f5f9;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
             style="max-width:680px;">
        <tr><td style="background:#0f172a;border-radius:14px;padding:24px;">
          <h1 style="margin:0;color:#ffffff;font-size:24px;line-height:1.3;">
            {len(listings)} new internship {noun}
          </h1>
          {threshold}
        </td></tr>
        <tr><td style="height:16px;"></td></tr>
        {cards}
        <tr><td style="padding:12px 4px;color:#64748b;font-size:12px;text-align:center;">
          Generated by internship-notifier. Links go directly to the job posting.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def graduate_degree_label(listing: dict[str, Any]) -> str | None:
    """Return a graduate-only label, or None when an undergraduate can apply.

    The upstream ``degrees`` list wins when it is present. A title is checked
    only when that list is missing or empty, and only for an explicit degree
    phrase such as ``PhD`` or ``MS/PhD``. ``Master Data`` does not match.
    """
    degrees = listing.get("degrees")
    if isinstance(degrees, list) and any(
        isinstance(item, str) and item.strip() for item in degrees
    ):
        kinds = _degree_kinds(degrees)
        if "undergrad" in kinds or "unknown" in kinds:
            return None
        return _label_for_kinds(kinds)
    return _label_for_kinds(_kinds_from_title(str(listing.get("title") or "")))


def _degree_kinds(degrees: list[object]) -> set[str]:
    """Classify recognized upstream degree names."""
    kinds: set[str] = set()
    for item in degrees:
        if not isinstance(item, str) or not item.strip():
            continue
        token = " ".join(item.translate(_APOSTROPHES).casefold().split())
        if token in _UNDERGRAD_DEGREES:
            kinds.add("undergrad")
        elif token in _MASTERS_DEGREES:
            kinds.add("masters")
        elif token in _PHD_DEGREES:
            kinds.add("phd")
        elif token in _MBA_DEGREES:
            kinds.add("mba")
        else:
            kinds.add("unknown")
    return kinds


def _kinds_from_title(title: str) -> set[str]:
    """Find explicit graduate-degree phrases in a job title."""
    title = title.translate(_APOSTROPHES)
    kinds: set[str] = set()
    if _TITLE_PHD.search(title):
        kinds.add("phd")
    if _TITLE_MASTERS.search(title):
        kinds.add("masters")
    if _TITLE_MBA.search(title):
        kinds.add("mba")
    return kinds


def _label_for_kinds(kinds: set[str]) -> str | None:
    """Turn recognized graduate degrees into one short status label."""
    masters = "masters" in kinds
    mba = "mba" in kinds
    phd = "phd" in kinds
    if (masters or mba) and phd:
        return "Master's or PhD required"
    if phd:
        return "PhD required"
    if mba and not masters:
        return "MBA required"
    if masters:
        return "Master's required"
    return None


def _render_degree_label(label: str) -> str:
    """Render the one-line graduate-degree status used in the email card."""
    safe_label = html.escape(label)
    return (
        '<div style="margin-top:10px;">'
        '<span style="display:inline-block;white-space:nowrap;background:#fff7ed;'
        "color:#9a3412;border:1px solid #fdba74;border-radius:999px;"
        'padding:3px 10px 3px 4px;font-size:13px;font-weight:bold;line-height:18px;">'
        '<span aria-hidden="true" style="display:inline-block;width:18px;height:18px;'
        "margin-right:6px;border-radius:999px;background:#9a3412;color:#fff7ed;"
        'font-size:12px;line-height:18px;text-align:center;vertical-align:middle;">!</span>'
        f'<span style="vertical-align:middle;">{safe_label}</span>'
        "</span></div>"
    )


def _render_listing_card(listing: NotificationListing) -> str:
    company = html.escape(listing.company)
    title = html.escape(listing.title)
    metadata = [listing.category, *listing.locations, *listing.terms]
    metadata_html = " &bull; ".join(html.escape(value) for value in metadata)
    degree_html = ""
    title_margin = "5px 0 8px"
    metadata_margin = ""
    if listing.degree_label:
        degree_html = _render_degree_label(listing.degree_label)
        title_margin = "5px 0 0"
        metadata_margin = "margin-top:8px;"
    prestige = ""
    if listing.prestige_score is not None:
        confidence = html.escape(listing.prestige_confidence or "unknown")
        prestige = (
            '<div style="margin-top:14px;padding:10px 12px;background:#eef2ff;'
            'border-radius:8px;color:#3730a3;font-size:14px;">'
            f"<strong>Prestige: {listing.prestige_score}/100</strong> "
            f"({confidence} confidence)"
        )
        if listing.prestige_reason:
            prestige += (
                f'<div style="margin-top:5px;color:#475569;">'
                f"{html.escape(listing.prestige_reason)}</div>"
            )
        prestige += "</div>"

    apply_button = ""
    safe_url = _safe_http_url(listing.url)
    if safe_url:
        apply_button = (
            '<div style="margin-top:18px;">'
            f'<a href="{html.escape(safe_url, quote=True)}" '
            'style="display:inline-block;background:#2563eb;color:#ffffff;'
            'text-decoration:none;font-weight:bold;padding:11px 18px;'
            'border-radius:8px;">Apply</a></div>'
        )

    return f"""<tr><td style="background:#ffffff;border:1px solid #e2e8f0;
border-radius:12px;padding:20px;">
  <div style="color:#2563eb;font-size:14px;font-weight:bold;">{company}</div>
  <h2 style="margin:{title_margin};font-size:20px;line-height:1.35;">{title}</h2>
  {degree_html}
  <div style="color:#64748b;font-size:14px;line-height:1.5;{metadata_margin}">{metadata_html}</div>
  {prestige}
  {apply_button}
</td></tr>
<tr><td style="height:12px;"></td></tr>
"""


def _safe_http_url(value: str) -> str | None:
    """Allow only normal HTTP(S) application links in HTML."""
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _string_tuple(value: object) -> tuple[str, ...]:
    """Normalize optional upstream string arrays for display."""
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if item)
