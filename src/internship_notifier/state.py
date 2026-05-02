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
        seen_ids: Listing ``id`` values already notified (or bootstrapped).
    """

    listings_sha: str = ""
    seen_ids: set[str] = field(default_factory=set)

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict (``seen_ids`` as sorted list)."""
        return {
            "listings_sha": self.listings_sha,
            "seen_ids": sorted(self.seen_ids),
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> NotifierState:
        """Build state from dict loaded from JSON."""
        raw_ids = data.get("seen_ids") or []
        if not isinstance(raw_ids, list):
            raise ValueError("seen_ids must be a list of strings")
        seen: set[str] = set()
        for x in raw_ids:
            if not isinstance(x, str):
                raise ValueError("each seen_ids entry must be a string")
            seen.add(x)
        sha = data.get("listings_sha") or ""
        if not isinstance(sha, str):
            raise ValueError("listings_sha must be a string")
        return cls(listings_sha=sha, seen_ids=seen)


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
