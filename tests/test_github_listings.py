"""Unit tests for internship_notifier.github_listings."""

from __future__ import annotations

import json
import urllib.parse
from unittest.mock import patch

import pytest

from internship_notifier import github_listings
from internship_notifier.github_listings import (
    LISTINGS_PATH,
    _auth_headers,
    fetch_listings_json,
    get_listings_metadata,
    listings_contents_url,
)


class TestListingsContentsUrl:
    def test_default_ref_dev(self) -> None:
        url = listings_contents_url()
        assert url.startswith("https://api.github.com/repos/SimplifyJobs/Summer2026-Internships/contents/")
        assert "ref=dev" in url
        encoded = urllib.parse.quote(LISTINGS_PATH)
        assert f"contents/{encoded}" in url

    def test_ref_query_value_is_quoted(self) -> None:
        # Spaces must be encoded; '/' stays unencoded (urllib.parse.quote default safe).
        ref = "my branch/extra"
        url = listings_contents_url(ref=ref)
        assert "ref=my%20branch/extra" in url


class TestAuthHeaders:
    def test_no_token_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        h = _auth_headers(None)
        assert "Authorization" not in h
        assert h["User-Agent"] == github_listings.USER_AGENT

    def test_explicit_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        h = _auth_headers("secret-token")
        assert h["Authorization"] == "Bearer secret-token"

    def test_token_from_env_when_param_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "from-env")
        h = _auth_headers(None)
        assert h["Authorization"] == "Bearer from-env"


class TestGetListingsMetadata:
    def test_parses_required_keys(self) -> None:
        payload = {
            "sha": "abc123",
            "size": 99,
            "download_url": "https://example.com/blob",
        }
        with patch.object(github_listings, "_http_get", return_value=json.dumps(payload).encode()):
            meta = get_listings_metadata(ref="dev", token=None)
        assert meta == {
            "sha": "abc123",
            "size": 99,
            "download_url": "https://example.com/blob",
        }

    def test_raises_when_key_missing(self) -> None:
        payload = {"sha": "x", "size": 1}
        with patch.object(github_listings, "_http_get", return_value=json.dumps(payload).encode()):
            with pytest.raises(RuntimeError, match="GitHub response missing 'download_url'"):
                get_listings_metadata()


class TestFetchListingsJson:
    def test_returns_list_of_dicts(self) -> None:
        body = [{"id": "1"}]
        with patch.object(github_listings, "_http_get", return_value=json.dumps(body).encode()):
            out = fetch_listings_json("https://example.com/raw")
        assert out == body

    def test_raises_when_root_not_list(self) -> None:
        with patch.object(github_listings, "_http_get", return_value=b"{}"):
            with pytest.raises(RuntimeError, match="Expected JSON array"):
                fetch_listings_json("https://example.com/raw")
