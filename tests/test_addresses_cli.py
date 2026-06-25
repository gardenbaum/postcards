"""Tests for the ``postcards addresses`` Typer command group.

The command group owns the user-facing surface for the
:class:`postcards.addressbook.models.AddressBook`. These tests
exercise every subcommand through
:func:`postcards.cli.runner.run` and pin the storage layer to
``tmp_path`` via the :data:`POSTCARDS_DATA_DIR` env var so the
real user data is never touched.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner
from typer.testing import Result as CliResult

from postcards.addressbook.storage import load_address_book
from postcards.cli import run as cli_run

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Pin storage to ``tmp_path`` so tests are hermetic."""
    monkeypatch.setenv("POSTCARDS_DATA_DIR", str(tmp_path / "data"))
    yield


def _invoke(*args: str) -> CliResult:
    return cli_run(list(args))


# ---------------------------------------------------------------------------
# Help / smoke
# ---------------------------------------------------------------------------


def test_addresses_help_lists_subcommands(runner: CliRunner) -> None:
    result = _invoke("addresses", "--help")
    assert result.exit_code == 0, result.output
    out = result.output.lower()
    for sub in ("add", "list", "show", "update", "remove"):
        assert sub in out, f"missing subcommand {sub!r} in 'addresses --help':\n{result.output}"


def test_addresses_no_args_shows_help() -> None:
    result = _invoke("addresses")
    assert result.exit_code == 2, result.output
    assert "usage" in result.output.lower()


def test_postcards_top_level_help_includes_addresses() -> None:
    result = _invoke("--help")
    assert result.exit_code == 0, result.output
    assert "addresses" in result.output


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_addresses_add_creates_entry() -> None:
    result = _invoke(
        "addresses",
        "add",
        "alice",
        "--category",
        "recipient",
        "--prename",
        "Alice",
        "--lastname",
        "Zuercher",
        "--street",
        "Bahnhofstrasse 1",
        "--zip-code",
        "8000",
        "--place",
        "Zurich",
    )
    assert result.exit_code == 0, result.output
    book = load_address_book()
    entry = book.get("alice")
    assert entry.address.prename == "Alice"
    assert entry.address.lastname == "Zuercher"
    assert entry.category.value == "recipient"


def test_addresses_add_default_category_is_recipient() -> None:
    result = _invoke(
        "addresses",
        "add",
        "alice",
        "--prename",
        "Alice",
        "--lastname",
        "Z",
    )
    assert result.exit_code == 0, result.output
    assert load_address_book().get("alice").category.value == "recipient"


def test_addresses_add_accepts_sender_aliases() -> None:
    result = _invoke(
        "addresses",
        "add",
        "home",
        "--category",
        "sender",
        "--prename",
        "Andrin",
        "--lastname",
        "B",
    )
    assert result.exit_code == 0, result.output
    assert load_address_book().get("home").category.value == "sender"

    result = _invoke(
        "addresses",
        "add",
        "work",
        "--category",
        "from",
        "--prename",
        "Andrin",
        "--lastname",
        "B",
    )
    assert result.exit_code == 0, result.output
    assert load_address_book().get("work").category.value == "sender"


def test_addresses_add_rejects_duplicate_name() -> None:
    first = _invoke(
        "addresses",
        "add",
        "alice",
        "--prename",
        "A",
        "--lastname",
        "B",
    )
    assert first.exit_code == 0, first.output
    second = _invoke(
        "addresses",
        "add",
        "alice",
        "--prename",
        "A",
        "--lastname",
        "B",
    )
    assert second.exit_code == 2
    assert "already exists" in second.output


def test_addresses_add_rejects_invalid_name() -> None:
    result = _invoke(
        "addresses",
        "add",
        "Bad Name",
        "--prename",
        "A",
        "--lastname",
        "B",
    )
    assert result.exit_code == 2
    assert "name" in result.output.lower()


def test_addresses_add_rejects_unknown_category() -> None:
    result = _invoke(
        "addresses",
        "add",
        "alice",
        "--category",
        "neighbour",
        "--prename",
        "A",
        "--lastname",
        "B",
    )
    assert result.exit_code == 2
    assert "unknown address category" in result.output


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_addresses_list_empty_book_prints_hint() -> None:
    result = _invoke("addresses", "list")
    assert result.exit_code == 0, result.output
    assert "empty" in result.output.lower()


def test_addresses_list_prints_table() -> None:
    _invoke(
        "addresses",
        "add",
        "alice",
        "--category",
        "recipient",
        "--prename",
        "Alice",
        "--lastname",
        "Z",
        "--place",
        "Zurich",
    )
    _invoke(
        "addresses",
        "add",
        "home",
        "--category",
        "sender",
        "--prename",
        "Andrin",
        "--lastname",
        "B",
        "--place",
        "Zurich",
    )
    result = _invoke("addresses", "list")
    assert result.exit_code == 0, result.output
    assert "alice" in result.output
    assert "home" in result.output
    assert "recipient" in result.output
    assert "sender" in result.output


