"""Tests for the native SMS / second-factor SwissID login (no live network).

Drives the interactive ``begin_login`` → ``submit_second_factor`` flow — and
the backend / service wrappers around it — against an injected fake session
that simulates SwissID's ``api-login`` ``nextAction`` state machine. The live
SMS step's exact wire format is undocumented and finalized from a real capture
(see ``Token.begin_login``); these tests pin the *plumbing*: the state machine,
the step transitions and the error mapping, all without touching Swiss Post.
"""

from __future__ import annotations

from typing import Any

import pytest

from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorException
from postcards._vendor.postcard_creator.token import Token
from postcards.backend.exceptions import AuthenticationError
from postcards.backend.swissid import SwissIdConsumerBackend
from postcards.web import service

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


class _SmsSession:
    """Fake session simulating a SwissID account with SMS 2FA.

    * ``no_2fa=True`` — device-print returns ``successUrl`` directly (no 2FA).
    * ``otp_ok=False`` — the OTP endpoint rejects the code (no ``successUrl``).
    * ``wrong_password=True`` — device-print returns an empty ``nextAction`` and
      no ``authId`` (bad credentials).
    """

    def __init__(
        self, *, no_2fa: bool = False, otp_ok: bool = True, wrong_password: bool = False
    ) -> None:
        self.no_2fa = no_2fa
        self.otp_ok = otp_ok
        self.wrong_password = wrong_password
        self.calls: list[tuple[str, str]] = []

    def _route(self, method: str, url: str) -> _Resp:
        self.calls.append((method, url))
        if "/idp/?login" in url:
            return _Resp(
                history=[_Resp(headers={"Location": "https://login.swissid.ch/?goto=G&x=1"})]
            )
        if "/api-login/authenticate/init" in url:
            return _Resp(payload={"tokens": {"authId": "INIT"}})
        if "/api-login/authenticate/basic" in url:
            return _Resp(payload={"tokens": {"authId": "BASIC"}})
        if "/api-login/anomaly-detection/device-print" in url:
            if self.wrong_password:
                return _Resp(payload={"nextAction": {}})
            if self.no_2fa:
                return _Resp(payload={"nextAction": {"successUrl": _SUCCESS_URL}})
            return _Resp(payload={"tokens": {"authId": "DP"}, "nextAction": {"type": "SEND_OTP"}})
        if "/api-login/authenticate/otp" in url:
            if self.otp_ok:
                return _Resp(payload={"nextAction": {"successUrl": _SUCCESS_URL}})
            return _Resp(payload={"nextAction": {}})
        if url == _SUCCESS_URL:
            return _Resp(text=f'<form name="LoginForm" action="{_SAML_FORM_ACTION}"></form>')
        if url == _SAML_FORM_ACTION:
            return _Resp(
                text='<input name="SAMLResponse" value="SAML"/><input name="RelayState" value="R"/>'
            )
        if url.endswith("/OAuth/token"):
            return _Resp(
                payload={"access_token": "ACCESS", "token_type": "Bearer", "expires_in": 3600}
            )
        if url.endswith("/OAuth/"):
            return _Resp(headers={"Location": "ch.post.pcc://auth/x?code=CODE"})
        return _Resp(payload={})

    def get(self, url: str, **kwargs: Any) -> _Resp:
        return self._route("get", url)

    def post(self, url: str, **kwargs: Any) -> _Resp:
        return self._route("post", url)


# ---------------------------------------------------------------------------
# Token: begin_login / submit_second_factor state machine
# ---------------------------------------------------------------------------


def test_begin_login_no_2fa_authenticates_directly() -> None:
    token = Token()
    step = token.begin_login("alice@example.ch", "pw", session=_SmsSession(no_2fa=True))
    assert step == "AUTHENTICATED"
    assert token.token == "ACCESS"


def test_begin_login_with_sms_returns_second_factor() -> None:
    token = Token()
    step = token.begin_login("alice@example.ch", "pw", session=_SmsSession())
    assert step == "SECOND_FACTOR"
    assert token.token is None
    assert token.second_factor_prompt == "SEND_OTP"
    assert token.second_factor is not None


def test_submit_second_factor_completes_login() -> None:
    token = Token()
    session = _SmsSession()
    assert token.begin_login("alice@example.ch", "pw", session=session) == "SECOND_FACTOR"
    token.submit_second_factor("123456", session=session)
    assert token.token == "ACCESS"
    assert token.token_implementation == "swissid-2fa"
    assert any("/api-login/authenticate/otp" in url for _, url in session.calls)


