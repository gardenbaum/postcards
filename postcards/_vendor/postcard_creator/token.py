"""``Token`` class for the vendored ``postcard_creator`` shim.

Mirrors the upstream ``postcard_creator.token.Token`` constructor and
public surface. The shim raises ``NotImplementedError`` from every
method that would have hit the network
(``fetch_token``, ``has_valid_credentials``) so that any accidental
live call is caught immediately; integration tests monkey-patch these
on a ``Token`` instance to drive the CLI's send flow against a mock
backend.
"""

from __future__ import annotations

import logging
from typing import Any

from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorException

LOGGING_TRACE_LVL = 5
logger = logging.getLogger("postcard_creator")
logging.addLevelName(LOGGING_TRACE_LVL, "TRACE")
logger.trace = lambda *args: logger.log(LOGGING_TRACE_LVL, *args)  # type: ignore[attr-defined]


class Token:
    """Holds an authenticated Swiss Post token.

    The shim's constructor only stores URLs and headers — no network.
    Setting ``self.token = '<value>'`` is enough for the shim's
    ``PostcardCreator`` to accept it. Integration tests construct a
    ``Token`` and set ``token.token`` to a sentinel value before
    handing it to ``PostcardCreator``.
    """

    def __init__(self, _protocol: str = "https://") -> None:
        self.protocol = _protocol
        self.base = f"{self.protocol}account.post.ch"
        self.swissid = f"{self.protocol}login.swissid.ch"
        self.token_url = f"{self.protocol}postcardcreator.post.ch/saml/SSO/alias/defaultAlias"
        self.legacy_headers: dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 6.0.1; wv) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Version/4.0 Chrome/52.0.2743.98 Mobile Safari/537.36"
            ),
        }
        self.swissid_headers: dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 6.0.1; wv) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Version/4.0 Chrome/52.0.2743.98 Mobile Safari/537.36"
            ),
        }

        self.token: str | None = None
        self.token_type: str | None = None
        self.token_expires_in: int | None = None
        self.token_fetched_at: str | None = None
        self.token_implementation: str | None = None
        self.cache_token: bool = False

    def has_valid_credentials(
        self, username: str | None, password: str | None, method: str = "mixed"
    ) -> bool:
        """Shim stub.

        The upstream method performs a live SwissID login. The shim
        raises ``NotImplementedError`` so accidental live calls are
        caught immediately; integration tests that need a valid
        token set ``token.token`` directly and monkey-patch this
        method to return ``True``.
        """
        raise NotImplementedError(
            "postcards._vendor.postcard_creator is a shim; "
            "Token.has_valid_credentials must be mocked in tests."
        )

    def fetch_token(self, username: str | None, password: str | None, method: str = "mixed") -> str:
        """Shim stub — never reaches the network."""
        if username is None or password is None:
            raise PostcardCreatorException("No username/ password given")

        methods = ["mixed", "legacy", "swissid"]
        if method not in methods:
            raise PostcardCreatorException("unknown method. choose from: " + str(methods))

        raise NotImplementedError(
            "postcards._vendor.postcard_creator is a shim; "
            "Token.fetch_token must be mocked in tests."
        )

    # The upstream ``Token`` carries a handful of helper methods
    # (``_do_get``, ``_do_post``, ``_parse_form``, etc.) that are only
    # used inside ``fetch_token``. The shim does not implement them
    # because ``fetch_token`` always raises; tests that exercise
    # ``fetch_token`` patch it on the instance instead.
    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"postcards._vendor.postcard_creator.token.Token has no attribute {name!r} "
            "in the shim; the upstream implementation is only invoked by "
            "Token.fetch_token, which the shim does not implement."
        )
