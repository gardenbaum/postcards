"""Unit tests for the XDG path-resolution helpers.

The :mod:`postcards.addressbook.paths` module owns the
``POSTCARDS_DATA_DIR`` / ``XDG_DATA_HOME`` / ``$HOME/.local/share``
resolution chain. Tests cover each branch of the chain plus the
directory-creation side effect, and they make sure the
``POSTCARDS_DATA_DIR`` override works without mutating the real
user data.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from postcards.addressbook.paths import (
    ADDRESS_BOOK_FILENAME,
    TEMPLATE_BOOK_FILENAME,
    address_book_path,
    data_dir,
    template_book_path,
)


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Strip the relevant env vars so tests start from a clean slate.

    We do NOT touch ``HOME`` itself; the override fixture
    (``POSTCARDS_DATA_DIR``) is what each test pins the
    resolution to.
    """
    monkeypatch.delenv("POSTCARDS_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    yield


class TestDataDir:
    def test_postcards_data_dir_env_var_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("POSTCARDS_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "should-be-ignored"))
        assert data_dir() == tmp_path.resolve()

    def test_xdg_data_home_is_used_when_no_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        xdg = tmp_path / "xdg"
        xdg.mkdir()
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
        assert data_dir() == (xdg / "postcards").resolve()

    def test_falls_back_to_home_local_share(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        # XDG_DATA_HOME is already deleted by the autouse fixture.
        expected = (home / ".local" / "share" / "postcards").resolve()
        assert data_dir() == expected

    def test_creates_directory_on_demand(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = tmp_path / "freshly-created"
        monkeypatch.setenv("POSTCARDS_DATA_DIR", str(target))
        assert not target.exists()
        result = data_dir()
        assert result == target.resolve()
        assert result.is_dir()

    def test_explicit_path_argument_overrides_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("POSTCARDS_DATA_DIR", str(tmp_path / "ignored"))
        explicit = tmp_path / "explicit"
        result = data_dir(override=explicit)
        assert result == explicit.resolve()


class TestPathHelpers:
    def test_address_book_path_default(self, tmp_path: Path) -> None:
        os.environ.pop("POSTCARDS_DATA_DIR", None)
        os.environ["POSTCARDS_DATA_DIR"] = str(tmp_path)
        try:
            path = address_book_path()
            assert path == (tmp_path / ADDRESS_BOOK_FILENAME).resolve()
            assert path.parent.is_dir()
        finally:
            del os.environ["POSTCARDS_DATA_DIR"]

    def test_template_book_path_default(self, tmp_path: Path) -> None:
        os.environ.pop("POSTCARDS_DATA_DIR", None)
        os.environ["POSTCARDS_DATA_DIR"] = str(tmp_path)
        try:
            path = template_book_path()
            assert path == (tmp_path / TEMPLATE_BOOK_FILENAME).resolve()
        finally:
            del os.environ["POSTCARDS_DATA_DIR"]

    def test_address_book_path_with_explicit_override(self, tmp_path: Path) -> None:
        path = address_book_path(override=tmp_path)
        assert path == (tmp_path / ADDRESS_BOOK_FILENAME).resolve()

    def test_filenames_are_stable(self) -> None:
        # The filenames are part of the on-disk contract — renaming
        # them would orphan existing user data without a migration.
        assert ADDRESS_BOOK_FILENAME == "addressbook.json"
        assert TEMPLATE_BOOK_FILENAME == "templates.json"
