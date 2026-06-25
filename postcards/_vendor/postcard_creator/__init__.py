"""Vendored shim for ``postcard-creator`` (PyPI package ``postcard-creator``,
import name ``postcard_creator``).

Why this exists
---------------

The upstream ``postcard-creator`` package is the unofficial wrapper
around the Swiss Postcard Creator consumer API. Its released wheels
(``2.2``) declare runtime dependencies that no longer install cleanly
on Python 3.12 / 3.13 (``cookies``, ``Js2Py``/``pyjsparser``, an old
``python-resize-image``, ``pytz``, ``tzlocal``, pinned 2017-era
``certifi``/``urllib3``).

So this package vendors a **modernized, working re-implementation** of
the upstream's SwissID + mobile-API flow, depending only on libraries
the project already ships (``requests``, ``beautifulsoup4``, Pillow):

* :mod:`~postcards._vendor.postcard_creator.token` — the real SwissID
  OAuth + SAML token flow (PKCE) plus the legacy ``isiweb`` path.
* :mod:`~postcards._vendor.postcard_creator.postcard_creator` — the
  data classes and the mobile-API client (``/user/quota``,
  ``/card/upload``).

It keeps the upstream public names (``Token``, ``PostcardCreator``,
``Postcard``, ``Recipient``, ``Sender``, ``PostcardCreatorException``)
so ``postcards.postcards`` and the plugins import unchanged.

Testing / safety
----------------

Network methods accept an injectable ``session`` and ``fetch_token``
raises :class:`PostcardCreatorException` on failure, so the test suite
drives the full flow against a **fake session** and **never** reaches
Swiss Post — SwissID needs real credentials and an
anomaly-detection-protected flow with 2FA, which cannot run in CI (see
``docs/CONSTITUTION.md`` §1). A live login is the user's manual step.

Versioning
----------

``__version__`` is suffixed ``-shim`` so logs make clear which package
answered; the upstream ``2.2`` semantics are preserved on the data
classes.
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
