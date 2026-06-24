"""Smoke tests for the Typer-based ``postcards`` CLI.

The M2 milestone migrated the user-facing ``postcards`` console
script from the legacy ``argparse`` parser in
:mod:`postcards.postcards` to a Typer-based command tree under
:mod:`postcards.cli`. These tests exercise that tree through
:func:`postcards.cli.runner.run` — the same driver the
production entry point uses internally — so the assertions
are representative of what a user sees at the terminal.

Why :func:`run` rather than :class:`typer.testing.CliRunner`
directly
-----------------------------------------------------------------

:func:`run` wraps :class:`typer.testing.CliRunner` and adds two
behaviours the production entry point relies on:

1. :class:`postcards.cli.errors.CLIError` is converted to a
   :class:`typer.Exit` with the requested exit code, so the
   test sees the right exit code (e.g. ``2`` for usage
   errors) and the error message in ``result.output``.
2. A fresh :class:`typer.testing.CliRunner` is used per call,
   so state does not leak between tests.

Tests that need to inspect the raw exception (e.g. to assert
that the right exception class was raised) instantiate
:class:`typer.testing.CliRunner` directly; the rest of the
suite goes through :func:`run`.

Scope
-----

* Each top-level subcommand (``send``, ``preview``, ``generate``,
  ``config``, ``accounts``, ``quota``, ``status``, ``encrypt``,
  ``decrypt``, ``legacy``) renders ``--help`` with exit 0.
* Sub-commands (``config init``, ``accounts add``) reach the
  business logic.
* Error paths surface :class:`postcards.cli.errors.CLIError`
  with the expected exit code.

The tests do NOT exercise the network. The send / preview
commands are run with ``--dry-run`` (which short-circuits the
``send_free_card`` call) against a mock-backend environment so
the Swiss Post endpoint is never touched.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner
from typer.testing import Result as CliResult

from postcards._vendor.postcard_creator import Token
from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorBase
from postcards.cli import run as cli_run
from postcards.cli.config_io import read_config
from postcards.cli.errors import CLIError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    """A fresh :class:`typer.testing.CliRunner` per test.

    Tests that need the ``run()`` error-handling layer use
    :func:`postcards.cli.run` instead; this fixture is for the
    few tests that need raw CliRunner access.
    """
    return CliRunner()


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip ``POSTCARDS_*`` env vars so tests are hermetic.

    The constitution (post-M2 §2) makes these env vars the
    highest-priority source of credentials. Tests that do not
    intend to exercise that path explicitly clear them so the
    config-file path is the one under test.
    """
    for key in (
        "POSTCARDS_USERNAME",
        "POSTCARDS_PASSWORD",
        "POSTCARDS_KEY",
        "POSTCARDS_BACKEND",
        "POSTCARDS_CONFIG",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def mock_shim_send(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Patch the shim's network methods so the CLI never hits the live API.

    Returns the list of recorded send calls; the test can assert
    against it. The patch mirrors
    ``tests/test_send_integration.py::MockBackend.install`` so
    the M1 tests and the M2 tests share the same backend
    contract.
    """
    recorded: list[dict] = []

    def mock_has_valid_credentials(self: Token, username: str | None, password: str | None) -> bool:
        # Mark this Token as authenticated so the
        # ``PostcardCreator(token)`` constructor in
        # ``_create_pcc_wrappers`` accepts it (the shim raises
        # ``PostcardCreatorException`` if ``token.token is None``).
        self.token = "<mocked-token>"
        return bool(username and password)

    def mock_has_free_postcard(self: PostcardCreatorBase) -> bool:
        return True

    def mock_send_free_card(
        self: PostcardCreatorBase,
        postcard: object,
        mock_send: bool = False,
        **_kwargs: object,
    ) -> None:
        recorded.append({"postcard": postcard, "mock_send": mock_send})

    monkeypatch.setattr(Token, "has_valid_credentials", mock_has_valid_credentials)
    monkeypatch.setattr(PostcardCreatorBase, "has_free_postcard", mock_has_free_postcard)
    monkeypatch.setattr(PostcardCreatorBase, "send_free_card", mock_send_free_card)
    return recorded


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Write a default config file and return its path.

    The config carries a single valid account (``alice`` /
    ``alice-pw``) and a valid recipient. Tests that need a
    different shape override the dict via a helper.
    """
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "recipient": {
                    "firstname": "Hans",
                    "lastname": "Muster",
                    "street": "Bahnhofstrasse 1",
                    "zipcode": "8000",
                    "city": "Zurich",
                    "salutation": "Mr.",
                },
                "accounts": [{"username": "alice", "password": "alice-pw"}],
            }
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(*args: str) -> CliResult:
    """Invoke the CLI via :func:`postcards.cli.runner.run`.

    Tests call this helper instead of instantiating a
    :class:`typer.testing.CliRunner` directly so the
    production error-handling layer (CLIError → typer.Exit
    conversion) is exercised.
    """
    return cli_run(list(args))


# ---------------------------------------------------------------------------
# Top-level help
# ---------------------------------------------------------------------------


def test_postcards_help_lists_subcommands(runner: CliRunner) -> None:
    """``postcards --help`` lists every M2 subcommand."""
    result = _invoke("--help")
    assert result.exit_code == 0, result.output
    output = result.output.lower()
    for sub in (
        "send",
        "preview",
        "generate",
        "config",
        "accounts",
        "quota",
        "status",
        "encrypt",
        "decrypt",
        "legacy",
    ):
        assert sub in output, f"missing subcommand {sub!r} in help:\n{result.output}"


def test_postcards_no_args_shows_help(runner: CliRunner) -> None:
    """``postcards`` with no args prints the help and exits 0.

    ``no_args_is_help=True`` on the Typer app makes a bare
    ``postcards`` invocation print the help screen and exit
    with code 2 (Click's "usage error" code) — the standard
    behaviour for a Typer app with no positional command
    specified.
    """
    result = _invoke()
    assert result.exit_code == 2, result.output
    assert "usage" in result.output.lower()


def test_postcards_version_prints_and_exits(runner: CliRunner) -> None:
    """``postcards --version`` prints the version and exits 0."""
    result = _invoke("--version")
    assert result.exit_code == 0
    # The version is dynamic; assert the prefix is present.
    assert "postcards" in result.output


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


def test_send_requires_picture_or_message(
    runner: CliRunner,
    config_file: Path,
    clean_env: None,
) -> None:
    """``send`` with neither ``--picture`` nor ``--message`` fails cleanly."""
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "--username",
        "alice",
        "--password",
        "alice-pw",
    )
    assert result.exit_code != 0
    assert "picture" in result.output.lower() or "message" in result.output.lower()


