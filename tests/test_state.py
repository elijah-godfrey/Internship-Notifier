"""Unit tests for internship_notifier.state."""

from __future__ import annotations

import json

import pytest

from internship_notifier.state import NotifierState, load_state, save_state


class TestNotifierStateJson:
    def test_to_json_dict_sorts_seen_ids(self) -> None:
        state = NotifierState(
            listings_sha="abc",
            seen_ids={"3", "1", "2"},
        )
        assert state.to_json_dict() == {
            "closed_ids_sha": "",
            "listings_sha": "abc",
            "quant_scraper_bootstrapped": False,
            "quant_scraper_posted": {},
            "quant_scraper_posted_baselined": False,
            "quant_scraper_shas": {},
            "seen_apply_keys": [],
            "seen_ids": ["1", "2", "3"],
        }

    def test_from_json_dict_roundtrip(self) -> None:
        data = {"listings_sha": "deadbeef", "seen_ids": ["a", "b"]}
        state = NotifierState.from_json_dict(data)
        assert state.listings_sha == "deadbeef"
        assert state.seen_ids == {"a", "b"}
        assert NotifierState.from_json_dict(state.to_json_dict()) == state

    def test_from_json_dict_omitted_keys_use_defaults(self) -> None:
        state = NotifierState.from_json_dict({})
        assert state.listings_sha == ""
        assert state.seen_ids == set()
        assert state.closed_ids_sha == ""
        assert state.seen_apply_keys == set()
        assert state.quant_scraper_shas == {}
        assert state.quant_scraper_bootstrapped is False
        assert state.quant_scraper_posted == {}
        assert state.quant_scraper_posted_baselined is False

    def test_from_json_dict_seen_ids_must_be_list(self) -> None:
        with pytest.raises(ValueError, match="seen_ids must be a list"):
            NotifierState.from_json_dict({"seen_ids": "not-a-list"})

    def test_from_json_dict_seen_ids_entries_must_be_strings(self) -> None:
        with pytest.raises(ValueError, match="each seen_ids entry must be a string"):
            NotifierState.from_json_dict({"seen_ids": [1, "2"]})

    def test_from_json_dict_listings_sha_must_be_string(self) -> None:
        with pytest.raises(ValueError, match="listings_sha must be a string"):
            NotifierState.from_json_dict({"listings_sha": 123})

    def test_from_json_dict_closed_ids_sha_must_be_string(self) -> None:
        with pytest.raises(ValueError, match="closed_ids_sha must be a string"):
            NotifierState.from_json_dict({"closed_ids_sha": 123})

    def test_from_json_dict_quant_fields_roundtrip(self) -> None:
        state = NotifierState.from_json_dict(
            {
                "quant_scraper_bootstrapped": True,
                "quant_scraper_shas": {"quant-scraper/Quant-Internships-New-Grad-2027": "abc"},
                "seen_apply_keys": ["greenhouse:1"],
            }
        )
        assert state.quant_scraper_bootstrapped is True
        assert state.quant_scraper_shas == {
            "quant-scraper/Quant-Internships-New-Grad-2027": "abc"
        }
        assert state.seen_apply_keys == {"greenhouse:1"}
        assert state.quant_scraper_posted == {}
        assert state.quant_scraper_posted_baselined is False

    def test_from_json_dict_quant_posted_dates_roundtrip(self) -> None:
        state = NotifierState.from_json_dict(
            {
                "quant_scraper_posted": {"qs:greenhouse:1": "2026-11-10"},
                "quant_scraper_posted_baselined": True,
            }
        )
        assert state.quant_scraper_posted == {"qs:greenhouse:1": "2026-11-10"}
        assert state.quant_scraper_posted_baselined is True
        assert NotifierState.from_json_dict(state.to_json_dict()) == state

    def test_from_json_dict_quant_posted_baselined_must_be_bool(self) -> None:
        with pytest.raises(ValueError, match="quant_scraper_posted_baselined must be a boolean"):
            NotifierState.from_json_dict({"quant_scraper_posted_baselined": "yes"})

    def test_from_json_dict_quant_bootstrapped_must_be_bool(self) -> None:
        with pytest.raises(ValueError, match="quant_scraper_bootstrapped must be a boolean"):
            NotifierState.from_json_dict({"quant_scraper_bootstrapped": "yes"})


class TestLoadSaveState:
    def test_load_state_missing_file_returns_empty(self, tmp_path) -> None:
        path = tmp_path / "missing.json"
        assert load_state(path) == NotifierState()

    def test_save_and_load_roundtrip(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        original = NotifierState(
            listings_sha="sha256:example",
            seen_ids={"10", "20"},
        )
        save_state(original, path)
        loaded = load_state(path)
        assert loaded == original

    def test_load_state_rejects_non_object_json(self, tmp_path) -> None:
        path = tmp_path / "bad.json"
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ValueError, match="state file must contain a JSON object"):
            load_state(path)
