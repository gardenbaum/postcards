#!/usr/bin/env python

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import random
import sys
import urllib.parse
import urllib.request
from argparse import RawTextHelpFormatter
from importlib import resources
from typing import Any

import inflection

from postcards import __version__
from postcards._vendor.postcard_creator import __version__ as postcard_creator_version
from postcards._vendor.postcard_creator import postcard_creator

LOGGING_TRACE_LVL = 5
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(name)s (%(levelname)s): %(message)s",
)

DEFAULT_KEY = "olMcxzq9Cq5lJpsoh4FvPKU"


class Postcards:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = self._create_logger(logger)
        self.default_key = os.environ.get("POSTCARDS_KEY", DEFAULT_KEY)

    def main(self, argv: list[str]) -> None:
        parser = self._build_root_parser(argv)
        subparsers = parser.add_subparsers(help="", dest="mode")
        self._build_subparser_generate(subparsers)
        self._build_subparser_send(subparsers)
        self._build_subparser_encrypt(subparsers)
        self._build_subparser_decrypt(subparsers)
        self.build_plugin_subparser(subparsers)
        self.logger.trace(argv)  # type: ignore[attr-defined]
        args = parser.parse_args()
        self._configure_logging(self.logger, args.verbose_count)
        self.logger.info(
            f"postcards {__version__} with postcard-creator {postcard_creator_version}"
        )
        self.logger.debug(args)

        if args.mode == "generate":
            self.do_command_generate(args)
        elif args.mode == "send":
            self.do_command_send(args)
        elif args.mode == "encrypt":
            self.do_command_encrypt(args)
        elif args.mode == "decrypt":
            self.do_command_decrypt(args)
        elif self.can_handle_command(args.mode):
            self.handle_command(args.mode, args)
        else:
            parser.print_usage()

    def do_command_generate(self, args: argparse.Namespace) -> None:
        target_location = str(os.path.join(os.getcwd(), "config.json"))

        if os.path.isfile(target_location):
            self.logger.error("config file already exist in current directory.")
            sys.exit(1)

        content = (
            resources.files(__name__).joinpath("template_config.json").read_text(encoding="utf-8")
        )
        with open(target_location, "w", encoding="utf-8") as file:
            file.write(content)

        self.logger.info(f"empty config file generated at {target_location}")

    def do_command_encrypt(self, args: argparse.Namespace) -> None:
        self.encrypt_credential(args.key, args.credential)

    def do_command_decrypt(self, args: argparse.Namespace) -> None:
        self.decrypt_credential(args.key, args.credential)

    def do_command_send(
        self,
        args: argparse.Namespace,
        *,
        config_dict: dict | None = None,
        accounts_dict: dict | None = None,
    ) -> None:
        """Run the ``send`` flow.

        Parameters
        ----------
        args:
            The :class:`argparse.Namespace` the CLI builds from
            Typer options. The shape is unchanged from the M1
            command surface — see :mod:`postcards.cli.commands.send`.
        config_dict:
            Optional in-memory replacement for the config file.
            When supplied, ``do_command_send`` skips the disk
            read at ``args.config_file[0]`` and uses ``config_dict``
            directly. The M4 ``postcards send --to NAME`` /
            ``--message-template NAME`` flow uses this to layer
            address-book and template resolutions on top of the
            on-disk config without writing a temporary file.
        accounts_dict:
            Optional in-memory replacement for the accounts
            file. Same semantics as ``config_dict`` but for
            ``args.accounts_file``.

        Returns
        -------
        None
            The method either succeeds silently (the card is
            sent / mocked-sent) or aborts the process via
            :func:`sys.exit`. It does not return a typed value.
        """
        if config_dict is None:
            config = self._read_json_file(args.config_file[0], "config")
        else:
            config = config_dict

        if accounts_dict is None:
            accounts_file: dict | None = None
            if args.accounts_file:
                accounts_file = self._read_json_file(args.accounts_file, "accounts")
        else:
            accounts_file = accounts_dict

        key_settings = self._parse_key(args)
        accounts = self._get_accounts(
            config=accounts_file if accounts_file is not None else config,
            key=key_settings["key"] if key_settings["uses_key"] else None,
            username=args.username,
            password=args.password,
        )
        random.shuffle(accounts)
        self._validate_config(config, accounts)

        plugin_payload = config.get("payload")
        if args and args.test_plugin:
            self.test_plugin_and_stop(plugin_payload or {}, args)

        self.logger.info("checking for valid accounts")
        wrappers, try_again_after = self._create_pcc_wrappers(
            accounts,
            stop_on_first_valid=not args.all_accounts,
        )
        if not wrappers:
            self.logger.error(f"no valid account given. try again after {try_again_after}")
            sys.exit(1)

        recipient_dict = config.get("recipient") or {}
        sender_dict = config.get("sender") or recipient_dict
        self.send_cards(
            pcc_wrappers=wrappers,
            recipient=recipient_dict,
            sender=sender_dict,
            mock=bool(args.mock),
            plugin_payload=plugin_payload,
            picture_stream=self._read_picture(args.picture) if args.picture else None,
            message=self._handle_message_argument(args.message),
            cli_args=args,
        )

    def send_cards(
        self,
        pcc_wrappers: list[Any],
        recipient: dict,
        sender: dict,
        mock: bool = False,
        plugin_payload: dict | None = None,
        message: str | None = None,
        picture_stream: Any | None = None,
        cli_args: argparse.Namespace | None = None,
    ) -> None:
        for wrapper in pcc_wrappers:
            self.send_card(
                wrapper,
                recipient,
                sender,
                mock=mock,
                plugin_payload=plugin_payload,
                message=message,
                picture_stream=picture_stream,
                cli_args=cli_args,
            )

    def send_card(
        self,
        pcc_wrapper: Any,
        recipient: dict,
        sender: dict,
        mock: bool = False,
        plugin_payload: dict | None = None,
        message: str | None = None,
        picture_stream: Any | None = None,
        cli_args: argparse.Namespace | None = None,
    ) -> None:
        # M3: the modern plugin system (``postcards.plugins``) is
        # config-driven. When ``config.json`` carries a
        # ``payload.plugin`` field, the new registry-based path is
        # taken; otherwise the legacy ``_is_plugin()`` branch
        # (legacy subclasses of :class:`Postcards`) is preserved for
        # backward compatibility with the ``postcards-folder`` /
        # ``postcards-yaml`` / ... console scripts.
        plugin_result = self._resolve_modern_plugin(plugin_payload or {}, cli_args=cli_args)
        if plugin_result is not None:
            result_image, result_message = plugin_result
            if not message and result_message:
                message = result_message
            if not picture_stream and result_image is not None:
                picture_stream = result_image
        elif self._is_plugin():
            cli_args_to_pass: argparse.Namespace = (
                cli_args if cli_args is not None else argparse.Namespace()
            )
            img_and_text = self.get_img_and_text(plugin_payload or {}, cli_args=cli_args_to_pass)

            if not message:
                message = img_and_text["text"]
            if not picture_stream:
                picture_stream = img_and_text["img"]

        card = postcard_creator.Postcard(
            message=message or "",
            recipient=self._create_recipient(recipient),
            sender=self._create_sender(sender),
            picture_stream=picture_stream,
        )

        self.logger.info("uploading postcard to server")
        try:
            self.delegate_send_free_card(pcc_wrapper, card, mock_send=mock)
        except Exception as e:
            self.logger.fatal("can not send postcard: " + str(e))
            raise

        if mock:
            self.logger.info("postcard not sent because of mock=True")
        else:
            self.logger.info("postcard is successfully sent")

    def _resolve_modern_plugin(
        self,
        plugin_payload: dict,
        cli_args: argparse.Namespace | None = None,
    ) -> tuple[Any, str | None] | None:
        """Apply the M3 plugin API to ``plugin_payload`` if requested.

        Returns ``None`` when ``plugin_payload`` does not declare a
        modern plugin (no ``plugin`` key, or the key is empty).
        Otherwise returns ``(picture_stream, message)`` from the
        plugin's :meth:`Plugin.render`. ``message`` may be ``None``
        when the plugin did not produce one.
        """
        name = plugin_payload.get("plugin") if plugin_payload else None
        if not isinstance(name, str) or not name:
            return None

        # Import inside the method so importing :mod:`postcards.postcards`
        # does not pull the modern plugin stack on every legacy use.
        from postcards.plugins import PluginContext, load_plugin
        from postcards.plugins.errors import PluginError

        # The ``plugin`` key is reserved for the plugin name; the
        # rest of the payload is forwarded to ``configure()``.
        plugin_config = {k: v for k, v in plugin_payload.items() if k != "plugin"}

        # Translate argparse.Namespace into the plugin context's
        # mapping. Plugins see a plain dict of option names instead
        # of the legacy argparse.Namespace.
        options: dict[str, Any] = {}
        if cli_args is not None:
            for attr in (
                "keyword",
                "safe_search",
                "category",
                "duplicate_file",
            ):
                value = getattr(cli_args, attr, None)
                if value is not None:
                    options[attr] = value

        try:
            plugin = load_plugin(name, plugin_config)
        except PluginError as exc:
            self.logger.error("plugin %s could not be loaded: %s", name, exc)
            sys.exit(1)

        try:
            result = plugin.render()
        except PluginError as exc:
            self.logger.error("plugin %s render failed: %s", name, exc)
            sys.exit(1)

        self.logger.info(
            "plugin %s produced a picture (message=%s)",
            name,
            "yes" if result.message else "no",
        )
        # Silence the unused-binding warning while keeping the
        # import for downstream readers.
        _ = PluginContext
        return (result.image, result.message)

    def delegate_send_free_card(self, pcc_wrapper: Any, postcard: Any, mock_send: bool) -> None:
        pcc_wrapper.send_free_card(postcard=postcard, mock_send=mock_send)

    def encrypt_credential(self, key: str, credential: str) -> None:
        self.logger.info("encrypted credential:")
        self.logger.info(self._encrypt(key, credential))

    def decrypt_credential(self, key: str, credential: str) -> None:
        self.logger.info("decrypted credential:")
        self.logger.info(self._decrypt(key, credential))

    def test_plugin_and_stop(
        self,
        payload: dict | None = None,
        args: argparse.Namespace | None = None,
    ) -> None:
        self.logger.info("running plugin only (--test-plugin)")
        self.get_img_and_text(payload or {}, cli_args=args or argparse.Namespace())
        sys.exit(0)

    def _create_pcc_wrappers(
        self,
        accounts: list[dict],
        stop_on_first_valid: bool = True,
    ) -> tuple[list[Any], str]:
        pcc_wrappers: list[Any] = []
        try_again_after = ""

        for account in accounts:
            token = postcard_creator.Token()
            if token.has_valid_credentials(account.get("username"), account.get("password")):
                pcc = postcard_creator.PostcardCreator(token)
                if pcc.has_free_postcard():
                    pcc_wrappers.append(pcc)
                    self.logger.info(f"account {account.get('username')} is valid")
                    if stop_on_first_valid:
                        break
                else:
                    next_quota = pcc.get_quota().get("next", "")
                    if not try_again_after or next_quota < try_again_after:
                        try_again_after = next_quota

                    self.logger.debug(
                        f"account {account.get('username')} is invalid. "
                        f"new quota available after {next_quota}."
                    )
            else:
                self.logger.warning(f"wrong user credentials for {account.get('username')}")

        return pcc_wrappers, try_again_after

    def _create_recipient(self, recipient: dict) -> Any:
        return postcard_creator.Recipient(
            prename=str(recipient.get("firstname") or ""),
            lastname=str(recipient.get("lastname") or ""),
            street=str(recipient.get("street") or ""),
            zip_code=str(recipient.get("zipcode") or ""),
            place=str(recipient.get("city") or ""),
            salutation=str(recipient.get("salutation") or ""),
        )

    def _create_sender(self, sender: dict) -> Any:
        return postcard_creator.Sender(
            prename=str(sender.get("firstname") or ""),
            lastname=str(sender.get("lastname") or ""),
            street=str(sender.get("street") or ""),
            zip_code=str(sender.get("zipcode") or ""),
            place=str(sender.get("city") or ""),
            # M4: forward the optional ``country`` field so
            # ``--sender NAME`` from the address book can
            # supply a return-address country. The shim's
            # Sender accepts ``country`` as an optional kwarg;
            # older configs without the field default to "".
            country=str(sender.get("country") or ""),
        )

    def _get_accounts(
        self,
        config: dict,
        key: str | None = None,
        username: str = "",
        password: str = "",
    ) -> list[dict]:
        accounts: list[dict] = []
        if username and password:
            self.logger.debug("using command line args as username and password")
            accounts.append({"username": username, "password": password})
        else:
            for account in config.get("accounts", []):
                stored_password = account.get("password")
                accounts.append(
                    {
                        "username": account.get("username"),
                        "password": (
                            self._decrypt(key, stored_password)
                            if key and stored_password
                            else stored_password
                        ),
                    }
                )
        return accounts

    def _parse_key(self, args: argparse.Namespace) -> dict:
        key = self.default_key
        uses_key = True

        if isinstance(args.key, tuple):
            uses_key = False
            self.logger.debug("using no key")
        elif args.key is None:
            key = self.default_key
            self.logger.debug("using default key")
        else:
            key = args.key
            self.logger.debug("using custom key")
        return {"uses_key": uses_key, "key": key}

    def _validate_config(self, config: dict, accounts: list[dict]) -> None:
        if not accounts:
            self.logger.error("no account set in config/accounts file")
            sys.exit(1)

        if not config.get("recipient"):
            self.logger.error("no recipient sent in config file")
            sys.exit(1)

        recipient = config.get("recipient") or {}
        required = ["firstname", "lastname", "street", "zipcode", "city"]
        if not all(recipient.get(field) for field in required):
            self.logger.error("recipient is invalid. required fields are " + str(required))
            sys.exit(1)

        sender = config.get("sender")
        if sender and not all(sender.get(field) for field in required):
            self.logger.error("sender is invalid. required fields are " + str(required))
            sys.exit(1)

    def _read_json_file(self, location: str, name: str) -> dict:
        location = self._make_absolute_path(location)
        self.logger.info(f"reading {name} file at {location}")

        if not os.path.isfile(location):
            self.logger.fatal(f"{name} file not found at {location}")
            sys.exit(1)
        try:
            with open(location, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            self.logger.error(f"can not parse {name} file {location} . is it valid json ?")
            sys.exit(1)

    def _read_picture(self, location: str) -> Any:
        """Return a binary stream of the picture at ``location``.

        M3: always returns an in-memory :class:`io.BytesIO` so the
        caller does not have to manage a file-handle lifetime.
        Previously this returned an open file handle, which
        leaked file descriptors when the caller (e.g. the M2
        integration tests) forgot to close it.
        """
        from io import BytesIO

        if location.startswith("http"):
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.11 "
                    "(KHTML, like Gecko) Chrome/23.0.1271.64 Safari/537.11"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Charset": "ISO-8859-1,utf-8;q=0.7,*;q=0.3",
                "Accept-Encoding": "none",
                "Accept-Language": "en-US,en;q=0.8",
                "Connection": "keep-alive",
            }
            self.logger.debug("reading picture from the internet at " + location)
            request = urllib.request.Request(location, None, headers)
            with urllib.request.urlopen(request) as response:
                return BytesIO(response.read())
        location = self._make_absolute_path(location)
        self.logger.debug("reading picture from " + location)
        if not os.path.isfile(location):
            self.logger.error("picture not found at " + location)
            sys.exit(1)
        with open(location, "rb") as fp:
            return BytesIO(fp.read())

    def _make_absolute_path(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return str(os.path.join(os.getcwd(), path))

    def _encrypt(self, key: str, msg: str) -> str:
        return self._encode(key.encode("utf-8"), msg.encode("utf-8")).decode("utf-8")

    def _decrypt(self, key: str, msg: str) -> str:
        try:
            return self._decode(key.encode("utf-8"), msg.encode("utf-8")).decode("utf-8")
        except Exception:
            self.logger.error("wrong key given, can not decrypt.")
            sys.exit(1)

    def _encode(self, key: bytes, clear: bytes) -> bytes:
        # https://stackoverflow.com/questions/2490334/simple-way-to-encode-a-string-according-to-a-password
        # XOR each byte of ``clear`` against the rotating key, then
        # base64-urlsafe-encode the result.
        enc = bytes((clear[i] + key[i % len(key)]) % 256 for i in range(len(clear)))
        return base64.urlsafe_b64encode(enc)

    def _decode(self, key: bytes, enc: bytes) -> bytes:
        decoded = base64.urlsafe_b64decode(enc)
        return bytes((decoded[i] - key[i % len(key)]) % 256 for i in range(len(decoded)))

    def _is_plugin(self) -> bool:
        return type(self).__name__ != "Postcards"

    def _create_logger(self, logger: logging.Logger | None = None) -> logging.Logger:
        logging.addLevelName(LOGGING_TRACE_LVL, "TRACE")
        logger = logger or logging.getLogger(inflection.underscore(type(self).__name__))
        _trace = lambda *args: logger.log(LOGGING_TRACE_LVL, *args)  # noqa: E731
        logger.trace = _trace  # type: ignore[attr-defined]
        return logger

    def _configure_logging(self, logger: logging.Logger, verbose_count: int = 0) -> None:
        # set log level to INFO going more verbose for each new -v
        # most verbose is level trace which is 5
        logger.setLevel(int(max(2.0 - verbose_count, 0.5) * 10))

        api_wrapper_logger = logging.getLogger("postcard_creator")
        if logger.level <= logging.DEBUG:
            api_wrapper_logger.setLevel(logging.DEBUG)
        if logger.level <= LOGGING_TRACE_LVL:
            api_wrapper_logger.setLevel(5)

    def _build_root_parser(self, argv: list[str]) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            formatter_class=RawTextHelpFormatter,
            description="Postcards is a CLI for the Swiss Postcard Creator",
        )
        parser.epilog = (
            "browse https://github.com/abertschi/postcards for documentation, \n"
            "sourcecode and bug reports"
        )

        parser.add_argument(
            "-v",
            "--verbose",
            dest="verbose_count",
            action="count",
            default=0,
            help="increases log verbosity for each occurrence.",
        )

        self.enhance_root_subparser(parser)
        return parser

    def _build_subparser_decrypt(self, subparsers: argparse._SubParsersAction) -> None:
        parser_decrypt = subparsers.add_parser("decrypt", help="decrypt credentials")
        parser_decrypt.add_argument("credential", help="credential to decrypt", action="store")
        parser_decrypt.add_argument(
            "-k",
            "--key",
            help="set a custom key to decrypt credential",
            default=self.default_key,
            action="store",
            dest="key",
        )

        self.enhance_decrypt_subparser(parser_decrypt)

    def _build_subparser_encrypt(self, subparsers: argparse._SubParsersAction) -> None:
        parser_encrypt = subparsers.add_parser(
            "encrypt", help="encrypt credentials to store in configuration file"
        )

        parser_encrypt.add_argument("credential", help="credential to encrypt", action="store")

        parser_encrypt.add_argument(
            "-k",
            "--key",
            help="set a custom key to encrypt credentials",
            action="store",
            default=self.default_key,
            dest="key",
        )

        self.enhance_encrypt_subparser(parser_encrypt)

    def _build_subparser_generate(self, subparsers: argparse._SubParsersAction) -> None:
        parser_generate = subparsers.add_parser(
            "generate",
            help="generate an empty configuration file",
            description="generate an empty configuration file",
        )
        self.enhance_generate_subparser(parser_generate)

    def _build_subparser_send(self, subparsers: argparse._SubParsersAction) -> None:
        parser_send = subparsers.add_parser(
            "send", help="send postcards", description="send postcards"
        )
        parser_send.add_argument(
            "-c",
            "--config",
            nargs=1,
            required=True,
            type=str,
            help="location to the configuration file (default: ./config.json)",
            default=[os.path.join(os.getcwd(), "config.json")],
            dest="config_file",
        )

        parser_send.add_argument(
            "-a",
            "--accounts-file",
            default=False,
            help="location to a dedicated file containing postcard creator accounts",
            dest="accounts_file",
        )

        parser_send.add_argument(
            "-p",
            "--picture",
            required=not self._is_plugin(),
            help="postcard picture. path to an URL or image on disk",
            dest="picture",
        )

        parser_send.add_argument(
            "-m",
            "--message",
            default="",
            type=str,
            nargs=1,
            help="postcard message. you can use HTML tags to format the message (e.g. <br/>).",
            dest="message",
        )

        parser_send.add_argument(
            "--mock",
            action="store_true",
            help="do not submit postcard. useful for testing",
            dest="mock",
        )

        parser_send.add_argument(
            "--test-plugin",
            action="store_true",
            help="run plugin without configuration validation. useful for testing",
            dest="test_plugin",
        )

        parser_send.add_argument(
            "--username",
            default="",
            type=str,
            help="username credential. otherwise set in config or accounts file",
            dest="username",
        )

        parser_send.add_argument(
            "--password",
            default="",
            type=str,
            help="password credential. otherwise set in config or accounts file",
            dest="password",
        )

        parser_send.add_argument(
            "--all-accounts",
            action="store_true",
            help="run send command as often as valid accounts available",
            dest="all_accounts",
        )

        parser_send.add_argument(
            "-k",
            "--key",
            nargs="?",
            metavar="KEY",
            default=(None,),
            help=(
                "use this argument if your credentials are stored encrypted in configuration file. \n"
                "set your custom key if you are not using default key. \n"
                "(i.e. --key PASSWORD instead of --key)"
            ),
            dest="key",
        )
        self.enhance_send_subparser(parser_send)

    def _handle_message_argument(self, message: Any) -> str:
        if isinstance(message, list):
            return " ".join(str(x) for x in message)
        if isinstance(message, str):
            return message
        return ""

    def get_img_and_text(self, plugin_payload: dict, cli_args: argparse.Namespace) -> dict:
        """
        To be overwritten by a plugin
        :param plugin_payload: plugin config from config file
        :param cli_args: parser args added in Postcards.enhance_send_subparser. See docs of argparse
        :return: an image and text
        """
        return {"img": None, "text": None}  # structure of object to return

    def build_plugin_subparser(self, subparsers: argparse._SubParsersAction) -> None:
        pass

    def enhance_root_subparser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def enhance_generate_subparser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def enhance_send_subparser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def enhance_encrypt_subparser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def enhance_decrypt_subparser(self, parser: argparse.ArgumentParser) -> None:
        pass

    def can_handle_command(self, command: str) -> bool:
        return False

    def handle_command(self, command: str, args: argparse.Namespace) -> None:
        pass


def main(argv: list[str] | None = None) -> None:
    p = Postcards()
    p.main(sys.argv if argv is None else argv)


if __name__ == "__main__":
    main(sys.argv)
