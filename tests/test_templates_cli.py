"""Tests for the ``postcards templates`` Typer command group.

The command group owns the user-facing surface for the
:class:`postcards.addressbook.models.TemplateBook`. These
tests exercise every subcommand through
:func:`postcards.cli.runner.run` and pin the storage layer to
``tmp_path`` via :data:`POSTCARDS_DATA_DIR`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner
from typer.testing import Result as CliResult

from postcards.addressbook.storage import load_template_book
from postcards.cli import run as cli_run

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolate_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("POSTCARDS_DATA_DIR", str(tmp_path / "data"))
    yield


def _invoke(*args: str) -> CliResult:
    return cli_run(list(args))


# ---------------------------------------------------------------------------
# Help / smoke
# ---------------------------------------------------------------------------


def test_templates_help_lists_subcommands() -> None:
    result = _invoke("templates", "--help")
    assert result.exit_code == 0, result.output
    out = result.output.lower()
    for sub in ("add", "list", "show", "update", "render", "remove"):
        assert sub in out, f"missing subcommand {sub!r} in 'templates --help':\n{result.output}"


def test_templates_no_args_shows_help() -> None:
    result = _invoke("templates")
    assert result.exit_code == 2, result.output


def test_postcards_top_level_help_includes_templates() -> None:
    result = _invoke("--help")
    assert result.exit_code == 0, result.output
    assert "templates" in result.output


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_templates_add_creates_template() -> None:
    result = _invoke(
        "templates",
        "add",
        "greeting",
        "--description",
        "default greeting",
        "--body",
        "Hi $name!",
    )
    assert result.exit_code == 0, result.output
    template = load_template_book().get("greeting")
    assert template.body == "Hi $name!"
    assert template.description == "default greeting"


def test_templates_add_reads_body_from_file(tmp_path: Path) -> None:
    body_path = tmp_path / "body.txt"
    body_path.write_text("Hello ${name}, greetings from Zurich", encoding="utf-8")
    result = _invoke(
        "templates",
        "add",
        "greeting",
        "--file",
        str(body_path),
    )
    assert result.exit_code == 0, result.output
    assert load_template_book().get("greeting").body == "Hello ${name}, greetings from Zurich"


def test_templates_add_requires_body_or_file() -> None:
    result = _invoke("templates", "add", "greeting")
    assert result.exit_code == 2
    assert "either --body or --file" in result.output


def test_templates_add_rejects_body_and_file_together(tmp_path: Path) -> None:
    body_path = tmp_path / "body.txt"
    body_path.write_text("Hi", encoding="utf-8")
    result = _invoke(
        "templates",
        "add",
        "greeting",
        "--body",
        "Hi",
        "--file",
        str(body_path),
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_templates_add_rejects_invalid_name() -> None:
    result = _invoke(
        "templates",
        "add",
        "Greeting",
        "--body",
        "Hi",
    )
    assert result.exit_code == 2
    assert "name" in result.output.lower()


def test_templates_add_rejects_duplicate() -> None:
    first = _invoke("templates", "add", "greeting", "--body", "Hi")
    assert first.exit_code == 0
    second = _invoke("templates", "add", "greeting", "--body", "Hi!")
    assert second.exit_code == 2
    assert "already exists" in second.output


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_templates_list_empty_book_prints_hint() -> None:
    result = _invoke("templates", "list")
    assert result.exit_code == 0, result.output
    assert "no templates" in result.output.lower()


def test_templates_list_prints_table() -> None:
    _invoke("templates", "add", "greeting", "--description", "default", "--body", "Hi $name")
    _invoke("templates", "add", "birthday", "--body", "Happy birthday $name")
    result = _invoke("templates", "list")
    assert result.exit_code == 0, result.output
    assert "greeting" in result.output
    assert "birthday" in result.output
    assert "default" in result.output


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_templates_show_prints_body_and_description() -> None:
    _invoke(
        "templates",
        "add",
        "greeting",
        "--description",
        "default",
        "--body",
        "Hi $name!",
    )
    result = _invoke("templates", "show", "greeting")
    assert result.exit_code == 0, result.output
    assert "greeting" in result.output
    assert "Hi $name!" in result.output
    assert "default" in result.output


def test_templates_show_unknown_is_cli_error() -> None:
    result = _invoke("templates", "show", "ghost")
    assert result.exit_code == 2
    assert "no template named 'ghost'" in result.output


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_templates_update_replaces_body() -> None:
    _invoke("templates", "add", "greeting", "--body", "Hi $name")
    result = _invoke(
        "templates",
        "update",
        "greeting",
        "--body",
        "Hello $name!",
    )
    assert result.exit_code == 0, result.output
    assert load_template_book().get("greeting").body == "Hello $name!"


def test_templates_update_clears_description() -> None:
    _invoke("templates", "add", "greeting", "--description", "old", "--body", "Hi")
    result = _invoke(
        "templates",
        "update",
        "greeting",
        "--description",
        "",
    )
    assert result.exit_code == 0, result.output
    assert load_template_book().get("greeting").description == ""


def test_templates_update_rejects_no_fields() -> None:
    _invoke("templates", "add", "greeting", "--body", "Hi")
    result = _invoke("templates", "update", "greeting")
    assert result.exit_code == 2
    assert "no fields to update" in result.output


def test_templates_update_rejects_body_and_file_together(tmp_path: Path) -> None:
    _invoke("templates", "add", "greeting", "--body", "Hi")
    body_path = tmp_path / "body.txt"
    body_path.write_text("Hi", encoding="utf-8")
    result = _invoke(
        "templates",
        "update",
        "greeting",
        "--body",
        "Hi!",
        "--file",
        str(body_path),
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_templates_update_unknown_is_cli_error() -> None:
    result = _invoke(
        "templates",
        "update",
        "ghost",
        "--description",
        "x",
    )
    assert result.exit_code == 2
    assert "no template named" in result.output


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def test_templates_render_substitutes_variables() -> None:
    _invoke("templates", "add", "greeting", "--body", "Hi $name, from $city")
    result = _invoke(
        "templates",
        "render",
        "greeting",
        "--var",
        "name=Alice",
        "--var",
        "city=Zurich",
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "Hi Alice, from Zurich"


def test_templates_render_with_no_variables_keeps_placeholders() -> None:
    _invoke("templates", "add", "greeting", "--body", "Hi $name")
    result = _invoke("templates", "render", "greeting")
    assert result.exit_code == 2
    assert "undefined variable" in result.output


def test_templates_render_rejects_malformed_var() -> None:
    _invoke("templates", "add", "greeting", "--body", "Hi $name")
    result = _invoke(
        "templates",
        "render",
        "greeting",
        "--var",
        "no-equals-sign",
    )
    assert result.exit_code == 2
    assert "malformed" in result.output


def test_templates_render_rejects_empty_key() -> None:
    _invoke("templates", "add", "greeting", "--body", "Hi $name")
    result = _invoke(
        "templates",
        "render",
        "greeting",
        "--var",
        "=value",
    )
    assert result.exit_code == 2
    assert "empty key" in result.output


def test_templates_render_unknown_is_cli_error() -> None:
    result = _invoke("templates", "render", "ghost")
    assert result.exit_code == 2
    assert "no template named 'ghost'" in result.output


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_templates_remove_deletes_template() -> None:
    _invoke("templates", "add", "greeting", "--body", "Hi")
    result = _invoke("templates", "remove", "greeting", "--yes")
    assert result.exit_code == 0, result.output
    assert load_template_book().find("greeting") is None


def test_templates_remove_unknown_is_cli_error() -> None:
    result = _invoke("templates", "remove", "ghost", "--yes")
    assert result.exit_code == 2
    assert "no template named" in result.output


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_templates_persistence_survives_reload(tmp_path: Path) -> None:
    _invoke(
        "templates",
        "add",
        "greeting",
        "--description",
        "default",
        "--body",
        "Hi $name",
    )
    target = tmp_path / "data" / "templates.json"
    assert target.is_file()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["templates"][0]["name"] == "greeting"
    assert payload["templates"][0]["body"] == "Hi $name"
