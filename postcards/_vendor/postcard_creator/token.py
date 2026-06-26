"""``Token`` — real SwissID / legacy authentication for the Postcard Creator.

Ported from the upstream ``postcard_creator_wrapper`` (abertschi), modernized
to use only ``requests`` + ``beautifulsoup4`` + ``urllib3`` (the upstream's
``requests_toolbelt`` debug-dump dependency is dropped). The flow obtains an
OAuth access token for the Swiss Post Postcard Creator mobile API:

1. **PKCE OAuth authorize** against ``pccweb.api.post.ch/OAuth/authorization``.
2. **SwissID web login** (``login.swissid.ch/api-login/*``): init → submit
   username/password → device-print anomaly step.
3. **SAML assertion** posted back to ``pccweb.api.post.ch/OAuth/`` → ``code``.
4. **Token exchange** at ``pccweb.api.post.ch/OAuth/token`` → ``access_token``.

A legacy ``isiweb`` username/password path is also ported for the few
non-SwissID Post accounts.

Testability / safety
--------------------
Every network method accepts an optional ``session`` (a ``requests.Session``
or a stand-in) so tests inject a fake and **never** hit the live API — the
project constitution forbids live calls in CI. ``Token.fetch_token`` raises
:class:`PostcardCreatorException` on any failure (bad credentials, changed
endpoints); :meth:`Token.has_valid_credentials` wraps that into a bool.

Fragility note: the SwissID flow has anomaly detection and may require 2FA;
it can break server-side. A live login is the user's manual, interactive step.
"""

from __future__ import annotations

import base64
import copy
import datetime
import hashlib
import logging
import re
import secrets
import urllib.parse
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3 import Retry

from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorException

logger = logging.getLogger("postcard_creator")

#: OAuth client constants — these are the public mobile-app client id/secret
#: shipped in the Swiss Post Postcard Creator Android app (not user secrets).
_CLIENT_ID = "ae9b9894f8728ca78800942cda638155"
_CLIENT_SECRET = "89ff451ede545c3f408d792e8caaddf0"
_REDIRECT_URI = "ch.post.pcc://auth/1016c75e-aa9c-493e-84b8-4eb3ba6177ef"
_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 6.0.1; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/52.0.2743.98 Mobile Safari/537.36"
)
_AUTH_METHODS = ("mixed", "legacy", "swissid")


