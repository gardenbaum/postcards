"""Tests for the backend registry / selection logic.

These tests exercise :func:`postcards.backend.registry.select_backend`
in isolation. They do NOT touch the network (MockBackend records but
does not call out; SwissIdConsumerBackend never reaches the network
in this suite because the registry is the only thing under test).

What is verified here
---------------------

* ``POSTCARDS_BACKEND`` env var selects the named backend.
* Config-file ``backend`` field selects when the env var is unset.
* Env wins over config (constitution §2: env takes precedence).
* Unknown names raise :class:`BackendNotAvailableError` and the
  message lists the valid names.
* Default falls through to ``"swissid"`` when neither source sets it.
* ``os.environ`` is consulted when ``env`` is ``None`` (the production
  path).
* ``available_backends()`` returns the full sorted set.
"""

from __future__ import annotations

import pytest

from postcards.backend import (
    BackendNotAvailableError,
    MockBackend,
    SwissIdConsumerBackend,
    available_backends,
    select_backend,
)


def test_available_backends_returns_sorted_set() -> None:
    """``available_backends`` exposes the built-ins, sorted alphabetically."""
    names = available_backends()
    assert "mock" in names
    assert "swissid" in names
    # sorted() is documented; assert the property rather than the exact
    # order so adding a third backend later does not break the test.
    assert names == sorted(names)


def test_select_backend_env_wins_over_config() -> None:
    """``POSTCARDS_BACKEND`` env var beats the config-file ``backend`` field."""
    backend = select_backend(
        env={"POSTCARDS_BACKEND": "swissid"},
        config={"backend": "mock"},
    )
    assert isinstance(backend, SwissIdConsumerBackend)


def test_select_backend_config_used_when_env_missing() -> None:
    """When the env var is absent, the config-file ``backend`` field wins."""
    backend = select_backend(env={}, config={"backend": "mock"})
    assert isinstance(backend, MockBackend)


def test_select_backend_falls_back_to_default() -> None:
    """Without env or config, the default backend (``swissid``) is selected."""
    backend = select_backend(env={}, config={})
    assert isinstance(backend, SwissIdConsumerBackend)


def test_select_backend_respects_custom_default() -> None:
    """``default=`` overrides the built-in ``swissid`` fallback."""
    backend = select_backend(env={}, config={}, default="mock")
    assert isinstance(backend, MockBackend)


def test_select_backend_unknown_name_raises_with_message_listing_valid_names() -> None:
    """A typo raises ``BackendNotAvailableError`` with the valid names listed."""
    with pytest.raises(BackendNotAvailableError) as excinfo:
        select_backend(env={"POSTCARDS_BACKEND": "mokc"})
    message = str(excinfo.value)
    # Both valid names appear in the error so the user can fix the typo
    # without reading the source.
    assert "mokc" in message
    assert "mock" in message
    assert "swissid" in message


def test_select_backend_env_none_uses_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """``env=None`` reads ``os.environ`` — the production selection path."""
    monkeypatch.setenv("POSTCARDS_BACKEND", "mock")
    backend = select_backend()
    assert isinstance(backend, MockBackend)


def test_select_backend_env_none_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``env=None`` with no ``POSTCARDS_BACKEND`` set falls back to ``swissid``."""
    monkeypatch.delenv("POSTCARDS_BACKEND", raising=False)
    backend = select_backend()
    assert isinstance(backend, SwissIdConsumerBackend)


def test_select_backend_returns_fresh_instance() -> None:
    """Each call constructs a fresh backend — no shared state between calls."""
    a = select_backend(env={"POSTCARDS_BACKEND": "mock"})
    b = select_backend(env={"POSTCARDS_BACKEND": "mock"})
    assert a is not b
    # The MockBackend records into its own lists; mutating one does
    # not leak into the other. Cast to MockBackend because the
    # registry's return type is the protocol (which has no ``logins``).
    assert isinstance(a, MockBackend)
    assert isinstance(b, MockBackend)
    a.login("u", "p")
    assert len(a.logins) == 1
    assert len(b.logins) == 0


def test_select_backend_swissid_returns_implementation() -> None:
    """``POSTCARDS_BACKEND=swissid`` returns a SwissIdConsumerBackend instance.

    The instance is constructed but not authenticated — calling
    ``send`` on it without ``login`` first raises ``RuntimeError``,
    which is the same behaviour the production CLI sees.
    """
    backend = select_backend(env={"POSTCARDS_BACKEND": "swissid"})
    assert isinstance(backend, SwissIdConsumerBackend)
    assert backend.name == "swissid"
    # Sending without authentication raises — confirms the backend
    # actually guards against the unauthenticated path.
    from postcards.backend.base import AddressSpec, PostcardSpec

    spec = PostcardSpec(
        sender=AddressSpec(
            prename="a",
            lastname="b",
            street="c",
            zip_code="d",
            place="e",
        ),
        recipient=AddressSpec(
            prename="f",
            lastname="g",
            street="h",
            zip_code="i",
            place="j",
        ),
        message="hello",
        picture=None,
    )
    with pytest.raises(RuntimeError, match="not authenticated"):
        backend.send(spec)
