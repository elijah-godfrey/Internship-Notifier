"""Fetch upstream `listings.json` metadata and body from GitHub.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

UPSTREAM_OWNER = "SimplifyJobs"
UPSTREAM_REPO = "Summer2026-Internships"
LISTINGS_PATH = ".github/scripts/listings.json"
DEFAULT_REF = "dev"
USER_AGENT = "internship-notifier/0.1"


def _auth_headers(token: str | None) -> dict[str, str]:
    """Build HTTP headers for GitHub API or raw download requests.

    Args:
        token: Optional bearer token. If omitted, ``GITHUB_TOKEN`` from the
            environment is used when set.

    Returns:
        A header dict including ``Accept``, API version, ``User-Agent``, and
        ``Authorization`` when a token is available.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    token = token or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_get(url: str, token: str | None = None, timeout: int = 120) -> bytes:
    """Perform an HTTP GET and return the response body.

    Args:
        url: Full URL to request.
        token: Optional GitHub token forwarded to :func:`_auth_headers`.
        timeout: Socket timeout in seconds.

    Returns:
        Raw response bytes.

    Raises:
        RuntimeError: If the server returns a non-success HTTP status (message
            includes status code and a snippet of the body).
        urllib.error.URLError: On network-level failures (DNS, timeout, etc.).
    """
    req = urllib.request.Request(url, headers=_auth_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} for {url}: {body[:500]}") from e


def listings_contents_url(ref: str = DEFAULT_REF) -> str:
    """Return the GitHub REST URL for the ``contents`` metadata of ``listings.json``.

    Args:
        ref: Git branch, tag, or commit SHA (default matches upstream ``dev``).

    Returns:
        Absolute ``api.github.com`` URL with path and ref query segments
        URL-encoded for the Contents API.
    """
    encoded = urllib.parse.quote(LISTINGS_PATH)
    return (
        f"https://api.github.com/repos/{UPSTREAM_OWNER}/{UPSTREAM_REPO}/"
        f"contents/{encoded}?ref={urllib.parse.quote(ref)}"
    )


def get_listings_metadata(ref: str = DEFAULT_REF, token: str | None = None) -> dict[str, Any]:
    """Fetch blob metadata for upstream ``listings.json`` (small response).

    Use this to read the blob ``sha`` without downloading the full (~14 MiB)
    JSON array. When ``sha`` changes, call :func:`fetch_listings_json` with the
    returned ``download_url``.

    Args:
        ref: Git branch, tag, or commit SHA for the upstream repo.
        token: Optional GitHub token (or set ``GITHUB_TOKEN``) for higher rate
            limits on the REST API.

    Returns:
        A dict with keys ``sha`` (str), ``size`` (int), and ``download_url``
        (str) as returned by the GitHub Contents API.

    Raises:
        RuntimeError: If the JSON response is missing any of the required keys,
            or if :func:`_http_get` raises for HTTP errors.
    """
    raw = _http_get(listings_contents_url(ref=ref), token=token, timeout=60)
    data = json.loads(raw.decode())
    for key in ("sha", "size", "download_url"):
        if key not in data:
            raise RuntimeError(f"GitHub response missing {key!r}: keys={list(data)!r}")
    return {
        "sha": data["sha"],
        "size": data["size"],
        "download_url": data["download_url"],
    }


def fetch_listings_json(download_url: str, token: str | None = None) -> list[dict[str, Any]]:
    """Download and parse the full ``listings.json`` array from ``download_url``.

    This transfer is large; call only when metadata ``sha`` (or size) has
    changed compared to your last run.

    Args:
        download_url: The ``download_url`` value from :func:`get_listings_metadata`.
        token: Optional token; raw ``raw.githubusercontent.com`` downloads
            usually work without auth, but a token may help with rate limits in
            some environments.

    Returns:
        The parsed JSON root as a list of listing dicts.

    Raises:
        RuntimeError: If the decoded JSON is not a list, or on HTTP errors from
            :func:`_http_get`.
    """
    raw = _http_get(download_url, token=token, timeout=120)
    parsed = json.loads(raw.decode())
    if not isinstance(parsed, list):
        raise RuntimeError(f"Expected JSON array, got {type(parsed).__name__}")
    return parsed