def _base64url(raw: bytes) -> str:
    """URL-safe base64 without padding (PKCE encoding)."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def extract_authorization_code(pasted: str) -> str:
    """Pull the OAuth ``code`` out of a pasted redirect URL or raw code.

    The browser-assisted login ends at a ``ch.post.pcc://auth/...?code=...``
    redirect the browser cannot open; the user copies that URL (or just the
    code) and pastes it. Accepts either form. Raises
    :class:`PostcardCreatorException` when no code can be found.
    """
    text = (pasted or "").strip()
    if not text:
        raise PostcardCreatorException("no authorization code provided")
    if "code=" in text:
        # Custom-scheme URLs parse fine: urlparse keeps the query string.
        query = parse_qs(urlparse(text).query)
        codes = query.get("code")
        if codes and codes[0]:
            return codes[0]
        raise PostcardCreatorException(f"could not find a 'code' parameter in: {text}")
    # Assume the user pasted the bare code.
    return text


def _redact(payload: Any) -> Any:
    """Return a deep copy of a login payload with token-ish values masked.

    Used only for logging the captured second-factor ``nextAction`` so we can
    finalize the (publicly undocumented) SMS step without leaking the
    short-lived ``authId``.
    """
    try:
        data = copy.deepcopy(payload)
    except Exception:
        return {}
    if isinstance(data, dict):
        tokens = data.get("tokens")
        if isinstance(tokens, dict):
            data["tokens"] = dict.fromkeys(tokens, "***")
    return data


@dataclass
class _PendingLogin:
    """In-flight SwissID login waiting for a second factor (e.g. SMS code).

    Captured by :meth:`Token.begin_login` when the ``api-login`` state machine
    stops at a 2FA challenge, and consumed by
    :meth:`Token.submit_second_factor`.
    """

    session: Any
    qs: str
    verifier: str
    auth_id: str
    next_action: dict[str, Any]
    raw: dict[str, Any]


class Token:
    """Holds an authenticated Swiss Post access token.

    Construct, then call :meth:`fetch_token` (or :meth:`has_valid_credentials`)
    with the user's SwissID credentials. On success ``self.token`` holds the
    bearer token used by :class:`PostcardCreator`.
    """

    def __init__(self, _protocol: str = "https://") -> None:
        self.protocol = _protocol
        self.base = f"{self.protocol}account.post.ch"
        self.swissid = f"{self.protocol}login.swissid.ch"
        self.token_url = f"{self.protocol}postcardcreator.post.ch/saml/SSO/alias/defaultAlias"
        self.user_agent = _USER_AGENT
        self.legacy_headers: dict[str, str] = {"User-Agent": _USER_AGENT}
        self.swissid_headers: dict[str, str] = {"User-Agent": _USER_AGENT}

        self.token: str | None = None
        self.token_type: str | None = None
        self.token_expires_in: int | None = None
        self.token_fetched_at: datetime.datetime | None = None
        self.token_implementation: str | None = None
        self.cache_token: bool = False

        #: Set by :meth:`begin_login` when an SMS / second factor is required;
        #: the raw ``nextAction`` is also exposed so the app can show it and we
        #: can finalize the (undocumented) SMS wire format from a real attempt.
        self._pending: _PendingLogin | None = None
        self.second_factor: dict[str, Any] | None = None
        self.second_factor_prompt: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_valid_credentials(
        self,
        username: str | None,
        password: str | None,
        method: str = "mixed",
        *,
        session: Any = None,
    ) -> bool:
        """Return ``True`` iff ``fetch_token`` succeeds (never raises)."""
        try:
            self.fetch_token(username, password, method=method, session=session)
            return True
        except PostcardCreatorException:
            return False

    def fetch_token(
        self,
        username: str | None,
        password: str | None,
        method: str = "swissid",
        *,
        session: Any = None,
    ) -> None:
        """Authenticate and store the access token on ``self``.

        ``method`` is one of ``"swissid"`` (default), ``"legacy"`` or
        ``"mixed"`` (try legacy, fall back to SwissID). Raises
        :class:`PostcardCreatorException` on any failure.
        """
        if username is None or password is None:
            raise PostcardCreatorException("No username/ password given")
        if method not in _AUTH_METHODS:
            raise PostcardCreatorException(
                "unknown method. choose from: " + str(list(_AUTH_METHODS))
            )

        access_token: dict[str, Any] | None = None
        implementation = ""

        if method in ("legacy", "mixed"):
            try:
                access_token = self._access_token_legacy(
                    session or self._create_session(), username, password
                )
                implementation = "legacy"
            except Exception as exc:
                if method == "legacy":
                    raise PostcardCreatorException(f"legacy authentication failed: {exc}") from exc
                logger.info("legacy auth failed, trying swissid: %s", exc)

        if access_token is None:
            try:
                access_token = self._access_token_swissid(
                    session or self._create_session(), username, password
                )
                implementation = "swissid"
            except PostcardCreatorException:
                raise
            except Exception as exc:
                raise PostcardCreatorException(f"swissid authentication failed: {exc}") from exc

        self._store_token(access_token, implementation)

    # ------------------------------------------------------------------
    # Browser-assisted login (works with any SwissID 2FA, incl. push/passkey)
    # ------------------------------------------------------------------

    def build_authorize_url(self) -> tuple[str, str]:
        """Return ``(authorize_url, code_verifier)`` for a browser login.

        The user opens ``authorize_url`` in a normal browser, completes the
        SwissID login + 2FA there, and the browser ends at a
        ``ch.post.pcc://auth/...?code=...`` redirect. Feed that code and the
        returned ``code_verifier`` back into :meth:`exchange_code`.
        """
        verifier, challenge = self._pkce()
        url = "https://pccweb.api.post.ch/OAuth/authorization?" + self._authorize_query(challenge)
        return url, verifier

    def exchange_code(self, code: str, code_verifier: str, *, session: Any = None) -> None:
        """Exchange an authorization ``code`` (+ its PKCE verifier) for a token.

        Stores the access token on ``self``. Raises
        :class:`PostcardCreatorException` on failure.
        """
        body = self._exchange_code_for_token(session or self._create_session(), code, code_verifier)
        self._store_token(body, "swissid-browser")

    # ------------------------------------------------------------------
    # Native SMS / second-factor login (interactive, in-app)
    # ------------------------------------------------------------------

    def begin_login(
        self,
        username: str | None,
        password: str | None,
        *,
        session: Any = None,
    ) -> str:
        """Start an interactive SwissID login; return the next required step.

        Runs the ``api-login`` flow up to and including the device-print
        anomaly step, then inspects the server's ``nextAction``:

        * ``"AUTHENTICATED"`` — no second factor; the access token is already
          stored on ``self`` (``send`` / ``quota`` work immediately).
        * ``"SECOND_FACTOR"`` — the account needs a second factor (SMS code).
          SwissID has sent the code; call :meth:`submit_second_factor` with it.
          The raw challenge is exposed via :attr:`second_factor` /
          :attr:`second_factor_prompt`.

        Only SMS can be completed in-app: push / passkey cannot be answered
        headlessly (use :meth:`build_authorize_url` browser login for those).
        Raises :class:`PostcardCreatorException` on bad credentials.
        """
        if username is None or password is None:
            raise PostcardCreatorException("No username/ password given")
        session = session or self._create_session()
        self._pending = None
        self.second_factor = None
        self.second_factor_prompt = ""

        verifier, challenge = self._pkce()
        qs, resp = self._swissid_until_anomaly(session, username, password, challenge)
        next_action = self._next_action(resp)

        if next_action.get("successUrl"):
            body = self._complete_after_authenticated(session, resp, verifier)
            self._store_token(body, "swissid")
            return "AUTHENTICATED"

        auth_id = self._auth_id(resp) or ""
        if not next_action and not auth_id:
            raise PostcardCreatorException("failed to login, username/password wrong?")

        # A second factor is required. We do not know the exact SMS wire format
        # (it is documented nowhere — the upstream wrapper never did 2FA), so we
        # capture the raw challenge for the UI and for finalizing the step.
        self._pending = _PendingLogin(
            session=session,
            qs=qs,
            verifier=verifier,
            auth_id=auth_id,
            next_action=next_action,
            raw=self._json(resp),
        )
        self.second_factor = self._json(resp)
        self.second_factor_prompt = str(next_action.get("type") or "second factor")
        logger.warning(
            "SwissID second-factor challenge reached (type=%s); nextAction=%s",
            self.second_factor_prompt,
            _redact(self.second_factor),
        )
        return "SECOND_FACTOR"

    def submit_second_factor(self, code: str, *, session: Any = None) -> None:
        """Submit the SMS code for a login started by :meth:`begin_login`.

        On success the access token is stored on ``self``. Raises
        :class:`PostcardCreatorException` if there is no pending login or the
        code is rejected; the rejection message embeds the (redacted) server
        response so an unexpected wire format can be reported and finalized.
        """
        if self._pending is None:
            raise PostcardCreatorException("no pending login; call begin_login() first")
        pending = self._pending
        sess = session or pending.session

        code = (code or "").strip()
        if not code:
            raise PostcardCreatorException("no SMS code provided")

        resp = self._post_second_factor(sess, pending, code)
        if not self._next_action(resp).get("successUrl"):
            # The OTP step may be followed by a (repeat) device-print round.
            resp = self._drive_known(sess, resp, pending.qs)
        if not self._next_action(resp).get("successUrl"):
            raise PostcardCreatorException(
                f"second factor not accepted: {_redact(self._json(resp))}"
            )

        body = self._complete_after_authenticated(sess, resp, pending.verifier)
        self._store_token(body, "swissid-2fa")
        self._pending = None
        self.second_factor = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _create_session(self) -> requests.Session:
        """Create a retrying session (the backend throttles aggressive clients)."""
        session = requests.Session()
        retry = Retry(
            total=5, read=5, connect=5, backoff_factor=0.5, status_forcelist=(500, 502, 504)
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _pkce(self) -> tuple[str, str]:
        """Return ``(code_verifier, code_challenge)`` for the PKCE S256 flow."""
        verifier = _base64url(secrets.token_bytes(64))
        challenge = _base64url(hashlib.sha256(verifier.encode("utf-8")).digest())
        return verifier, challenge

    def _authorize_query(self, challenge: str) -> str:
        return urllib.parse.urlencode(
            {
                "client_id": _CLIENT_ID,
                "response_type": "code",
                "redirect_uri": _REDIRECT_URI,
                "scope": "PCCWEB offline_access",
                "response_mode": "query",
                "state": "abcd",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "lang": "en",
            }
        )

    def _exchange_code_for_token(self, session: Any, code: str, verifier: str) -> dict[str, Any]:
        resp = session.post(
            "https://pccweb.api.post.ch/OAuth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": _REDIRECT_URI,
            },
            headers=self.swissid_headers,
            allow_redirects=False,
        )
        body = resp.json()
        if resp.status_code != 200 or "access_token" not in body:
            raise PostcardCreatorException(f"not able to fetch access token: {resp.text}")
        return body

    def _saml_to_code(self, session: Any, soup: BeautifulSoup) -> str:
        """POST the SAML assertion back to PCC and extract the OAuth ``code``."""
        saml_response = soup.find("input", {"name": "SAMLResponse"})
        if saml_response is None or saml_response.get("value") is None:
            raise PostcardCreatorException(
                "Username/password authentication failed. Are your credentials valid?"
            )
        relay_state = soup.find("input", {"name": "RelayState"})
        headers = dict(self.swissid_headers)
        headers["Origin"] = "https://account.post.ch"
        headers["X-Requested-With"] = "ch.post.it.pcc"
        headers["Upgrade-Insecure-Requests"] = "1"
        resp = session.post(
            "https://pccweb.api.post.ch/OAuth/",  # trailing slash matters
            headers=headers,
            data={
                "RelayState": relay_state["value"] if relay_state else "",
                "SAMLResponse": saml_response.get("value"),
            },
            allow_redirects=False,
        )
        try:
            location = resp.headers["Location"]
            return parse_qs(urlparse(location).query)["code"][0]
        except Exception as exc:
            raise PostcardCreatorException(
                "response does not have code attribute. Did the endpoint change?"
            ) from exc

    def _access_token_swissid(self, session: Any, username: str, password: str) -> dict[str, Any]:
        verifier, challenge = self._pkce()
        _qs, resp = self._swissid_until_anomaly(session, username, password, challenge)
        next_action = self._next_action(resp)
        if not next_action.get("successUrl"):
            if next_action.get("type") or self._auth_id(resp):
                raise PostcardCreatorException(
                    "this SwissID account requires a second factor (2FA); use the SMS or "
                    "browser login instead of direct e-mail + password"
                )
            raise PostcardCreatorException("failed to login, username/password wrong?")
        return self._complete_after_authenticated(session, resp, verifier)

    def _swissid_until_anomaly(
        self, session: Any, username: str, password: str, challenge: str
    ) -> tuple[str, Any]:
        """Run authorize → IdP → init → basic → device-print.

        Returns ``(qs, response)`` where ``response`` is the server's reply to
        the device-print step — either authenticated (``nextAction.successUrl``)
        or a second-factor challenge. Shared by the direct, SMS and (indirectly)
        legacy paths so the request ordering lives in one place.
        """
        session.get(
            "https://pccweb.api.post.ch/OAuth/authorization?" + self._authorize_query(challenge),
            allow_redirects=True,
            headers=self.swissid_headers,
        )

        idp_url = (
            "https://account.post.ch/idp/?login"
            "&targetURL=https://pccweb.api.post.ch/SAML/ServiceProvider/"
            "?redirect_uri=" + _REDIRECT_URI + "&profile=default"
            "&app=pccwebapi&inMobileApp=true&layoutType=standard"
        )
        resp = session.post(
            idp_url,
            data={"externalIDP": "externalIDP"},
            allow_redirects=True,
            headers=self.swissid_headers,
        )
        if not resp.history:
            raise PostcardCreatorException("failed to reach SwissID IdP")
        goto_match = re.search(r"goto=(.*?)$", resp.history[-1].headers["Location"])
        if goto_match is None:
            raise PostcardCreatorException("swissid: cannot find goto param")
        goto = goto_match.group(1).split("&")[0]
        if not goto:
            raise PostcardCreatorException("swissid: empty goto param")

        qs = f"locale=en&goto={goto}&acr_values=loa-1&realm=%2Fsesam&service=qoa1"
        session.get(
            f"https://login.swissid.ch/api-login/authenticate/token/status?{qs}",
            allow_redirects=True,
        )
        session.get(
            f"https://login.swissid.ch/api-login/welcome-pack?locale=en{goto}"
            "&acr_values=loa-1&realm=%2Fsesam&service=qoa1",
            allow_redirects=True,
        )
        resp = session.post(
            f"https://login.swissid.ch/api-login/authenticate/init?{qs}", allow_redirects=True
        )

        headers = dict(self.swissid_headers)
        headers["authId"] = resp.json()["tokens"]["authId"]
        resp = session.post(
            f"https://login.swissid.ch/api-login/authenticate/basic?{qs}",
            json={"username": username, "password": password},
            headers=headers,
            allow_redirects=True,
        )

        return qs, self._anomaly_detection(session, resp, qs)

    def _complete_after_authenticated(
        self, session: Any, resp: Any, verifier: str
    ) -> dict[str, Any]:
        """Finish an authenticated flow: successUrl → SAML assertion → token."""
        success_url = self._next_action(resp)["successUrl"]
        resp = session.get(success_url, headers=self.swissid_headers, allow_redirects=True)
        form_action = BeautifulSoup(resp.text, "html.parser").find("form", {"name": "LoginForm"})
        if form_action is None:
            raise PostcardCreatorException("swissid: LoginForm not found after login")
        resp = session.post(form_action["action"], headers=self.swissid_headers)

        soup = BeautifulSoup(resp.text, "html.parser")
        code = self._saml_to_code(session, soup)
        return self._exchange_code_for_token(session, code, verifier)

    # -- second-factor (SMS) helpers ----------------------------------
    #
    # The exact api-login SMS step is undocumented; these are the best
    # reconstruction of the `nextAction`-driven state machine and are written
    # to be finalized from the raw challenge captured on the first real attempt
    # (see Token.begin_login). If the captured nextAction carries an explicit
    # endpoint we use it; otherwise we fall back to a provisional path.

    def _post_second_factor(self, session: Any, pending: _PendingLogin, code: str) -> Any:
        """POST the SMS ``code`` to the second-factor endpoint."""
        headers = dict(self.swissid_headers)
        if pending.auth_id:
            headers["authId"] = pending.auth_id
        return session.post(
            self._second_factor_url(pending),
            json={"code": code},
            headers=headers,
            allow_redirects=True,
        )

    def _second_factor_url(self, pending: _PendingLogin) -> str:
        """Resolve the endpoint for submitting the SMS code.

        Prefers an explicit URL/path embedded in the captured ``nextAction``;
        otherwise uses a provisional path (finalized from a real capture).
        """
        for key in ("url", "href", "endpoint", "actionUrl", "submitUrl"):
            value = pending.next_action.get(key)
            if isinstance(value, str) and value:
                return value if value.startswith("http") else f"https://login.swissid.ch{value}"
        return f"https://login.swissid.ch/api-login/authenticate/otp?{pending.qs}"

    def _drive_known(self, session: Any, resp: Any, qs: str) -> Any:
        """Advance one known intermediate step (a repeat device-print) if present."""
        next_action = self._next_action(resp)
        if next_action.get("successUrl"):
            return resp
        if next_action.get("type") == "SEND_DEVICE_PRINT" or self._auth_id(resp):
            try:
                return self._anomaly_detection(session, resp, qs)
            except PostcardCreatorException:
                return resp
        return resp

    # -- small response accessors -------------------------------------

    def _store_token(self, body: dict[str, Any], implementation: str) -> None:
        """Store an access-token response on ``self`` (shared by all paths)."""
        try:
            self.token = body["access_token"]
            self.token_type = body.get("token_type")
            self.token_expires_in = body.get("expires_in")
            self.token_fetched_at = datetime.datetime.now()
            self.token_implementation = implementation
        except Exception as exc:
            raise PostcardCreatorException(f"token response missing fields: {body}") from exc

    def _json(self, resp: Any) -> dict[str, Any]:
        """Best-effort JSON body of ``resp`` (``{}`` when not JSON)."""
        try:
            data = resp.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _next_action(self, resp: Any) -> dict[str, Any]:
        """The ``nextAction`` object of an api-login response (``{}`` if absent)."""
        next_action = self._json(resp).get("nextAction")
        return next_action if isinstance(next_action, dict) else {}

    def _auth_id(self, resp: Any) -> str | None:
        """The ``tokens.authId`` of an api-login response, if present."""
        tokens = self._json(resp).get("tokens")
        return tokens.get("authId") if isinstance(tokens, dict) else None

    def _anomaly_detection(self, session: Any, prev_response: Any, qs: str) -> Any:
        """SwissID device-print anomaly step (introduced 2022-10).

        Any plausible device-print payload is accepted; we send a static one.
        """
        url = f"https://login.swissid.ch/api-login/anomaly-detection/device-print?{qs}"
        ctx = prev_response.json()
        try:
            headers = dict(self.swissid_headers)
            headers["authId"] = ctx["tokens"]["authId"]
            return session.post(url, json=self._device_print(), headers=headers)
        except Exception as exc:
            raise PostcardCreatorException(f"anomaly-detection step failed: {ctx}") from exc

    def _device_print(self) -> dict[str, Any]:
        return {
            "appCodeName": "Mozilla",
            "appName": "Netscape",
            "appVersion": self.user_agent.replace("Mozilla/", ""),
            "fonts": {
                "installedFonts": (
                    "cursive;monospace;serif;sans-serif;fantasy;default;Arial;Courier;"
                    "Courier New;Georgia;Tahoma;Times;Times New Roman;Verdana"
                )
            },
            "language": "de",
            "platform": "Linux x86_64",
            "plugins": {"installedPlugins": ""},
            "product": "Gecko",
            "productSub": "20030107",
            "screen": {"screenColourDepth": 24, "screenHeight": 732, "screenWidth": 412},
            "timezone": {"timezone": -120},
            "userAgent": self.user_agent,
            "vendor": "Google Inc.",
        }

    def _access_token_legacy(self, session: Any, username: str, password: str) -> dict[str, Any]:
        verifier, challenge = self._pkce()
        session.get(
            "https://pccweb.api.post.ch/OAuth/authorization?" + self._authorize_query(challenge),
            allow_redirects=True,
            headers=self.legacy_headers,
        )
        idp_query = urllib.parse.urlencode(
            {
                "targetURL": "https://pccweb.api.post.ch/SAML/ServiceProvider/?redirect_uri="
                + _REDIRECT_URI,
                "profile": "default",
                "app": "pccwebapi",
                "inMobileApp": "true",
                "layoutType": "standard",
            }
        )
        url = "https://account.post.ch/idp/?login&" + idp_query
        session.post(
            url,
            data={"isiwebuserid": username, "isiwebpasswd": password, "confirmLogin": ""},
            allow_redirects=True,
            headers=self.legacy_headers,
        )
        resp = session.post(url, allow_redirects=True, headers=self.legacy_headers)
        soup = BeautifulSoup(resp.text, "html.parser")
        code = self._saml_to_code(session, soup)
        return self._exchange_code_for_token(session, code, verifier)

    def to_json(self) -> dict[str, Any]:
        return {
            "fetched_at": self.token_fetched_at,
            "token": self.token,
            "expires_in": self.token_expires_in,
            "type": self.token_type,
            "implementation": self.token_implementation,
        }
