"""Tests for the SwissID OAuth + SAML token flow (no live network).

Drives ``Token.fetch_token`` through the full SwissID sequence against an
injected fake session that returns canned responses for each step. This
verifies the request ordering and the response parsing (goto param,
authId, SAML assertion, OAuth code, access token) without ever reaching
Swiss Post — the project constitution forbids live calls in CI.

The live flow has anomaly detection and may require 2FA; it can only be
exercised end-to-end by the user with real credentials.
"""

from __future__ import annotations

from typing import Any

import pytest

from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorException
from postcards._vendor.postcard_creator.token import Token

_SUCCESS_URL = "https://login.swissid.ch/success-callback"
_SAML_FORM_ACTION = "https://pccweb.api.post.ch/saml-form-post"


class _Resp:
    """Minimal ``requests``-like response."""

    def __init__(
        self,
        *,
        status: int = 200,
        payload: dict[str, Any] | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
        history: list[_Resp] | None = None,
    ) -> None:
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}
        self.history = history or []

    def json(self) -> dict[str, Any]:
        return self._payload


class _SwissIdSession:
    """Fake session that walks the SwissID flow to a successful token.

    ``fail_at_login=True`` drops the ``nextAction.successUrl`` so the flow
    raises (simulating wrong credentials).
    """

    def __init__(self, *, fail_at_login: bool = False) -> None:
        self.fail_at_login = fail_at_login
        self.calls: list[tuple[str, str]] = []

    def _route(self, method: str, url: str) -> _Resp:
        self.calls.append((method, url))
        if "/idp/?login" in url:
            return _Resp(
                history=[_Resp(headers={"Location": "https://login.swissid.ch/?goto=GOTO123&x=1"})]
            )
        if "/api-login/authenticate/init" in url:
            return _Resp(payload={"tokens": {"authId": "INIT"}})
        if "/api-login/authenticate/basic" in url:
            return _Resp(payload={"tokens": {"authId": "BASIC"}})
        if "/api-login/anomaly-detection/device-print" in url:
            if self.fail_at_login:
                return _Resp(payload={"nextAction": {}})
            return _Resp(payload={"nextAction": {"successUrl": _SUCCESS_URL}})
        if url == _SUCCESS_URL:
            return _Resp(text=f'<form name="LoginForm" action="{_SAML_FORM_ACTION}"></form>')
        if url == _SAML_FORM_ACTION:
            return _Resp(
                text='<input name="SAMLResponse" value="SAML123"/>'
                '<input name="RelayState" value="RELAY1"/>'
            )
        if url.endswith("/OAuth/token"):
            return _Resp(
                payload={"access_token": "ACCESS", "token_type": "Bearer", "expires_in": 3600}
            )
        if url.endswith("/OAuth/"):
            return _Resp(headers={"Location": "ch.post.pcc://auth/x?code=CODE123"})
        # authorize, token/status, welcome-pack, anything else
        return _Resp(payload={})

    def get(self, url: str, **kwargs: Any) -> _Resp:
        return self._route("get", url)

    def post(self, url: str, **kwargs: Any) -> _Resp:
        return self._route("post", url)


def test_fetch_token_swissid_happy_path_sets_access_token() -> None:
    token = Token()
    session = _SwissIdSession()
    token.fetch_token("alice@example.ch", "pw", method="swissid", session=session)
    assert token.token == "ACCESS"
    assert token.token_type == "Bearer"
    assert token.token_expires_in == 3600
    assert token.token_implementation == "swissid"
    # The OAuth code exchange was the final call.
    assert any(url.endswith("/OAuth/token") for _, url in session.calls)


def test_has_valid_credentials_true_on_success() -> None:
    token = Token()
    assert (
        token.has_valid_credentials("a", "b", method="swissid", session=_SwissIdSession()) is True
    )


def test_fetch_token_raises_on_login_failure() -> None:
    token = Token()
    with pytest.raises(PostcardCreatorException):
        token.fetch_token("a", "bad", method="swissid", session=_SwissIdSession(fail_at_login=True))


def test_has_valid_credentials_false_on_failure() -> None:
    token = Token()
    session = _SwissIdSession(fail_at_login=True)
    assert token.has_valid_credentials("a", "bad", method="swissid", session=session) is False
    assert token.token is None
