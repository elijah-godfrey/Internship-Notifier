"""Persist notifier state: last ``listings.json`` blob SHA and seen listing IDs."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NotifierState:
    """On-disk state for deduplication and skipping unchanged upstream blobs.

    Attributes:
        listings_sha: Git blob ``sha`` from the last Contents API response for
            ``listings.json``, or empty string if unknown.
        seen_ids: Listing ``id`` values already notified, bootstrapped, or
            remembered while closed.
        closed_ids_sha: Blob ``sha`` whose closed listings have already been
            merged into ``seen_ids``. Empty until that one-time pass has run
            for the current blob.
        seen_apply_keys: Apply-URL identity keys for quant-scraper roles already
            notified or bootstrapped. A later SimplifyJobs row with the same
            key is not emailed again.
        quant_scraper_shas: README blob SHAs for the quant-scraper repos, keyed
            by ``owner/name``.
        quant_scraper_bootstrapped: True after the current quant-scraper
            internships have been marked seen once. Missing on old state files,
            which means that first pass has not run.
    """

    listings_sha: str = ""
    seen_ids: set[str] = field(default_factory=set)
    closed_ids_sha: str = ""
    seen_apply_keys: set[str] = field(default_factory=set)
    quant_scraper_shas: dict[str, str] = field(default_factory=dict)
    quant_scraper_bootstrapped: bool = False

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict (id sets as sorted lists)."""
        return {
            "closed_ids_sha": self.closed_ids_sha,
            "listings_sha": self.listings_sha,
            "quant_scraper_bootstrapped": self.quant_scraper_bootstrapped,
            "quant_scraper_shas": dict(sorted(self.quant_scraper_shas.items())),
            "seen_apply_keys": sorted(self.seen_apply_keys),
            "seen_ids": sorted(self.seen_ids),
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> NotifierState:
        """Build state from dict loaded from JSON."""
        seen = _string_set(data.get("seen_ids") or [], "seen_ids")
        sha = data.get("listings_sha") or ""
        if not isinstance(sha, str):
            raise ValueError("listings_sha must be a string")
        closed_ids_sha = data.get("closed_ids_sha") or ""
        if not isinstance(closed_ids_sha, str):
            raise ValueError("closed_ids_sha must be a string")
        seen_apply_keys = _string_set(data.get("seen_apply_keys") or [], "seen_apply_keys")
        quant_scraper_shas = _string_dict(
            data.get("quant_scraper_shas") or {},
            "quant_scraper_shas",
        )
        bootstrapped = data.get("quant_scraper_bootstrapped", False)
        if not isinstance(bootstrapped, bool):
            raise ValueError("quant_scraper_bootstrapped must be a boolean")
        return cls(
            listings_sha=sha,
            seen_ids=seen,
            closed_ids_sha=closed_ids_sha,
            seen_apply_keys=seen_apply_keys,
            quant_scraper_shas=quant_scraper_shas,
            quant_scraper_bootstrapped=bootstrapped,
        )


def _string_set(raw: object, label: str) -> set[str]:
    """Validate a JSON list of strings and return it as a set."""
    if not isinstance(raw, list):
        raise ValueError(f"{label} must be a list of strings")
    values: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"each {label} entry must be a string")
        values.add(item)
    return values


def _string_dict(raw: object, label: str) -> dict[str, str]:
    """Validate a JSON object whose keys and values are strings."""
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object of strings")
    values: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"each {label} entry must be a string")
        values[key] = value
    return values


def default_state_path() -> Path:
    """Default path for state JSON (user data dir, cross-platform).

    Returns:
        ``Path`` to ``internship-notifier/state.json`` under ``%APPDATA%`` on
        Windows, ``~/Library/Application Support`` on macOS, or
        ``$XDG_STATE_HOME`` / ``~/.local/state`` on other Unix-like systems.
    """
    if os.name == "nt" or sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_STATE_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "internship-notifier" / "state.json"


def load_state(path: Path | None = None) -> NotifierState:
    """Load state from ``path``, or return empty state if the file is missing.

    Args:
        path: JSON file location. Defaults to :func:`default_state_path`.

    Returns:
        Parsed :class:`NotifierState`, or a new empty instance if the file does
        not exist.

    Raises:
        ValueError: If the JSON shape is invalid.
        OSError: If the file exists but cannot be read.
    """
    path = path or default_state_path()
    if not path.is_file():
        return NotifierState()
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("state file must contain a JSON object")
    return NotifierState.from_json_dict(data)


def save_state(state: NotifierState, path: Path | None = None) -> None:
    """Write ``state`` to ``path`` (creates parent directories).

    Args:
        state: In-memory state to persist.
        path: JSON file location. Defaults to :func:`default_state_path`.

    Raises:
        OSError: If the directory cannot be created or the file cannot be written.
    """
    path = path or default_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_json_dict(), indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")
