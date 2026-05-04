"""Unit tests for internship_notifier.config_toml."""

from __future__ import annotations

import textwrap

import pytest

from internship_notifier.config_toml import (
    NotifierTomlConfig,
    load_notifier_toml,
    resolve_config_path,
)


def _write_toml(path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


class TestLoadNotifierToml:
    def test_loads_offseason_with_categories(self, tmp_path) -> None:
        p = tmp_path / "notifier.toml"
        _write_toml(
            p,
            """
            source = "offseason"
            all_categories = false
            categories = ["Software Engineering", "Data Science"]
            """,
        )
        cfg = load_notifier_toml(p)
        assert cfg == NotifierTomlConfig(
            source="offseason",
            all_categories=False,
            categories=["Software Engineering", "Data Science"],
        )

    def test_loads_summer_all_categories(self, tmp_path) -> None:
        p = tmp_path / "notifier.toml"
        _write_toml(
            p,
            """
            source = "summer2026"
            all_categories = true
            categories = ["ignored when all true"]
            """,
        )
        cfg = load_notifier_toml(p)
        assert cfg.source == "summer2026"
        assert cfg.all_categories is True
        assert cfg.categories == []

    def test_strips_category_whitespace_and_skips_empty(self, tmp_path) -> None:
        p = tmp_path / "notifier.toml"
        _write_toml(
            p,
            """
            source = "offseason"
            all_categories = false
            categories = ["  Software Engineering  ", "", "  "]
            """,
        )
        cfg = load_notifier_toml(p)
        assert cfg.categories == ["Software Engineering"]

    def test_missing_file_raises(self, tmp_path) -> None:
        p = tmp_path / "nope.toml"
        with pytest.raises(FileNotFoundError):
            load_notifier_toml(p)

    def test_invalid_source_raises(self, tmp_path) -> None:
        p = tmp_path / "notifier.toml"
        _write_toml(
            p,
            """
            source = "winter2027"
            all_categories = true
            """,
        )
        with pytest.raises(ValueError, match="source"):
            load_notifier_toml(p)

    def test_all_categories_must_be_boolean(self, tmp_path) -> None:
        p = tmp_path / "notifier.toml"
        _write_toml(
            p,
            """
            source = "offseason"
            all_categories = "yes"
            categories = ["Software Engineering"]
            """,
        )
        with pytest.raises(ValueError, match="all_categories"):
            load_notifier_toml(p)

    def test_categories_must_be_strings_when_not_all(self, tmp_path) -> None:
        p = tmp_path / "notifier.toml"
        _write_toml(
            p,
            """
            source = "offseason"
            all_categories = false
            categories = [1, 2]
            """,
        )
        with pytest.raises(ValueError, match="categories"):
            load_notifier_toml(p)

    def test_categories_non_empty_when_not_all(self, tmp_path) -> None:
        p = tmp_path / "notifier.toml"
        _write_toml(
            p,
            """
            source = "offseason"
            all_categories = false
            categories = []
            """,
        )
        with pytest.raises(ValueError, match="categories"):
            load_notifier_toml(p)


class TestResolveConfigPath:
    def test_explicit_missing_raises(self, tmp_path) -> None:
        missing = tmp_path / "missing.toml"
        with pytest.raises(FileNotFoundError, match="notifier config not found"):
            resolve_config_path(missing)

    def test_explicit_existing_returns_path(self, tmp_path) -> None:
        p = tmp_path / "custom.toml"
        p.write_text('source = "offseason"\nall_categories = true\n', encoding="utf-8")
        assert resolve_config_path(p) == p

    def test_notifier_config_env(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = tmp_path / "from-env.toml"
        p.write_text('source = "offseason"\nall_categories = true\n', encoding="utf-8")
        monkeypatch.setenv("NOTIFIER_CONFIG", str(p))
        assert resolve_config_path(None) == p

    def test_notifier_config_env_missing_file_returns_none(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOTIFIER_CONFIG", str(tmp_path / "does-not-exist.toml"))
        monkeypatch.chdir(tmp_path)
        assert resolve_config_path(None) is None

    def test_cwd_notifier_toml(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOTIFIER_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        cwd_toml = tmp_path / "notifier.toml"
        cwd_toml.write_text('source = "offseason"\nall_categories = true\n', encoding="utf-8")
        assert resolve_config_path(None) == cwd_toml

    def test_returns_none_when_no_candidate(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOTIFIER_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        assert resolve_config_path(None) is None