def test_addresses_list_filters_by_category() -> None:
    _invoke(
        "addresses",
        "add",
        "alice",
        "--category",
        "recipient",
        "--prename",
        "A",
        "--lastname",
        "B",
    )
    _invoke(
        "addresses",
        "add",
        "home",
        "--category",
        "sender",
        "--prename",
        "H",
        "--lastname",
        "B",
    )
    result = _invoke("addresses", "list", "--category", "sender")
    assert result.exit_code == 0, result.output
    assert "home" in result.output
    assert "alice" not in result.output


def test_addresses_list_rejects_unknown_category() -> None:
    result = _invoke("addresses", "list", "--category", "neighbour")
    assert result.exit_code == 2
    assert "unknown address category" in result.output


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_addresses_show_prints_full_record() -> None:
    _invoke(
        "addresses",
        "add",
        "alice",
        "--category",
        "recipient",
        "--prename",
        "Alice",
        "--lastname",
        "Zuercher",
        "--street",
        "Bahnhofstrasse 1",
        "--zip-code",
        "8000",
        "--place",
        "Zurich",
        "--salutation",
        "Ms.",
        "--notes",
        "friend from uni",
    )
    result = _invoke("addresses", "show", "alice")
    assert result.exit_code == 0, result.output
    assert "Alice" in result.output
    assert "Zuercher" in result.output
    assert "Bahnhofstrasse 1" in result.output
    assert "8000" in result.output
    assert "Zurich" in result.output
    assert "friend from uni" in result.output


def test_addresses_show_unknown_name_is_cli_error() -> None:
    result = _invoke("addresses", "show", "ghost")
    assert result.exit_code == 2
    assert "no address-book entry named 'ghost'" in result.output


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_addresses_update_patches_fields() -> None:
    _invoke(
        "addresses",
        "add",
        "alice",
        "--prename",
        "Alice",
        "--lastname",
        "Z",
        "--place",
        "Zurich",
    )
    result = _invoke(
        "addresses",
        "update",
        "alice",
        "--place",
        "Bern",
        "--street",
        "Bahnhofstrasse 1",
    )
    assert result.exit_code == 0, result.output
    entry = load_address_book().get("alice")
    assert entry.address.place == "Bern"
    assert entry.address.street == "Bahnhofstrasse 1"
    # Other fields are preserved.
    assert entry.address.prename == "Alice"


def test_addresses_update_clears_field_with_empty_string() -> None:
    _invoke(
        "addresses",
        "add",
        "alice",
        "--prename",
        "Alice",
        "--notes",
        "vacation 2024",
    )
    result = _invoke(
        "addresses",
        "update",
        "alice",
        "--notes",
        "",
    )
    assert result.exit_code == 0, result.output
    entry = load_address_book().get("alice")
    assert entry.notes == ""


def test_addresses_update_unknown_name_is_cli_error() -> None:
    result = _invoke(
        "addresses",
        "update",
        "ghost",
        "--place",
        "Bern",
    )
    assert result.exit_code == 2
    assert "no address-book entry named" in result.output


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_addresses_remove_deletes_entry() -> None:
    _invoke("addresses", "add", "alice", "--prename", "A", "--lastname", "B")
    result = _invoke("addresses", "remove", "alice", "--yes")
    assert result.exit_code == 0, result.output
    assert load_address_book().find("alice") is None


def test_addresses_remove_unknown_name_is_cli_error() -> None:
    result = _invoke("addresses", "remove", "ghost", "--yes")
    assert result.exit_code == 2
    assert "no address-book entry named" in result.output


def test_addresses_remove_prompts_for_confirmation() -> None:
    _invoke("addresses", "add", "alice", "--prename", "A", "--lastname", "B")
    # Without --yes and without feeding input, the prompt
    # defaults to "No" and the command exits 1 ("aborted").
    runner = CliRunner()
    result = runner.invoke(
        __import__("postcards.cli.app", fromlist=["app"]).app, ["addresses", "remove", "alice"]
    )
    assert result.exit_code != 0
    assert load_address_book().find("alice") is not None  # still present


def test_addresses_persistence_survives_reload(tmp_path: Path) -> None:
    """Round-trip: a save through the CLI is readable by the storage layer."""
    _invoke(
        "addresses",
        "add",
        "alice",
        "--prename",
        "Alice",
        "--lastname",
        "Z",
    )
    # The on-disk file should be valid JSON.
    target = tmp_path / "data" / "addressbook.json"
    assert target.is_file()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["entries"][0]["name"] == "alice"