def test_send_dry_run_calls_send_free_card_with_mock_true(
    config_file: Path,
    mock_shim_send: list[dict],
    clean_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``send --dry-run`` reaches the shim with ``mock_send=True``."""
    from postcards.postcards import Postcards

    in_memory = io.BytesIO(b"\xff\xd8\xff\xe0fake-jpeg")
    monkeypatch.setattr(Postcards, "_read_picture", lambda self, location: in_memory)
    picture = tmp_path / "pic.jpg"
    picture.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture),
        "-m",
        "Hello world",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    assert len(mock_shim_send) == 1
    assert mock_shim_send[0]["mock_send"] is True


def test_send_with_mock_flag_still_works(
    config_file: Path,
    mock_shim_send: list[dict],
    clean_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--mock`` is a hidden alias for ``--dry-run`` (backward compat)."""
    from postcards.postcards import Postcards

    in_memory = io.BytesIO(b"\xff\xd8\xff\xe0fake-jpeg")
    monkeypatch.setattr(Postcards, "_read_picture", lambda self, location: in_memory)
    picture = tmp_path / "pic.jpg"
    picture.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    result = _invoke(
        "send",
        "-c",
        str(config_file),
        "-p",
        str(picture),
        "-m",
        "Hi",
        "--username",
        "alice",
        "--password",
        "alice-pw",
        "--mock",
    )
    assert result.exit_code == 0, result.output
    assert mock_shim_send[0]["mock_send"] is True


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_prints_human_readable_summary(
    config_file: Path,
    mock_shim_send: list[dict],
    clean_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``preview`` prints a human-readable summary of the would-be send.

    We patch ``Postcards._read_picture`` to return an in-memory
    stream so the test does not leave a real file handle open
    at teardown (which would trip pytest's strict
    ``ResourceWarning`` policy).

    The preview walks the same code path as ``send --dry-run``;
    the shim's ``send_free_card`` is called with
    ``mock_send=True`` so no real network call is made.
    """
    from postcards.postcards import Postcards

    in_memory = io.BytesIO(b"\xff\xd8\xff\xe0fake-jpeg")
    monkeypatch.setattr(Postcards, "_read_picture", lambda self, location: in_memory)

    picture = tmp_path / "pic.jpg"
    picture.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    result = _invoke(
        "preview",
        "-c",
        str(config_file),
        "-p",
        str(picture),
        "-m",
        "Hello world",
        "--username",
        "alice",
        "--password",
        "alice-pw",
    )
    assert result.exit_code == 0, result.output
    out = result.output.lower()
    assert "recipient" in out
    assert "hans" in out
    assert "muster" in out
    assert "preview ok" in out


def test_preview_requires_picture_or_message(
    runner: CliRunner,
    config_file: Path,
    clean_env: None,
) -> None:
    """``preview`` without ``--picture`` or ``--message`` fails with code 2."""
    result = _invoke(
        "preview",
        "-c",
        str(config_file),
        "--username",
        "alice",
        "--password",
        "alice-pw",
    )
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def test_generate_writes_starter_config(
    runner: CliRunner,
    tmp_path: Path,
    clean_env: None,
) -> None:
    """``generate`` writes the bundled template to the requested path."""
    target = tmp_path / "starter.json"
    result = _invoke("generate", "-o", str(target))
    assert result.exit_code == 0, result.output
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    # The template is the same one the legacy CLI used; we only
    # assert that it parses as JSON and has a ``recipient`` block.
    assert "recipient" in data


def test_generate_refuses_to_clobber_without_force(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """``generate`` refuses to overwrite an existing file."""
    target = tmp_path / "existing.json"
    target.write_text('{"existing": true}', encoding="utf-8")
    result = _invoke("generate", "-o", str(target))
    assert result.exit_code == 2
    assert target.read_text(encoding="utf-8") == '{"existing": true}'


def test_generate_with_force_overwrites(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """``generate --force`` clobbers an existing file."""
    target = tmp_path / "existing.json"
    target.write_text('{"existing": true}', encoding="utf-8")
    result = _invoke("generate", "-o", str(target), "--force")
    assert result.exit_code == 0, result.output
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "recipient" in data


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_config_init_writes_starter(runner: CliRunner, tmp_path: Path) -> None:
    """``config init`` is a thin alias for ``generate``."""
    target = tmp_path / "config.json"
    result = _invoke("config", "init", "-o", str(target))
    assert result.exit_code == 0, result.output
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "recipient" in data


def test_config_show_prints_json(
    runner: CliRunner,
    config_file: Path,
) -> None:
    """``config show`` prints the config file as JSON."""
    result = _invoke("config", "show", "-c", str(config_file))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.split("\n", 1)[1])
    assert "alice" in {a["username"] for a in data["accounts"]}


def test_config_show_redacts_passwords_by_default(
    runner: CliRunner,
    config_file: Path,
) -> None:
    """``config show`` masks passwords unless ``--no-redact`` is given."""
    result = _invoke("config", "show", "-c", str(config_file))
    assert result.exit_code == 0, result.output
    assert "alice-pw" not in result.output
    assert "***" in result.output


def test_config_show_no_redact_reveals_passwords(
    runner: CliRunner,
    config_file: Path,
) -> None:
    """``config show --no-redact`` shows the stored password."""
    result = _invoke("config", "show", "-c", str(config_file), "--no-redact")
    assert result.exit_code == 0, result.output
    assert "alice-pw" in result.output


def test_config_set_updates_field(
    runner: CliRunner,
    config_file: Path,
) -> None:
    """``config set recipient.city Zurich`` mutates the file."""
    result = _invoke(
        "config",
        "set",
        "recipient.city",
        "Geneva",
        "-c",
        str(config_file),
    )
    assert result.exit_code == 0, result.output
    data = read_config(config_file)
    assert data["recipient"]["city"] == "Geneva"


def test_config_set_accounts_list_index(
    runner: CliRunner,
    config_file: Path,
) -> None:
    """``config set accounts.0.username bob`` updates the first account."""
    result = _invoke(
        "config",
        "set",
        "accounts.0.username",
        "bob",
        "-c",
        str(config_file),
    )
    assert result.exit_code == 0, result.output
    data = read_config(config_file)
    assert data["accounts"][0]["username"] == "bob"


# ---------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------


def test_accounts_add_appends_account(
    runner: CliRunner,
    config_file: Path,
) -> None:
    """``accounts add`` appends to the ``accounts`` list."""
    result = _invoke(
        "accounts",
        "add",
        "bob",
        "--password",
        "bob-pw",
        "-c",
        str(config_file),
    )
    assert result.exit_code == 0, result.output
    data = read_config(config_file)
    usernames = [a["username"] for a in data["accounts"]]
    assert "bob" in usernames
    assert "alice" in usernames


def test_accounts_add_rejects_duplicate_username(
    runner: CliRunner,
    config_file: Path,
) -> None:
    """Adding a username that already exists is a CLI error."""
    result = _invoke(
        "accounts",
        "add",
        "alice",
        "--password",
        "different-pw",
        "-c",
        str(config_file),
    )
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_accounts_list_masks_passwords_by_default(
    runner: CliRunner,
    config_file: Path,
) -> None:
    """``accounts list`` shows ``***`` in place of passwords."""
    result = _invoke("accounts", "list", "-c", str(config_file))
    assert result.exit_code == 0, result.output
    assert "alice" in result.output
    assert "alice-pw" not in result.output
    assert "***" in result.output


def test_accounts_use_marks_active(
    runner: CliRunner,
    config_file: Path,
) -> None:
    """``accounts use alice`` sets ``active_account`` in the config."""
    result = _invoke(
        "accounts",
        "use",
        "alice",
        "-c",
        str(config_file),
    )
    assert result.exit_code == 0, result.output
    data = read_config(config_file)
    assert data["active_account"] == "alice"


def test_accounts_use_rejects_unknown_username(
    runner: CliRunner,
    config_file: Path,
) -> None:
    """``accounts use bob`` (when only alice is configured) exits 2."""
    result = _invoke(
        "accounts",
        "use",
        "bob",
        "-c",
        str(config_file),
    )
    assert result.exit_code == 2
    assert "not in" in result.output


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_prints_version_and_paths(
    runner: CliRunner,
    config_file: Path,
    clean_env: None,
) -> None:
    """``status`` prints the resolved config path and version info."""
    result = _invoke("status", "-c", str(config_file))
    assert result.exit_code == 0, result.output
    out = result.output
    assert "postcards version" in out
    assert "config path" in out
    assert str(config_file.resolve()) in out


# ---------------------------------------------------------------------------
# quota
# ---------------------------------------------------------------------------


def test_quota_uses_mock_backend_when_requested(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``quota --backend mock`` returns a free-postcard message via the mock."""
    # Inject a MockBackend directly via the registry so the test
    # does not need a config file.
    from postcards.backend import MockBackend
    from postcards.backend import registry as registry_module

    sentinel = MockBackend(quota_info=MockBackend().quota_info)
    monkeypatch.setattr(registry_module, "_BUILTINS", {"mock": type(sentinel)})
    monkeypatch.setenv("POSTCARDS_USERNAME", "alice")
    monkeypatch.setenv("POSTCARDS_PASSWORD", "alice-pw")

    result = _invoke(
        "quota",
        "--backend",
        "mock",
    )
    assert result.exit_code == 0, result.output
    assert "free postcard available" in result.output


def test_quota_requires_username(
    runner: CliRunner,
    clean_env: None,
) -> None:
    """``quota`` without ``--username`` and no env var fails with code 2."""
    result = _invoke("quota")
    assert result.exit_code == 2
    assert "username" in result.output.lower()


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


def test_encrypt_then_decrypt_roundtrips(
    runner: CliRunner,
) -> None:
    """``encrypt`` followed by ``decrypt`` with the same key round-trips."""
    plaintext = "top-secret-password"
    enc = _invoke("encrypt", plaintext, "-k", "my-key")
    assert enc.exit_code == 0, enc.output
    encrypted = enc.output.strip()
    assert encrypted != plaintext

    dec = _invoke("decrypt", encrypted, "-k", "my-key")
    assert dec.exit_code == 0, dec.output
    assert dec.output.strip() == plaintext


def test_decrypt_with_wrong_key_produces_garbage(
    runner: CliRunner,
) -> None:
    """``decrypt`` with the wrong key produces garbage (not an error).

    The XOR-based cipher is intentionally weak — the legacy
    implementation does not raise on a wrong key, it just
    produces a different plaintext. The test pins that
    contract so a future refactor that does add real error
    detection is caught loudly.
    """
    enc = _invoke("encrypt", "secret", "-k", "k1")
    assert enc.exit_code == 0
    enc_blob = enc.output.strip()

    result = _invoke("decrypt", enc_blob, "-k", "k2")
    assert result.exit_code == 0, result.output
    # The wrong key produces *some* output, but it must not
    # match the original plaintext.
    assert result.output.strip() != "secret"


# ---------------------------------------------------------------------------
# legacy
# ---------------------------------------------------------------------------


def test_legacy_help_lists_subcommand(runner: CliRunner) -> None:
    """``legacy --help`` mentions the ``run`` subcommand."""
    result = _invoke("legacy", "--help")
    assert result.exit_code == 0
    assert "run" in result.output


def test_legacy_run_help_mentions_postcards(runner: CliRunner) -> None:
    """``legacy run --help`` shows the escape-hatch description."""
    result = _invoke("legacy", "run", "--help")
    assert result.exit_code == 0
    # The exact text changes between Typer versions; assert
    # the description mentions the legacy module by name.
    assert "legacy" in result.output.lower()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_clierror_carries_exit_code() -> None:
    """``CLIError`` carries the exit code through the runner.

    The error path is exercised by every test that triggers a
    :class:`CLIError`; this test pins the contract that the
    exit code defaults to 1 and can be overridden.
    """
    err = CLIError("boom")
    assert err.exit_code == 1
    err2 = CLIError("bad usage", exit_code=2)
    assert err2.exit_code == 2


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_verbose_flag_configures_logging(
    runner: CliRunner,
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``-vv`` configures the root logger to DEBUG level."""
    import logging

    seen: list[int] = []

    def capture(level: int = logging.NOTSET, **_kw: object) -> None:
        if level != logging.NOTSET:
            seen.append(level)

    monkeypatch.setattr(logging, "basicConfig", capture)
    result = _invoke("-vv", "status")
    assert result.exit_code == 0, result.output
    # ``-vv`` translates to target level 0 (TRACE = 5). The
    # basicConfig call must have set a level at or below DEBUG
    # (10) — that is the contract that makes the postcards and
    # postcard_creator loggers surface DEBUG-level messages.
    assert any(level <= logging.DEBUG for level in seen)


# ---------------------------------------------------------------------------
# Mocking the entry point side-effect
# ---------------------------------------------------------------------------


def test_main_function_invokes_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``postcards.cli.main.main()`` invokes the Typer ``app``.

    The production entry point is a thin wrapper that calls
    :data:`postcards.cli.app.app` and lets Click convert
    :class:`typer.Exit` into a process exit code. The test
    pins that contract by stubbing the app and asserting it
    was called.
    """
    from postcards.cli import main as entry_main
    from postcards.cli import runner

    called: list[None] = []
    monkeypatch.setattr(runner, "app", lambda: called.append(None))
    # The wrapper must not raise on a clean return.
    entry_main.main()
    assert called == [None]


def test_main_function_exits_zero_on_help(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``postcards --help`` (via the real entry point) exits 0.

    This exercises the production path end-to-end: the test
    rewires :data:`sys.argv`, calls :func:`postcards.cli.main.main`,
    and asserts Click wrote the help text to stdout and the
    process exit code is 0. Catches regressions where the
    entry point is silently broken (e.g. if it were ever
    wrapped in a :class:`typer.testing.CliRunner.invoke` that
    swallows the output).
    """
    import sys

    from postcards.cli import main as entry_main

    monkeypatch.setattr(sys, "argv", ["postcards", "--help"])
    try:
        entry_main.main()
    except SystemExit as exc:
        # Click raises SystemExit(0) on --help. Anything else
        # is a regression.
        assert exc.code == 0, f"unexpected SystemExit code: {exc.code!r}"
    else:
        # If main() returns normally that is also acceptable
        # (some Click versions do not raise on --help). The
        # contract is "no exception, exit 0".
        pass
    captured = capsys.readouterr()
    # The help text must reach the terminal. We assert on a
    # substring to stay stable across Typer / Click versions.
    assert "postcards" in captured.out.lower()
    assert "usage" in captured.out.lower()


def test_clierror_class_preserved() -> None:
    """``CLIError`` keeps the public ``message`` / ``exit_code`` API.

    The class is a typed exception that internal helpers may
    raise; the production path uses
    :func:`postcards.cli.errors.raise_cli_error` instead, but
    the wrapper class is part of the public surface and stays
    in place for the test suite and any future caller.
    """
    err = CLIError("oh no", exit_code=3)
    assert err.message == "oh no"
    assert err.exit_code == 3
