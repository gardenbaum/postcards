"""Tests for the ``encode``/``decode`` credential helpers.

These helpers XOR each byte of ``clear`` against the rotating key
and then base64-urlsafe-encode the result (``_encode`` is the
matching operation). The XOR pattern is the legacy 2017-era
``postcards`` crypto: not strong — see ``docs/CONSTITUTION.md`` §2 —
but it is the project's contract and tests should pin its behavior.
"""

from __future__ import annotations

import pytest

from postcards.postcards import Postcards


@pytest.fixture
def encoder() -> Postcards:
    """A ``Postcards`` instance exposes the ``_encode`` / ``_decode`` helpers.

    The instance is created with the default constructor; the tests
    only use the encoding helpers and never call ``main()`` or any
    network code.
    """
    return Postcards()


def test_encode_decode_roundtrip(encoder: Postcards) -> None:
    """Roundtripping a cleartext through ``_encrypt`` / ``_decrypt`` recovers it."""
    key = "test-key-123"
    msg = "hunter2"
    assert encoder._decrypt(key, encoder._encrypt(key, msg)) == msg


def test_encode_decode_empty_string(encoder: Postcards) -> None:
    """The empty string roundtrips."""
    key = "k"
    encrypted = encoder._encrypt(key, "")
    assert encoder._decrypt(key, encrypted) == ""


def test_encode_decode_unicode(encoder: Postcards) -> None:
    """Unicode strings survive the roundtrip via UTF-8."""
    key = "another-key"
    msg = "Grüezi — mit Umlauten ✓"
    assert encoder._decrypt(key, encoder._encrypt(key, msg)) == msg


def test_encode_uses_urlsafe_base64(encoder: Postcards) -> None:
    """The encoded form is base64-urlsafe (no ``+`` / ``/`` chars)."""
    encrypted = encoder._encrypt("x", "hello")
    assert "+" not in encrypted
    assert "/" not in encrypted


def test_decrypt_returns_decoded_string(encoder: Postcards) -> None:
    """``_decrypt`` is the ``str``-level wrapper around ``_decode``."""
    key = "my-secret"
    credential = "supersecret"
    encrypted = encoder._encrypt(key, credential)
    assert encoder._decrypt(key, encrypted) == credential


def test_encrypt_returns_string(encoder: Postcards) -> None:
    """``_encrypt`` returns a ``str`` (base64-urlsafe-encoded)."""
    encrypted = encoder._encrypt("k", "v")
    assert isinstance(encrypted, str)
    assert "+" not in encrypted
    assert "/" not in encrypted


def test_decrypt_invalid_base64_exits(encoder: Postcards, monkeypatch: pytest.MonkeyPatch) -> None:
    """Garbage that fails to base64-decode raises and exits, matching the legacy contract.

    The legacy implementation calls ``sys.exit(1)`` if the encoded
    ciphertext is not valid base64-urlsafe (any ``binascii.Error``
    bubbles up and ``_decrypt``'s bare ``except`` re-raises after
    logging "wrong key given"). We patch ``sys.exit`` so the test
    does not actually exit, and assert that the helper logged an
    error first.
    """
    exits: list[int] = []
    monkeypatch.setattr("postcards.postcards.sys.exit", lambda code=0: exits.append(code))

    error_logs: list[str] = []
    monkeypatch.setattr(
        encoder.logger, "error", lambda msg, *args: error_logs.append(msg % args if args else msg)
    )

    # 1 char of garbage is enough to break base64-urlsafe decoding.
    encoder._decrypt("any-key", "!!!not-base64!!!")
    assert exits == [1]
    assert any("wrong key" in msg for msg in error_logs)
