"""Tests for :class:`postcards.config.ConfigLayer`.

The config layer is the single point of truth for credential and
address resolution (see ``docs/CONSTITUTION.md`` §2). These tests
cover the resolution-order contract:

1. CLI override (``--username`` / ``--password``).
2. ``POSTCARDS_USERNAME`` + ``POSTCARDS_PASSWORD`` env vars.
3. ``POSTCARDS_USERNAME`` + keyring lookup.
4. Config-file ``accounts`` list.
5. ``POSTCARDS_BACKEND`` env / ``backend`` field for the backend name.

They also assert that no plaintext password ever lives in a string
the loader returns without going through one of those sources.

Hermetic
--------

The tests inject a custom ``env`` mapping and a custom ``config_path``
pointing at a ``tmp_path``-based fixture. They never touch
``os.environ`` or the real keyring — the keyring is mocked via the
``keyring_backend=`` constructor argument.

No live network is exercised. No secrets enter the repository.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from postcards.backend.base import AddressSpec
from postcards.config import AccountConfig, ConfigError, ConfigLayer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Write a default config file and return its path."""
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
                "sender": {
                    "firstname": "Maria",
                    "lastname": "Muster",
                    "street": "Bahnhofstrasse 1",
                    "zipcode": "8000",
                    "city": "Zurich",
                },
                "accounts": [
                    {"username": "alice", "password": "alice-secret"},
                    {"username": "bob", "password": "bob-secret"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def env() -> dict[str, str]:
    """Empty env mapping for tests; individual tests override keys."""
    return {}


@pytest.fixture
def fake_keyring() -> Iterator[Any]:
    """A minimal keyring stand-in that records calls and answers one lookup.

    The stand-in implements the ``get_password(service, username)``
    contract the ConfigLayer relies on. Tests mutate
    ``fake_keyring.answers`` to control which lookups succeed.
    """

    class _FakeKeyring:
        def __init__(self) -> None:
            self.answers: dict[tuple[str, str], str] = {}
            self.calls: list[tuple[str, str]] = []

        def get_password(self, service: str, username: str) -> str | None:
            self.calls.append((service, username))
            return self.answers.get((service, username))

        def set_password(self, service: str, username: str, value: str) -> None:
            self.answers[(service, username)] = value

    fake = _FakeKeyring()
    yield fake


# ---------------------------------------------------------------------------
# CLI override
# ---------------------------------------------------------------------------


def test_cli_override_short_circuits_every_other_source(
    config_file: Path, env: dict[str, str]
) -> None:
    """``--username`` / ``--password`` on the CLI bypass env / keyring / file."""
    layer = ConfigLayer(env=env, config_path=config_file)
    accounts = layer.load_accounts(username_override="cli-user", password_override="cli-pass")
    assert len(accounts) == 1
    assert accounts[0] == AccountConfig(username="cli-user", password="cli-pass", source="cli")


def test_cli_override_with_only_username_falls_through(
    config_file: Path, env: dict[str, str]
) -> None:
    """A username override without a password falls through to the config file."""
    layer = ConfigLayer(env=env, config_path=config_file)
    # The config file's accounts[] is the only source consulted.
    accounts = layer.load_accounts(username_override="cli-user", password_override="")
    usernames = [a.username for a in accounts]
    assert "alice" in usernames
    assert "bob" in usernames


# ---------------------------------------------------------------------------
# Env precedence
# ---------------------------------------------------------------------------


def test_postcards_username_with_env_password_returns_env_source(
    config_file: Path,
) -> None:
    """``POSTCARDS_USERNAME`` + ``POSTCARDS_PASSWORD`` → ``source="env"``."""
    layer = ConfigLayer(
        env={"POSTCARDS_USERNAME": "alice", "POSTCARDS_PASSWORD": "from-env"},
        config_path=config_file,
    )
    accounts = layer.load_accounts()
    assert len(accounts) == 1
    assert accounts[0].source == "env"
    assert accounts[0].username == "alice"
    assert accounts[0].password == "from-env"


def test_postcards_username_falls_through_to_keyring(
    config_file: Path,
    fake_keyring: Any,
) -> None:
    """When ``POSTCARDS_PASSWORD`` is unset, the keyring is consulted next."""
    fake_keyring.set_password("postcards", "alice", "from-keyring")
    layer = ConfigLayer(
        env={"POSTCARDS_USERNAME": "alice"},
        config_path=config_file,
        keyring_backend=fake_keyring,
    )
    accounts = layer.load_accounts()
    assert len(accounts) == 1
    assert accounts[0].source == "keyring"
    assert accounts[0].password == "from-keyring"
    # The loader records which username it looked up so the test can
    # assert that the keyring was actually consulted.
    assert ("postcards", "alice") in fake_keyring.calls


def test_postcards_username_falls_through_to_config_file(
    config_file: Path,
) -> None:
    """When env and keyring are silent, the config-file ``accounts`` list wins."""
    layer = ConfigLayer(
        env={"POSTCARDS_USERNAME": "bob"},
        config_path=config_file,
    )
    accounts = layer.load_accounts()
    assert len(accounts) == 1
    assert accounts[0].source == "config_file"
    assert accounts[0].username == "bob"
    assert accounts[0].password == "bob-secret"


def test_postcards_username_without_password_raises(
    config_file: Path,
    fake_keyring: Any,
) -> None:
    """``POSTCARDS_USERNAME`` with no resolvable password raises ``ConfigError``."""
    layer = ConfigLayer(
        env={"POSTCARDS_USERNAME": "nobody"},
        config_path=config_file,
        keyring_backend=fake_keyring,
    )
    with pytest.raises(ConfigError, match="POSTCARDS_USERNAME"):
        layer.load_accounts()


# ---------------------------------------------------------------------------
# Multi-account path (no env vars)
# ---------------------------------------------------------------------------


def test_no_env_returns_all_config_file_accounts(config_file: Path, env: dict[str, str]) -> None:
    """Without env overrides, the loader returns every account in the file."""
    layer = ConfigLayer(env=env, config_path=config_file)
    accounts = layer.load_accounts()
    usernames = [a.username for a in accounts]
    assert sorted(usernames) == ["alice", "bob"]
    assert all(a.source == "config_file" for a in accounts)


def test_account_missing_username_raises(tmp_path: Path, env: dict[str, str]) -> None:
    """A config-file account without a username is a hard error."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"accounts": [{"password": "x"}]}),
        encoding="utf-8",
    )
    layer = ConfigLayer(env=env, config_path=path)
    with pytest.raises(ConfigError, match="username"):
        layer.load_accounts()


def test_no_env_no_accounts_raises(tmp_path: Path, env: dict[str, str]) -> None:
    """Neither env nor config-file accounts is a hard error."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"recipient": {}, "accounts": []}), encoding="utf-8")
    layer = ConfigLayer(env=env, config_path=path)
    with pytest.raises(ConfigError, match="no accounts"):
        layer.load_accounts()


def test_missing_config_file_is_not_an_error_when_env_provided(
    env: dict[str, str],
) -> None:
    """A missing config file is fine when env vars supply the credentials."""
    layer = ConfigLayer(
        env={"POSTCARDS_USERNAME": "alice", "POSTCARDS_PASSWORD": "pw"},
        config_path=Path("/nonexistent/config.json"),
    )
    accounts = layer.load_accounts()
    assert len(accounts) == 1
    assert accounts[0].source == "env"


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


def test_load_recipient_returns_address_spec(config_file: Path) -> None:
    """``load_recipient`` parses the recipient block into an ``AddressSpec``."""
    layer = ConfigLayer(env={}, config_path=config_file)
    recipient = layer.load_recipient()
    assert isinstance(recipient, AddressSpec)
    assert recipient.prename == "Hans"
    assert recipient.lastname == "Muster"
    assert recipient.place == "Zurich"
    assert recipient.salutation == "Mr."
    assert recipient.zip_code == "8000"


def test_load_recipient_missing_required_field_raises(
    tmp_path: Path,
) -> None:
    """A recipient block missing any required field raises ``ConfigError``."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"recipient": {"firstname": "Hans"}}),
        encoding="utf-8",
    )
    layer = ConfigLayer(env={}, config_path=path)
    with pytest.raises(ConfigError, match="recipient"):
        layer.load_recipient()


def test_load_recipient_missing_block_raises(tmp_path: Path) -> None:
    """A config file with no recipient block raises ``ConfigError``."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"sender": {}}), encoding="utf-8")
    layer = ConfigLayer(env={}, config_path=path)
    with pytest.raises(ConfigError, match="recipient"):
        layer.load_recipient()


def test_load_sender_returns_none_when_missing(tmp_path: Path) -> None:
    """A config file without a sender block returns ``None`` (fall back to recipient)."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "recipient": {
                    "firstname": "H",
                    "lastname": "M",
                    "street": "S",
                    "zipcode": "1",
                    "city": "C",
                }
            }
        ),
        encoding="utf-8",
    )
    layer = ConfigLayer(env={}, config_path=path)
    assert layer.load_sender() is None


def test_load_sender_returns_address_spec(config_file: Path) -> None:
    """``load_sender`` parses the sender block into an ``AddressSpec``."""
    layer = ConfigLayer(env={}, config_path=config_file)
    sender = layer.load_sender()
    assert sender is not None
    assert sender.prename == "Maria"


# ---------------------------------------------------------------------------
# Backend name
# ---------------------------------------------------------------------------


def test_load_backend_name_env_wins_over_config(tmp_path: Path) -> None:
    """``POSTCARDS_BACKEND`` env var beats the config-file ``backend`` field."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"backend": "mock"}), encoding="utf-8")
    layer = ConfigLayer(
        env={"POSTCARDS_BACKEND": "swissid"},
        config_path=path,
    )
    assert layer.load_backend_name() == "swissid"


def test_load_backend_name_from_config_when_env_missing(tmp_path: Path) -> None:
    """Without env, the config-file ``backend`` field is consulted."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"backend": "mock"}), encoding="utf-8")
    layer = ConfigLayer(env={}, config_path=path)
    assert layer.load_backend_name() == "mock"


def test_load_backend_name_returns_none_when_unset(tmp_path: Path) -> None:
    """Neither source set → ``None`` so the registry falls back to its default."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"recipient": {}}), encoding="utf-8")
    layer = ConfigLayer(env={}, config_path=path)
    assert layer.load_backend_name() is None


# ---------------------------------------------------------------------------
# Config file parsing
# ---------------------------------------------------------------------------


def test_invalid_json_raises(tmp_path: Path) -> None:
    """A config file that is not valid JSON raises ``ConfigError``."""
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")
    layer = ConfigLayer(env={}, config_path=path)
    with pytest.raises(ConfigError, match="parse"):
        layer.load_accounts()


def test_non_object_root_raises(tmp_path: Path) -> None:
    """A config file whose root is not an object raises ``ConfigError``."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    layer = ConfigLayer(env={}, config_path=path)
    with pytest.raises(ConfigError, match="JSON object"):
        layer.load_accounts()


def test_postcards_key_propagates_to_account_config(
    config_file: Path,
) -> None:
    """``POSTCARDS_KEY`` is forwarded to ``AccountConfig.key``."""
    layer = ConfigLayer(
        env={"POSTCARDS_KEY": "rotate-key"},
        config_path=config_file,
    )
    accounts = layer.load_accounts()
    assert all(a.key == "rotate-key" for a in accounts)


# ---------------------------------------------------------------------------
# Default config-path resolution
# ---------------------------------------------------------------------------


def test_default_config_path_is_cwd_config_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no ``config_path`` and no ``POSTCARDS_CONFIG``, the loader uses ``./config.json``."""
    monkeypatch.chdir(tmp_path)
    layer = ConfigLayer(env={})
    assert layer.config_path_resolved() == tmp_path / "config.json"


def test_postcards_config_env_overrides_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``POSTCARDS_CONFIG`` overrides the default location."""
    monkeypatch.chdir(tmp_path)
    alt = tmp_path / "alt.json"
    layer = ConfigLayer(env={"POSTCARDS_CONFIG": str(alt)})
    assert layer.config_path_resolved() == alt


# ---------------------------------------------------------------------------
# No plaintext-in-repo invariant (defensive)
# ---------------------------------------------------------------------------


def test_loader_does_not_echo_password_when_block_missing(
    tmp_path: Path,
) -> None:
    """An absent ``accounts`` block plus no env returns no credentials at all.

    This is the negative form of the no-plaintext-in-repo invariant:
    the loader never invents a credential, and a config file that
    does not carry them produces an explicit ``ConfigError`` rather
    than a fabricated string.
    """
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"recipient": {}}), encoding="utf-8")
    layer = ConfigLayer(env={}, config_path=path)
    with pytest.raises(ConfigError):
        layer.load_accounts()


def test_account_config_is_valid_requires_username_and_password() -> None:
    """``AccountConfig.is_valid`` rejects an empty username or password."""
    assert AccountConfig(username="u", password="p").is_valid() is True
    assert AccountConfig(username="", password="p").is_valid() is False
    assert AccountConfig(username="u", password="").is_valid() is False
