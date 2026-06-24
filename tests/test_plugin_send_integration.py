"""End-to-end integration test for the M3 plugin system.

Drives the postcards CLI ``send`` flow against the MOCKED
Swiss Post backend, with a config-driven M3 plugin (``folder``
or ``folder_yaml``) producing the picture. Verifies that:

* the plugin was loaded via the new registry,
* the picture bytes produced by the plugin reached the
  ``Postcard`` object that the backend received,
* the message from the plugin (when present) made it through,
* the legacy ``_is_plugin()`` branch is NOT triggered when
  the ``payload.plugin`` field is set,
* a misconfigured plugin (``payload.plugin`` points at an
  unknown name) fails loudly rather than silently falling
  back to the legacy path.

The mock backend mimics the upstream ``Token`` /
``PostcardCreator`` just enough to drive the send flow without
ever touching the network. No live API is exercised at any
point.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from postcards._vendor.postcard_creator import Token
from postcards._vendor.postcard_creator.postcard_creator import PostcardCreatorBase
from postcards.postcards import Postcards

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class MockUpstream:
    """Patches the upstream Token / PostcardCreator for the duration of a test."""

    def __init__(self) -> None:
        self.calls_to_has_valid_credentials: list[tuple[str | None, str | None]] = []
        self.calls_to_send_free_card: list[dict[str, Any]] = []

    def __enter__(self) -> MockUpstream:
        backend = self

        def mock_has_valid_credentials(
            self: Token, username: str | None, password: str | None
        ) -> bool:
            backend.calls_to_has_valid_credentials.append((username, password))
            # Mark this Token as authenticated so the
            # ``PostcardCreator(token)`` constructor accepts it
            # (the shim raises ``PostcardCreatorException`` if
            # ``token.token is None``).
            self.token = "<mocked-token>"
            return True

        def mock_has_free_postcard(self: PostcardCreatorBase) -> bool:
            return True

        def mock_send_free_card(
            self: PostcardCreatorBase,
            postcard: Any,
            mock_send: bool = False,
            **_kwargs: object,
        ) -> None:
            backend.calls_to_send_free_card.append(
                {
                    "postcard": postcard,
                    "mock_send": mock_send,
                }
            )

        self._patchers: list[Any] = [
            patch.object(Token, "has_valid_credentials", mock_has_valid_credentials),
            patch.object(PostcardCreatorBase, "send_free_card", mock_send_free_card),
            patch.object(PostcardCreatorBase, "has_free_postcard", mock_has_free_postcard),
        ]
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        for p in self._patchers:
            p.stop()


def _write_config(path: Path, *, recipient: dict[str, str], payload: dict[str, Any]) -> Path:
    cfg = {
        "accounts": [{"username": "alice", "password": "secret"}],
        "recipient": recipient,
        "payload": payload,
    }
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def _png_bytes(color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _recipient() -> dict[str, str]:
    return {
        "firstname": "Bob",
        "lastname": "Recipient",
        "street": "Teststrasse 1",
        "zipcode": "8000",
        "city": "Zurich",
    }


def _build_args(
    config_path: Path, *, mock: bool = True, message: list[str] | None = None
) -> argparse.Namespace:
    return argparse.Namespace(
        config_file=[str(config_path)],
        accounts_file=False,
        picture=None,
        message=message or [""],
        mock=mock,
        test_plugin=False,
        username="",
        password="",
        all_accounts=False,
        key=(None,),
    )


# ---------------------------------------------------------------------------
# Plugin through send flow
# ---------------------------------------------------------------------------


def test_folder_plugin_drives_send_flow(tmp_path: Path) -> None:
    """A ``payload.plugin = "folder"`` config drives the send flow through the new plugin API."""
    folder = tmp_path / "pics"
    folder.mkdir()
    expected_bytes = _png_bytes("red")
    (folder / "first.png").write_bytes(expected_bytes)

    config_path = _write_config(
        tmp_path / "config.json",
        recipient=_recipient(),
        payload={"plugin": "folder", "folder": str(folder)},
    )

    with MockUpstream() as upstream:
        cards = Postcards()
        cards.do_command_send(_build_args(config_path))

    # The mock backend recorded exactly one send.
    assert len(upstream.calls_to_send_free_card) == 1
    sent = upstream.calls_to_send_free_card[0]
    assert sent["mock_send"] is True
    postcard = sent["postcard"]
    # The picture bytes from the plugin reached the backend.
    assert postcard.picture_stream is not None
    assert postcard.picture_stream.read() == expected_bytes


def test_folder_yaml_plugin_message_and_picture_reach_backend(tmp_path: Path) -> None:
    """``folder_yaml`` produces both picture AND message; both reach the backend."""
    folder = tmp_path / "pics"
    folder.mkdir()
    (folder / "first.png").write_bytes(_png_bytes("red"))
    yaml_path = tmp_path / "playlist.yaml"
    yaml_path.write_text("- 'Hi from Zurich'\n- first.png\n", encoding="utf-8")

    config_path = _write_config(
        tmp_path / "config.json",
        recipient=_recipient(),
        payload={
            "plugin": "folder_yaml",
            "folder": str(folder),
            "yaml": str(yaml_path),
        },
    )

    with MockUpstream() as upstream:
        cards = Postcards()
        cards.do_command_send(_build_args(config_path))

    assert len(upstream.calls_to_send_free_card) == 1
    postcard = upstream.calls_to_send_free_card[0]["postcard"]
    assert postcard.message == "Hi from Zurich"
    assert postcard.picture_stream is not None


def test_cli_message_overrides_plugin_message(tmp_path: Path) -> None:
    """``-m`` from the CLI wins over the message produced by the plugin."""
    folder = tmp_path / "pics"
    folder.mkdir()
    (folder / "first.png").write_bytes(_png_bytes("red"))
    yaml_path = tmp_path / "playlist.yaml"
    yaml_path.write_text("- 'plugin message'\n- first.png\n", encoding="utf-8")

    config_path = _write_config(
        tmp_path / "config.json",
        recipient=_recipient(),
        payload={
            "plugin": "folder_yaml",
            "folder": str(folder),
            "yaml": str(yaml_path),
        },
    )

    with MockUpstream() as upstream:
        cards = Postcards()
        cards.do_command_send(_build_args(config_path, message=["CLI overrides plugin"]))

    postcard = upstream.calls_to_send_free_card[0]["postcard"]
    assert postcard.message == "CLI overrides plugin"


def test_cli_picture_overrides_plugin_picture(tmp_path: Path) -> None:
    """``-p`` from the CLI wins over the picture produced by the plugin."""
    folder = tmp_path / "pics"
    folder.mkdir()
    (folder / "first.png").write_bytes(_png_bytes("red"))
    cli_picture = tmp_path / "cli.png"
    (cli_picture).write_bytes(_png_bytes("blue"))

    config_path = _write_config(
        tmp_path / "config.json",
        recipient=_recipient(),
        payload={"plugin": "folder", "folder": str(folder)},
    )

    args = _build_args(config_path)
    args.picture = str(cli_picture)

    with MockUpstream() as upstream:
        cards = Postcards()
        cards.do_command_send(args)

    postcard = upstream.calls_to_send_free_card[0]["postcard"]
    # The CLI picture is the blue one — verify by pixel.
    sent_picture = Image.open(postcard.picture_stream)
    assert sent_picture.getpixel((0, 0)) == (0, 0, 255)


def test_unknown_plugin_name_fails_loudly(tmp_path: Path) -> None:
    """An unregistered plugin name should NOT fall back to the legacy path."""
    config_path = _write_config(
        tmp_path / "config.json",
        recipient=_recipient(),
        payload={"plugin": "does-not-exist", "folder": "/tmp"},
    )

    with MockUpstream() as _upstream:
        cards = Postcards()
        with pytest.raises(SystemExit) as exc_info:
            cards.do_command_send(_build_args(config_path))
    # SystemExit with code 1 — the plugin loader logs the error and exits.
    assert exc_info.value.code == 1


def test_legacy_postcards_subclass_path_unchanged(tmp_path: Path) -> None:
    """A subclass of Postcards (legacy plugin) still drives the flow without payload.plugin."""

    class LegacySubclass(Postcards):
        """Minimal legacy plugin that just returns a fixed picture + message."""

        def get_img_and_text(
            self, plugin_payload: dict, cli_args: argparse.Namespace
        ) -> dict[str, Any]:
            return {"img": io.BytesIO(_png_bytes("green")), "text": "legacy text"}

    folder = tmp_path / "pics"
    folder.mkdir()
    config_path = _write_config(
        tmp_path / "config.json",
        recipient=_recipient(),
        payload={"folder": str(folder)},  # NOTE: no "plugin" key
    )

    with MockUpstream() as upstream:
        cards = LegacySubclass()
        cards.do_command_send(_build_args(config_path))

    postcard = upstream.calls_to_send_free_card[0]["postcard"]
    assert postcard.message == "legacy text"
    sent_picture = Image.open(postcard.picture_stream)
    assert sent_picture.getpixel((0, 0)) == (0, 128, 0)


def test_plugin_payload_without_plugin_key_falls_back_to_legacy(tmp_path: Path) -> None:
    """When ``payload`` exists but has no ``plugin`` key, the legacy path is taken."""

    class LegacySubclass(Postcards):
        def get_img_and_text(
            self, plugin_payload: dict, cli_args: argparse.Namespace
        ) -> dict[str, Any]:
            return {
                "img": io.BytesIO(_png_bytes("yellow")),
                "text": "legacy fallback",
            }

    folder = tmp_path / "pics"
    folder.mkdir()
    config_path = _write_config(
        tmp_path / "config.json",
        recipient=_recipient(),
        payload={"folder": str(folder)},  # no "plugin" key
    )

    with MockUpstream() as upstream:
        cards = LegacySubclass()
        cards.do_command_send(_build_args(config_path))

    postcard = upstream.calls_to_send_free_card[0]["postcard"]
    assert postcard.message == "legacy fallback"


# ---------------------------------------------------------------------------
# M3 round 2: url + local + unsplash end-to-end
# ---------------------------------------------------------------------------


def test_url_plugin_drives_send_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``url`` plugin's bytes reach the backend; network is mocked."""

    class _FakeResp:
        def __init__(self, body: bytes) -> None:
            self.content = body
            self.status_code = 200

    expected_bytes = _png_bytes("red")
    monkeypatch.setattr(
        "postcards.plugins.builtin.url.requests.get",
        lambda url, **_kw: _FakeResp(expected_bytes),
    )

    config_path = _write_config(
        tmp_path / "config.json",
        recipient=_recipient(),
        payload={
            "plugin": "url",
            "url": "https://example.com/pic.png",
            "message": "hi from the url plugin",
        },
    )

    with MockUpstream() as upstream:
        cards = Postcards()
        cards.do_command_send(_build_args(config_path))

    assert len(upstream.calls_to_send_free_card) == 1
    postcard = upstream.calls_to_send_free_card[0]["postcard"]
    assert postcard.message == "hi from the url plugin"
    assert postcard.picture_stream is not None
    assert postcard.picture_stream.read() == expected_bytes


