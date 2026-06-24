"""Tests for the postcard_creator vendored shim.

The shim is the in-tree replacement for the upstream ``postcard-creator``
PyPI package (see ``postcards._vendor.postcard_creator.__init__`` for
the rationale). These tests cover the public surface that ``postcards``
and its plugins rely on:

* Construction and field assignment for ``Recipient``, ``Sender``,
  ``Postcard``.
* ``is_valid()`` predicates on those data classes.
* ``PostcardCreator`` accepting a ``Token`` whose ``.token`` attribute
  is non-``None`` and rejecting a ``Token`` without one.
* ``Token`` exposes the URLs and headers the upstream class had.
* Every network method (``fetch_token``, ``has_valid_credentials``,
  ``send_free_card``, ``has_free_postcard``, ``get_quota``) raises
  ``NotImplementedError`` — they are stubs; tests that need a live
  flow monkey-patch the instance.

These tests run on every supported Python version without hitting
the network. The mocked-Swiss-Post integration test lives in
``tests/test_send_integration.py``.
"""

from __future__ import annotations

import pytest

from postcards._vendor.postcard_creator import (
    Postcard,
    PostcardCreator,
    PostcardCreatorException,
    Recipient,
    Sender,
    Token,
    __version__,
)
from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorBase


def test_shim_version_string_is_suffixed() -> None:
    """The shim's version is suffixed ``-shim`` so logs make clear which answered."""
    assert __version__.endswith("-shim")
    # The numeric prefix must match the upstream major.minor so callers
    # that gate on the major version keep working.
    assert __version__.startswith("2.2")


def test_recipient_is_valid_requires_all_fields() -> None:
    """A recipient missing any of the required address fields is invalid."""
    full = Recipient(
        prename="Hans",
        lastname="Muster",
        street="Bahnhofstrasse 1",
        zip_code="8000",
        place="Zurich",
    )
    assert full.is_valid() is True

    missing_street = Recipient(
        prename="Hans",
        lastname="Muster",
        street="",
        zip_code="8000",
        place="Zurich",
    )
    assert missing_street.is_valid() is False


def test_sender_is_valid_requires_all_fields() -> None:
    """A sender missing any of the required address fields is invalid."""
    full = Sender(
        prename="Maria",
        lastname="Muster",
        street="Bahnhofstrasse 1",
        zip_code="8000",
        place="Zurich",
    )
    assert full.is_valid() is True

    missing_zip = Sender(
        prename="Maria",
        lastname="Muster",
        street="Bahnhofstrasse 1",
        zip_code="",
        place="Zurich",
    )
    assert missing_zip.is_valid() is False


def test_postcard_is_valid_requires_recipient_and_sender() -> None:
    """A postcard with no recipient or no sender is invalid."""
    sender = Sender(
        prename="Maria",
        lastname="Muster",
        street="Bahnhofstrasse 1",
        zip_code="8000",
        place="Zurich",
    )
    recipient = Recipient(
        prename="Hans",
        lastname="Muster",
        street="Bahnhofstrasse 2",
        zip_code="8000",
        place="Zurich",
    )
    card = Postcard(sender=sender, recipient=recipient, picture_stream=None, message="Hi")
    assert card.is_valid() is True

    # We can't construct a Postcard with recipient=None (typed as
    # Recipient, not Optional), so instead we verify is_valid returns
    # False when sender/recipient fields are blank — the same code path
    # the upstream class takes.
    bad_recipient = Recipient(prename="", lastname="", street="", zip_code="", place="")
    card_bad = Postcard(sender=sender, recipient=bad_recipient, picture_stream=None)
    assert card_bad.is_valid() is False


def test_postcard_validate_raises_for_invalid_recipient() -> None:
    """Postcard.validate raises a typed exception for bad recipient/sender."""
    sender = Sender(prename="x", lastname="y", street="z", zip_code="1", place="q")
    bad_recipient = Recipient(prename="", lastname="y", street="z", zip_code="1", place="q")
    card = Postcard(sender=sender, recipient=bad_recipient, picture_stream=None)
    with pytest.raises(PostcardCreatorException):
        card.validate()