def test_submit_second_factor_rejects_bad_code() -> None:
    token = Token()
    session = _SmsSession(otp_ok=False)
    token.begin_login("alice@example.ch", "pw", session=session)
    with pytest.raises(PostcardCreatorException):
        token.submit_second_factor("000000", session=session)
    assert token.token is None


def test_submit_second_factor_without_begin_raises() -> None:
    with pytest.raises(PostcardCreatorException):
        Token().submit_second_factor("123456")


def test_submit_second_factor_empty_code_raises() -> None:
    token = Token()
    session = _SmsSession()
    token.begin_login("alice@example.ch", "pw", session=session)
    with pytest.raises(PostcardCreatorException):
        token.submit_second_factor("   ", session=session)


def test_begin_login_wrong_password_raises() -> None:
    token = Token()
    with pytest.raises(PostcardCreatorException):
        token.begin_login("alice@example.ch", "bad", session=_SmsSession(wrong_password=True))


def test_direct_fetch_token_on_2fa_account_hints_at_2fa() -> None:
    token = Token()
    with pytest.raises(PostcardCreatorException, match="second factor"):
        token.fetch_token("alice@example.ch", "pw", method="swissid", session=_SmsSession())


# ---------------------------------------------------------------------------
# Backend wrapper
# ---------------------------------------------------------------------------


def test_backend_begin_sms_login_needs_code_then_submit() -> None:
    backend = SwissIdConsumerBackend()
    session = _SmsSession()
    assert backend.begin_sms_login("alice@example.ch", "pw", session=session) is False
    assert backend.second_factor_prompt == "SEND_OTP"
    assert backend.second_factor_info is not None
    backend.submit_sms_code("123456", session=session)  # must not raise
    assert backend._token is not None  # test inspects internal auth state


def test_backend_begin_sms_login_no_2fa_returns_true() -> None:
    backend = SwissIdConsumerBackend()
    assert backend.begin_sms_login("a@b.ch", "pw", session=_SmsSession(no_2fa=True)) is True


def test_backend_begin_sms_login_bad_credentials_raises() -> None:
    backend = SwissIdConsumerBackend()
    with pytest.raises(AuthenticationError):
        backend.begin_sms_login("a@b.ch", "bad", session=_SmsSession(wrong_password=True))


def test_backend_submit_without_begin_raises() -> None:
    with pytest.raises(AuthenticationError):
        SwissIdConsumerBackend().submit_sms_code("123456")


def test_backend_submit_bad_code_raises() -> None:
    backend = SwissIdConsumerBackend()
    session = _SmsSession(otp_ok=False)
    backend.begin_sms_login("a@b.ch", "pw", session=session)
    with pytest.raises(AuthenticationError):
        backend.submit_sms_code("000000", session=session)


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


def test_service_begin_sms_login_needs_code_then_submit() -> None:
    session = _SmsSession()
    state = service.begin_sms_login("alice@example.ch", "pw", session=session)
    assert state.ok and state.needs_code and not state.authenticated
    assert state.prompt == "SEND_OTP"
    final = service.submit_sms_code(state.backend, "123456", session=session)
    assert final.ok and final.authenticated


def test_service_begin_sms_login_no_2fa_authenticated() -> None:
    state = service.begin_sms_login("a@b.ch", "pw", session=_SmsSession(no_2fa=True))
    assert state.ok and state.authenticated and not state.needs_code


def test_service_begin_sms_login_requires_credentials() -> None:
    state = service.begin_sms_login("", "", session=_SmsSession())
    assert not state.ok


def test_service_begin_sms_login_bad_credentials() -> None:
    state = service.begin_sms_login("a@b.ch", "bad", session=_SmsSession(wrong_password=True))
    assert not state.ok and state.detail


def test_service_submit_empty_code() -> None:
    session = _SmsSession()
    state = service.begin_sms_login("a@b.ch", "pw", session=session)
    final = service.submit_sms_code(state.backend, "  ", session=session)
    assert not final.ok and final.needs_code


def test_service_submit_bad_code() -> None:
    session = _SmsSession(otp_ok=False)
    state = service.begin_sms_login("a@b.ch", "pw", session=session)
    final = service.submit_sms_code(state.backend, "000000", session=session)
    assert not final.ok and final.needs_code