def test_local_plugin_drives_send_flow(tmp_path: Path) -> None:
    """``local`` plugin's deterministic pick reaches the backend."""
    folder = tmp_path / "pics"
    folder.mkdir()
    expected_bytes = _png_bytes("green")
    (folder / "01.png").write_bytes(expected_bytes)
    (folder / "02.png").write_bytes(_png_bytes("blue"))

    config_path = _write_config(
        tmp_path / "config.json",
        recipient=_recipient(),
        payload={"plugin": "local", "folder": str(folder)},
    )

    with MockUpstream() as upstream:
        cards = Postcards()
        cards.do_command_send(_build_args(config_path))

    postcard = upstream.calls_to_send_free_card[0]["postcard"]
    assert postcard.picture_stream is not None
    assert postcard.picture_stream.read() == expected_bytes


def test_unsplash_plugin_drives_send_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``unsplash`` plugin's two-step API + download reaches the backend."""

    class _FakeResp:
        def __init__(
            self,
            *,
            body: bytes = b"",
            status_code: int = 200,
            json_body: object | None = None,
        ) -> None:
            self.content = body
            self.status_code = status_code
            self._json = json_body
            self.text = ""

        def json(self) -> object:
            assert self._json is not None
            return self._json

    expected_bytes = _png_bytes("purple")
    responses = iter(
        [
            _FakeResp(
                json_body={
                    "id": "abc",
                    "urls": {"regular": "https://images.unsplash.com/abc.jpg"},
                }
            ),
            _FakeResp(body=expected_bytes),
        ]
    )
    monkeypatch.setattr(
        "postcards.plugins.builtin.unsplash.requests.get",
        lambda url, **_kw: next(responses),
    )
    monkeypatch.setenv("POSTCARDS_UNSPLASH_ACCESS_KEY", "test-access-key")

    config_path = _write_config(
        tmp_path / "config.json",
        recipient=_recipient(),
        payload={"plugin": "unsplash", "query": "alps"},
    )

    with MockUpstream() as upstream:
        cards = Postcards()
        cards.do_command_send(_build_args(config_path))

    postcard = upstream.calls_to_send_free_card[0]["postcard"]
    assert postcard.picture_stream is not None
    assert postcard.picture_stream.read() == expected_bytes
