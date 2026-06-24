"""Vendored shim for ``postcard-creator`` (PyPI package ``postcard-creator``,
import name ``postcard_creator``).

Why this exists
---------------

The upstream ``postcard-creator`` package is the unofficial wrapper
around the Swiss Postcard Creator consumer web API. It is in
maintenance mode and the released wheels (``2.2``) declare runtime
dependencies that no longer install cleanly on Python 3.12 / 3.13
(``cookies``, ``Js2Py``/``pyjsparser``, an old ``python-resize-image``,
``pytz``, ``tzlocal``, pinned 2017-era ``certifi``/``urllib3``).

We do not need the live API to exercise the CLI surface — the
``postcards`` codebase is a SwissID-authenticated consumer wrapper and
cannot be exercised in CI anyway (SwissID requires real credentials
and an anomaly-detection-protected flow with 2FA; see
``docs/CONSTITUTION.md`` §1).

This shim exposes the same public names that ``postcards.postcards``
and its plugins import (``Token``, ``PostcardCreator``, ``Postcard``,
``Recipient``, ``Sender``, ``PostcardCreatorException``) so that:

* ``postcards --help`` runs without import errors on 3.12 / 3.13.
* Plugin entry points (``postcards-folder``, ``postcards-random``,
  ``postcards-pexels``, ``postcards-chuck-norris``,
  ``postcards-yaml``) import cleanly.
* Unit and integration tests can construct ``Recipient`` /
  ``Sender`` / ``Postcard`` objects and exercise the CLI's send flow
  with a ``Backend``-like mock instead of the live API.

Network operations on the shim (``Token.fetch_token``,
``Token.has_valid_credentials``, ``PostcardCreator.send_free_card``,
``PostcardCreator.has_free_postcard``, ``PostcardCreator.get_quota``)
raise ``NotImplementedError`` so that any accidental live call is
caught immediately rather than silently going to the network. The
integration test suite monkey-patches these methods on a
``PostcardCreator`` instance to drive the CLI's send flow against a
mock backend.

Versioning
----------

``__version__`` of the shim is suffixed with ``-shim`` so that logs and
exception messages make clear which package answered. The upstream
``2.2`` semantics are preserved on the data classes (``Recipient``,
``Sender``, ``Postcard``, their ``is_valid()`` predicates).
"""

from __future__ import annotations

from typing import Any

from postcards._vendor.postcard_creator.postcard_creator import (
    Postcard,
    PostcardCreator,
    PostcardCreatorException,
    Recipient,
    Sender,
    Token,
)

__version__ = "2.2-shim"
__all__ = [
    "Postcard",
    "PostcardCreator",
    "PostcardCreatorException",
    "Recipient",
    "Sender",
    "Token",
    "__version__",
]


def __getattr__(name: str) -> Any:
    """Lazy re-export of names that the legacy code expects.

    The legacy ``postcard_creator`` package re-exports its top-level
    classes via the ``postcard_creator`` submodule (``from
    postcard_creator.postcard_creator import Postcard`` etc.). The
    shim keeps that import path working for any caller that does
    ``from postcards._vendor.postcard_creator import postcard_creator``
    and then accesses ``postcard_creator.Postcard`` etc.
    """
    if name == "postcard_creator":
        from postcards._vendor.postcard_creator import postcard_creator as _pc

        return _pc
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")