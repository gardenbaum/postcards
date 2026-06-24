"""Typed configuration loader.

The loader encapsulates the three resolution sources defined in
``docs/CONSTITUTION.md`` §2:

1. **Environment variables** — ``POSTCARDS_USERNAME``, ``POSTCARDS_PASSWORD``,
   ``POSTCARDS_BACKEND``, ``POSTCARDS_CONFIG``, ``POSTCARDS_KEY``.
   These take precedence over everything else so a CI job, a
   container, or a user with shell-set credentials always wins.
2. **OS keyring** — accessed via the optional ``keyring`` PyPI package
   under the service name ``"postcards"``. The keyring is consulted
   only when the ``keyring`` package is importable; if it is missing
   the loader silently skips that source (so a minimal install still
   works on hosts without a keyring backend).
3. **Config file** — a JSON document whose location is taken from
   ``POSTCARDS_CONFIG`` or, by default, ``./config.json``. The file is
   expected to be gitignored; plaintext credentials are accepted only
   when no ``POSTCARDS_KEY`` is configured (see §2.3 — encrypted
   credentials are fine in tracked config files).

Resolution algorithm
--------------------

For ``load_accounts(username_override, password_override)``:

* If both ``username_override`` and ``password_override`` are
  non-empty, return a single :class:`AccountConfig` with
  ``source="cli"``.
* Else, if ``POSTCARDS_USERNAME`` is set, look up the matching
  password in:
  1. ``POSTCARDS_PASSWORD`` env var,
  2. the keyring (username as the key),
  3. the ``accounts`` list of the config file (matched by username).
* Else, return every account in the config file's ``accounts`` list.

A missing required field raises :class:`ConfigError`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from postcards.backend.base import AddressSpec

# Service name used when reading credentials from the OS keyring.
# Per CONSTITUTION.md §2.2 the keyring is one of the supported
# sources; the convention is "service per application".
KEYRING_SERVICE = "postcards"


class ConfigError(RuntimeError):
    """Raised when a required field is missing or a credential cannot be resolved."""


@dataclass(frozen=True)
class AccountConfig:
    """A Swiss Post account resolved by :class:`ConfigLayer`.

    ``source`` is one of ``"cli"``, ``"env"``, ``"keyring"``,
    ``"config_file"``; tests assert against it to confirm the
    expected resolution path was taken.
    """

    username: str
    password: str
    key: str | None = None
    source: str = "config_file"

    def is_valid(self) -> bool:
        """Return True iff both username and password are non-empty."""
        return bool(self.username and self.password)


@dataclass
class ConfigLayer:
    """Typed configuration loader.

    Construct with the env mapping and the config-file path the
    loader should use. Both can be overridden; the defaults match
    the legacy CLI's behaviour (``os.environ``, ``./config.json``).
    """

    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    config_path: Path | None = None

    # The keyring is lazy: we only try to import ``keyring`` when
    # ``load_accounts`` actually needs it, and we let tests inject a
    # mock keyring backend via ``__init__``.
    keyring_backend: Any = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def config_path_resolved(self) -> Path:
        """Return the absolute config-file path the loader will read."""
        if self.config_path is not None:
            return self.config_path
        raw = self.env.get("POSTCARDS_CONFIG")
        if raw:
            return Path(raw).expanduser()
        return Path.cwd() / "config.json"

    def load_accounts(
        self,
        username_override: str | None = None,
        password_override: str | None = None,
    ) -> list[AccountConfig]:
        """Resolve accounts per the constitution's resolution order.

        Parameters
        ----------
        username_override, password_override:
            CLI-supplied credentials (``--username`` / ``--password``).
            When both are non-empty, they short-circuit every other
            source and return a single ``AccountConfig(source="cli")``.
        """
        if username_override and password_override:
            return [
                AccountConfig(
                    username=username_override,
                    password=password_override,
                    source="cli",
                )
            ]

        accounts: list[AccountConfig] = []
        config = self._load_config_file()

        # Single-account env path: ``POSTCARDS_USERNAME`` set.
        env_username = self.env.get("POSTCARDS_USERNAME")
        if env_username:
            password = self._resolve_password_for_username(env_username, config=config)
            if password is None:
                raise ConfigError(
                    f"POSTCARDS_USERNAME is set but no password could be resolved "
                    f"for {env_username!r} via env / keyring / config_file"
                )
            accounts.append(
                AccountConfig(
                    username=env_username,
                    password=password,
                    key=self.env.get("POSTCARDS_KEY"),
                    source=self._password_source(env_username, config=config),
                )
            )
            return accounts

        # Multi-account config-file path: read the ``accounts`` list.
        for entry in config.get("accounts", []) or []:
            username = entry.get("username")
            stored_password = entry.get("password")
            if not username:
                raise ConfigError(f"account entry is missing 'username': {entry!r}")
            accounts.append(
                AccountConfig(
                    username=username,
                    password=str(stored_password) if stored_password else "",
                    key=self.env.get("POSTCARDS_KEY"),
                    source="config_file",
                )
            )

        if not accounts:
            raise ConfigError(
                f"no accounts found in {self.config_path_resolved()} "
                "and POSTCARDS_USERNAME is not set"
            )
        return accounts

    def load_recipient(self, config: dict[str, Any] | None = None) -> AddressSpec:
        """Return the recipient address from the config file.

        Raises :class:`ConfigError` when the ``recipient`` block is
        missing or any required field is empty.
        """
        cfg = config if config is not None else self._load_config_file()
        recipient = cfg.get("recipient") or {}
        return _build_address(recipient, kind="recipient")

    def load_sender(self, config: dict[str, Any] | None = None) -> AddressSpec | None:
        """Return the sender address, or ``None`` to fall back to the recipient.

        A ``sender`` block missing in the config returns ``None``; the
        CLI's send flow uses the recipient address as the sender in
        that case (matching the legacy behaviour).
        """
        cfg = config if config is not None else self._load_config_file()
        sender = cfg.get("sender")
        if not sender:
            return None
        return _build_address(sender, kind="sender")

    def load_backend_name(self, config: dict[str, Any] | None = None) -> str | None:
        """Return the backend name from env / config, or ``None``.

        ``POSTCARDS_BACKEND`` wins; otherwise the ``backend`` field
        of the config file is consulted. Returning ``None`` lets the
        backend registry fall back to its default.
        """
        env_name = self.env.get("POSTCARDS_BACKEND")
        if env_name:
            return env_name
        cfg = config if config is not None else self._load_config_file()
        raw = cfg.get("backend")
        return raw if isinstance(raw, str) else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_config_file(self) -> dict[str, Any]:
        """Read and parse the config file, returning ``{}`` when missing.

        A missing config file is not an error at this layer — the
        caller may be loading only from env / keyring.
        """
        path = self.config_path_resolved()
        if not path.is_file():
            return {}
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"failed to parse config file at {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(
                f"config file at {path} must be a JSON object, got {type(data).__name__}"
            )
        return data

    def _resolve_password_for_username(
        self,
        username: str,
        *,
        config: dict[str, Any],
    ) -> str | None:
        """Try env → keyring → config_file in that order."""
        env_password = self.env.get("POSTCARDS_PASSWORD")
        if env_password:
            return env_password

        from_keyring = self._read_keyring(username)
        if from_keyring is not None:
            return from_keyring

        for entry in config.get("accounts", []) or []:
            if entry.get("username") == username:
                stored = entry.get("password")
                if stored:
                    return str(stored)
        return None

    def _password_source(
        self,
        username: str,
        *,
        config: dict[str, Any],
    ) -> str:
        """Return the source name ("env" / "keyring" / "config_file") for diagnostics."""
        if self.env.get("POSTCARDS_PASSWORD"):
            return "env"
        if self._read_keyring(username) is not None:
            return "keyring"
        return "config_file"

    def _read_keyring(self, username: str) -> str | None:
        """Read a password from the keyring, returning ``None`` on any failure.

        The keyring is optional; if the package is not installed the
        function returns ``None``. Tests inject a fake backend via
        ``keyring_backend=`` to drive this path.
        """
        backend = self.keyring_backend
        if backend is None:
            try:
                import keyring
            except ImportError:
                return None
        else:
            keyring = backend
        try:
            value = keyring.get_password(KEYRING_SERVICE, username)
        except Exception:
            # The keyring backend raised (locked, denied, ...). Treat as
            # "no value" so the loader can fall back to the next source.
            return None
        return value


def _build_address(raw: Mapping[str, Any], *, kind: str) -> AddressSpec:
    """Build an :class:`AddressSpec` from a config-file address block.

    Required fields are ``firstname``, ``lastname``, ``street``,
    ``zipcode``, ``city``. Anything else is optional.
    """
    required = ("firstname", "lastname", "street", "zipcode", "city")
    missing = [f for f in required if not raw.get(f)]
    if missing:
        raise ConfigError(f"{kind} address is missing required field(s): {missing}")
    return AddressSpec(
        prename=str(raw["firstname"]),
        lastname=str(raw["lastname"]),
        street=str(raw["street"]),
        zip_code=str(raw["zipcode"]),
        place=str(raw["city"]),
        company=str(raw.get("company") or ""),
        country=str(raw.get("country") or ""),
        salutation=str(raw.get("salutation") or ""),
        company_addition=str(raw.get("company_addition") or ""),
    )


__all__ = [
    "KEYRING_SERVICE",
    "AccountConfig",
    "ConfigError",
    "ConfigLayer",
]