def test_postcardcreator_rejects_token_without_token_string() -> None:
    """PostcardCreator requires a Token whose ``.token`` is set, like upstream."""
    bare = Token()
    assert bare.token is None
    with pytest.raises(PostcardCreatorException, match="No Token given"):
        PostcardCreator(bare)

    # A Token with a sentinel token string is accepted.
    bare.token = "sentinel"
    pc = PostcardCreator(bare)
    assert pc.token is bare
    assert isinstance(pc.impl, PostcardCreatorBase)


def test_token_urls_match_upstream_endpoints() -> None:
    """The Token URLs and User-Agent headers match the upstream contract.

    These exact strings are not load-bearing for the shim (the shim
    never reaches the network), but the legacy ``postcards.postcards``
    code reads them and tests should fail loudly if the URLs change.
    """
    token = Token()
    assert token.base == "https://account.post.ch"
    assert token.swissid == "https://login.swissid.ch"
    assert token.token_url == "https://postcardcreator.post.ch/saml/SSO/alias/defaultAlias"
    assert "User-Agent" in token.legacy_headers
    assert "User-Agent" in token.swissid_headers


def test_token_has_valid_credentials_raises_not_implemented() -> None:
    """``has_valid_credentials`` is a network call: the shim raises, never tries."""
    token = Token()
    with pytest.raises(NotImplementedError, match="shim"):
        token.has_valid_credentials("user", "pass")


def test_token_fetch_token_validates_args() -> None:
    """``fetch_token`` validates args first, then raises on the network path."""
    token = Token()
    with pytest.raises(PostcardCreatorException, match="No username"):
        token.fetch_token(None, None)

    with pytest.raises(PostcardCreatorException, match="unknown method"):
        token.fetch_token("u", "p", method="not-a-real-method")


def test_token_fetch_token_raises_not_implemented_for_valid_args() -> None:
    """With valid args the shim raises NotImplementedError instead of going to the network."""
    token = Token()
    with pytest.raises(NotImplementedError, match="shim"):
        token.fetch_token("user", "pass")


def test_postcardcreator_network_methods_raise_not_implemented() -> None:
    """``has_free_postcard``, ``send_free_card``, ``get_quota`` are stubs."""
    token = Token()
    token.token = "x"
    pc = PostcardCreator(token)

    sender = Sender(prename="a", lastname="b", street="c", zip_code="d", place="e")
    recipient = Recipient(prename="f", lastname="g", street="h", zip_code="i", place="j")
    card = Postcard(sender=sender, recipient=recipient, picture_stream=None)

    with pytest.raises(NotImplementedError):
        pc.has_free_postcard()

    with pytest.raises(NotImplementedError):
        pc.send_free_card(postcard=card, mock_send=True)

    with pytest.raises(NotImplementedError):
        pc.get_quota()


def test_postcardcreator_getattr_delegates_to_stub_impl() -> None:
    """``PostcardCreator.__getattr__`` forwards unknown attrs to the impl.

    The shim's ``PostcardCreatorBase`` raises ``AttributeError`` for
    attributes it doesn't know about, so an unknown method call on a
    ``PostcardCreator`` instance bubbles the same error up through the
    proxy — matching the upstream behaviour.
    """
    token = Token()
    token.token = "x"
    pc = PostcardCreator(token)
    with pytest.raises(AttributeError):
        pc.some_future_method()


def test_recipient_constructor_signature_matches_upstream() -> None:
    """``Recipient`` constructor takes the upstream keyword arguments."""
    r = Recipient(
        prename="a",
        lastname="b",
        street="c",
        zip_code="d",
        place="e",
        company="co",
        company_addition="co-add",
        salutation="Mr.",
    )
    assert r.company == "co"
    assert r.company_addition == "co-add"
    assert r.salutation == "Mr."


def test_sender_constructor_signature_matches_upstream() -> None:
    """``Sender`` constructor takes the upstream keyword arguments."""
    s = Sender(
        prename="a",
        lastname="b",
        street="c",
        zip_code="d",
        place="e",
        company="co",
        country="CH",
    )
    assert s.company == "co"
    assert s.country == "CH"
