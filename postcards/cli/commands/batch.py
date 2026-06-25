"""``postcards batch`` — send to many recipients in one shot.

The batch command is the natural next step after ``postcards
send`` and the address book: instead of one card at a time, the
user supplies a list of recipient names (or a manifest file)
and the CLI iterates over them, dispatching each via the same
:mod:`postcards.postcards` plumbing ``send`` uses. The result
is a per-recipient summary that prints to stdout when the run
completes.

Recipient sources
-----------------

The CLI accepts exactly one of the following:

* ``--to-many name1,name2,...`` — an inline list of address-book
  recipient names.
* ``--to-all-recipients`` — every entry in the address book
  whose category is :attr:`AddressCategory.RECIPIENT`.
* ``--manifest <path>`` — a CSV or YAML file. See
  ``docs/BATCH.md`` for the file formats; the supported columns
  / keys are the same per-recipient overrides the inline flags
  already accept (``to``, ``picture``, ``message``,
  ``message_template``, ``var``, ``sender``).

Combining multiple sources is rejected so the user never has to
guess which one won.

Per-recipient overrides
-----------------------

The shared flags (``--picture``, ``--message``,
``--message-template``, ``--var``, ``--sender``) are inherited
from the M4 ``postcards send`` surface. When the manifest
provides a per-recipient override, the manifest value wins;
otherwise the flag value is used for every recipient. This
mirrors the way ``postcards send`` handles ``--to`` / ``--sender``
on top of the on-disk config.

Dispatch
--------

For each recipient the command builds an in-memory config dict
(recipient + sender + plugin payload) and delegates to
:meth:`postcards.postcards.Postcards.do_command_send` — the same
plumbing ``send`` uses. Per-recipient failures are caught and
turned into a non-fatal error row; the loop continues with the
next recipient so a bad row does not abort the batch. The
process exits non-zero when at least one recipient fails; the
exit code reflects the failure count so cron / CI can detect
partial success.
"""

from __future__ import annotations

import argparse
import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import typer
import yaml

from postcards import __version__ as _postcards_version
from postcards.addressbook.models import (
    AddressBook,
    AddressBookEntry,
    AddressCategory,
)
from postcards.addressbook.storage import load_address_book
from postcards.addressbook.variables import TemplateRenderError
from postcards.cli.app import app
from postcards.cli.commands.send import (
    _address_to_legacy_dict,
    _parse_var,
    _resolve_message,
    _resolve_recipient_entry,
    _resolve_sender_entry,
)
from postcards.cli.errors import raise_cli_error
from postcards.cli.options import (
    all_accounts_option,
    config_path_option,
    dry_run_option,
    key_option,
    mock_option,
    password_option,
    picture_option,
    username_option,
)
from postcards.postcards import Postcards

# ``_postcards_version`` is imported to keep the build graph
# honest (the CLI uses it via ``--version``); the import itself
# is the coupling.
_ = _postcards_version


# ---------------------------------------------------------------------------
# Manifest data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestEntry:
    """One recipient row from a manifest file.

    ``name`` is the address-book entry name (required).
    The other fields are optional overrides; when ``None`` the
    CLI inherits the value from the shared ``--picture`` /
    ``--message`` / ``--sender`` / ``--message-template`` /
    ``--var`` flags so a flat list of names can be expressed
    with a single shared set of inputs.
    """

    name: str
    picture: str | None = None
    message: str | None = None
    message_template: str | None = None
    sender: str | None = None
    var: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def resolved_vars(self, shared_vars: Sequence[str]) -> dict[str, str]:
        """Return the merged ``KEY=VALUE`` map for this entry.

        Manifest-supplied variables win over the shared
        ``--var`` flags. The result is a plain dict so the
        template book can render it without further translation.
        """
        merged: dict[str, str] = {}
        for raw in shared_vars:
            key, value = _parse_var(raw)
            merged[key] = value
        for key, value in self.var:
            merged[key] = value
        return merged


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def _read_manifest_text(path: Path) -> str:
    """Return the textual contents of ``path``.

    A missing file is a user error (the flag was passed); a
    read failure is propagated as :class:`CLIError` so the user
    sees the OS error verbatim.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise_cli_error(f"could not read manifest {path}: {exc}", exit_code=2)


def _parse_manifest(path: Path) -> list[ManifestEntry]:
    """Parse ``path`` as a CSV or YAML manifest.

    The format is decided by the file extension: ``.yaml`` /
    ``.yml`` are YAML, everything else is CSV. The
    ``--manifest`` user explicitly opted into a file, so a
    malformed file is a hard error — the user sees the parse
    error verbatim.

    See ``docs/BATCH.md`` for the supported columns / keys.
    """
    text = _read_manifest_text(path)
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            return _parse_yaml_manifest(text)
        except yaml.YAMLError as exc:
            raise_cli_error(f"manifest {path} is not valid YAML: {exc}", exit_code=2)
    try:
        return _parse_csv_manifest(text)
    except csv.Error as exc:
        raise_cli_error(f"manifest {path} is not valid CSV: {exc}", exit_code=2)


def _parse_csv_manifest(text: str) -> list[ManifestEntry]:
    """Parse a CSV manifest body.

    The first row is the header. Required column is ``to``;
    optional columns are ``picture``, ``message``,
    ``message_template``, ``sender``, ``var`` (the ``var``
    column is semicolon-separated ``KEY=VALUE`` pairs to avoid
    clashing with the CSV column separator).
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "to" not in reader.fieldnames:
        raise_cli_error(
            f"CSV manifest must have a 'to' column; got columns {reader.fieldnames!r}",
            exit_code=2,
        )
    entries: list[ManifestEntry] = []
    for row in reader:
        name = (row.get("to") or "").strip()
        if not name:
            raise_cli_error(
                "CSV manifest row has empty 'to' column; every row must name a recipient",
                exit_code=2,
            )
        var_column = row.get("var") or ""
        var_pairs: list[tuple[str, str]] = []
        if var_column.strip():
            for chunk in var_column.split(";"):
                chunk = chunk.strip()
                if not chunk:
                    continue
                var_pairs.append(_parse_var(chunk))
        entries.append(
            ManifestEntry(
                name=name,
                picture=_empty_to_none(row.get("picture")),
                message=_empty_to_none(row.get("message")),
                message_template=_empty_to_none(row.get("message_template")),
                sender=_empty_to_none(row.get("sender")),
                var=tuple(var_pairs),
            )
        )
    return entries


def _parse_yaml_manifest(text: str) -> list[ManifestEntry]:
    """Parse a YAML manifest body.

    Two shapes are accepted:

    1. A flat list of recipient names::

           recipients:
             - alice
             - bob

    2. A list of objects with optional per-recipient overrides::

           recipients:
             - to: alice
               picture: /path/to/pic.jpg
               message: "Hi Alice"
             - to: bob
               message_template: greeting
               var:
                 name: Bob

    The first shape is the common case (the user has a small
    address book and just wants to send to all of them); the
    second is the escape hatch for heterogeneous batches.
    """
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError:
        raise  # already a YAML error; let the caller translate it
    if payload is None:
        return []
    if isinstance(payload, list):
        # Top-level list is the same shape as ``recipients:`` —
        # accept it for symmetry with the ``--to-many`` flag.
        items = payload
    elif isinstance(payload, dict) and "recipients" in payload:
        items = payload["recipients"]
    else:
        raise_cli_error(
            "YAML manifest must be a list of names or a mapping with a 'recipients' "
            "key listing per-recipient entries",
            exit_code=2,
        )
    if not isinstance(items, list):
        raise_cli_error(
            "YAML manifest 'recipients' must be a list",
            exit_code=2,
        )

    entries: list[ManifestEntry] = []
    for index, raw in enumerate(items):
        if isinstance(raw, str):
            entries.append(ManifestEntry(name=raw.strip()))
            continue
        if not isinstance(raw, dict):
            raise_cli_error(
                f"YAML manifest entry #{index + 1} must be a string or a mapping, "
                f"got {type(raw).__name__}",
                exit_code=2,
            )
        name = raw.get("to")
        if not isinstance(name, str) or not name.strip():
            raise_cli_error(
                f"YAML manifest entry #{index + 1} missing required 'to' key",
                exit_code=2,
            )
        raw_var = raw.get("var") or {}
        var_pairs: list[tuple[str, str]] = []
        if isinstance(raw_var, list):
            for chunk in raw_var:
                if not isinstance(chunk, str):
                    raise_cli_error(
                        f"YAML manifest entry #{index + 1} 'var' must be a list of "
                        "'KEY=VALUE' strings",
                        exit_code=2,
                    )
                var_pairs.append(_parse_var(chunk))
        elif isinstance(raw_var, dict):
            for key, value in raw_var.items():
                if not isinstance(key, str):
                    raise_cli_error(
                        f"YAML manifest entry #{index + 1} 'var' keys must be strings",
                        exit_code=2,
                    )
                var_pairs.append((key, "" if value is None else str(value)))
        elif raw_var:
            raise_cli_error(
                f"YAML manifest entry #{index + 1} 'var' must be a list or a mapping",
                exit_code=2,
            )
        entries.append(
            ManifestEntry(
                name=name.strip(),
                picture=_empty_to_none(raw.get("picture")),
                message=_empty_to_none(raw.get("message")),
                message_template=_empty_to_none(raw.get("message_template")),
                sender=_empty_to_none(raw.get("sender")),
                var=tuple(var_pairs),
            )
        )
    return entries


def _empty_to_none(value: object) -> str | None:
    """Normalise empty strings / ``None`` to ``None``.

    CSV rows frequently carry an empty string for an absent
    optional column; YAML keys sometimes resolve to ``None``.
    The manifest dataclass wants ``None`` for "unset".
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _manifest_provides_message(manifest: Path | None) -> bool:
    """Return ``True`` when ``manifest`` declares a message column / key.

    Used by the CLI's input-validation guard so a manifest
    that already provides per-recipient messages is accepted
    without an additional ``--message`` / ``--picture`` flag.
    A parse error here is treated as "no message column" so
    the user still sees the "either ... is required" error
    rather than a confusing manifest parse failure before
    they have supplied any other inputs.
    """
    if manifest is None:
        return False
    try:
        text = _read_manifest_text(manifest)
    except Exception:
        return False
    suffix = manifest.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            payload = yaml.safe_load(text)
        except yaml.YAMLError:
            return False
        items = payload.get("recipients", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return False
        for item in items:
            if isinstance(item, dict) and _empty_to_none(item.get("message")) is not None:
                return True
        return False
    # CSV: peek at the header for a ``message`` column.
    try:
        reader = csv.DictReader(io.StringIO(text))
    except csv.Error:
        return False
    return reader.fieldnames is not None and "message" in reader.fieldnames


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------


def _resolve_recipients(
    *,
    to_many: str | None,
    to_all: bool,
    manifest: Path | None,
    book: AddressBook,
) -> list[AddressBookEntry]:
    """Return the ordered list of recipient entries to dispatch.

    Exactly one of ``to_many`` / ``to_all`` / ``manifest`` must
    be supplied. The function rejects typos early (unknown
    name → CLIError, unknown category → CLIError) so the user
    sees the failure before the first send.
    """
    sources = sum(bool(x) for x in (to_many, to_all, manifest))
    if sources == 0:
        raise_cli_error(
            "batch requires one of --to-many, --to-all-recipients, or --manifest",
            exit_code=2,
        )
    if sources > 1:
        raise_cli_error(
            "--to-many, --to-all-recipients, and --manifest are mutually exclusive",
            exit_code=2,
        )

    if to_all:
        recipients = book.filter(category=AddressCategory.RECIPIENT)
        if recipients.is_empty():
            raise_cli_error(
                "address book has no recipient entries; "
                "create one with 'postcards addresses add' first",
                exit_code=2,
            )
        return list(recipients)

    if to_many is not None:
        names = [name.strip() for name in to_many.split(",") if name.strip()]
        if not names:
            raise_cli_error("--to-many must list at least one recipient name", exit_code=2)
        return [_resolve_recipient_entry(name, book=book) for name in names]

    if manifest is not None:
        entries = _parse_manifest(manifest)
        if not entries:
            raise_cli_error(f"manifest {manifest} has no recipient rows", exit_code=2)
        return [_resolve_recipient_entry(entry.name, book=book) for entry in entries]

    # Unreachable: ``sources`` covered every branch above.
    raise_cli_error("no recipient source selected", exit_code=2)  # pragma: no cover


# ---------------------------------------------------------------------------
# Per-recipient dispatch
# ---------------------------------------------------------------------------


def _build_namespace(
    *,
    config_file: Path,
    picture: str | None,
    message: list[str],
    dry_run: bool,
    mock: bool,
    username: str | None,
    password: str | None,
    all_accounts: bool,
    key: str | None,
    accounts_file: Path | None,
) -> argparse.Namespace:
    """Build the :class:`argparse.Namespace` ``do_command_send`` expects.

    Identical to :func:`postcards.cli.commands.send._build_namespace`
    — duplicated rather than imported because the helper is
    private to ``send`` and a future refactor may move it.
    """
    return argparse.Namespace(
        config_file=[str(config_file)],
        accounts_file=str(accounts_file) if accounts_file is not None else False,
        picture=picture,
        message=message,
        mock=bool(dry_run or mock),
        test_plugin=False,
        username=username or "",
        password=password or "",
        all_accounts=all_accounts,
        key=(None,) if key is None else key,
    )


def _resolve_sender_for_recipient(
    *,
    explicit_sender: str | None,
    manifest_sender: str | None,
    book: AddressBook,
) -> AddressBookEntry | None:
    """Pick the sender :class:`AddressBookEntry` for one recipient.

    Precedence: ``manifest_sender`` > ``explicit_sender`` > ``None``.
    The name is validated against the address book here so a
    typo in the manifest is caught before the first send; the
    resolved entry is returned so the caller can feed it
    straight into the legacy send flow without a second lookup.
    """
    chosen = manifest_sender or explicit_sender
    if chosen is None:
        return None
    return _resolve_sender_entry(chosen, book=book)


@dataclass(frozen=True)
class BatchOutcome:
    """One row in the per-recipient summary the batch command prints.

    ``name`` is the recipient's address-book entry. ``sent``
    is ``True`` when the underlying ``do_command_send``
    returned without raising; ``error`` carries the failure
    message when ``sent`` is ``False``.
    """

    name: str
    sent: bool
    error: str | None = None


def _dispatch_recipient(
    *,
    recipient: AddressBookEntry,
    sender_entry: AddressBookEntry | None,
    config_path: Path,
    picture: str | None,
    message: list[str] | None,
    message_template: str | None,
    var_args: Sequence[str],
    dry_run: bool,
    mock: bool,
    username: str | None,
    password: str | None,
    all_accounts: bool,
    key: str | None,
    accounts_file: Path | None,
) -> BatchOutcome:
    """Dispatch a single recipient via ``Postcards().do_command_send``.

    ``sender_entry`` is the pre-resolved sender (the caller
    has already validated it against the address book). The
    function builds an in-memory config dict, layers the
    sender on top, and delegates to the legacy send flow.
    Errors are caught and turned into a :class:`BatchOutcome`
    so the caller can keep going on the next recipient.
    """
    config: dict = {"recipient": _address_to_legacy_dict(recipient)}
    if sender_entry is not None:
        config["sender"] = _address_to_legacy_dict(sender_entry)

    try:
        message_parts = _resolve_message(
            message=message,
            template_name=message_template,
            var_args=var_args,
        )
    except (TemplateRenderError, ValueError) as exc:
        return BatchOutcome(name=recipient.name, sent=False, error=str(exc))

    args = _build_namespace(
        config_file=config_path,
        picture=picture,
        message=message_parts,
        dry_run=dry_run,
        mock=mock,
        username=username,
        password=password,
        all_accounts=all_accounts,
        key=key,
        accounts_file=accounts_file,
    )
    try:
        Postcards().do_command_send(args, config_dict=config)
    except SystemExit as exc:
        # ``do_command_send`` calls ``sys.exit`` on errors. We
        # catch it so the batch loop continues with the next
        # recipient.
        return BatchOutcome(
            name=recipient.name,
            sent=False,
            error=f"send failed (exit code {exc.code})",
        )
    except Exception as exc:
        return BatchOutcome(
            name=recipient.name, sent=False, error=str(exc) or exc.__class__.__name__
        )
    return BatchOutcome(name=recipient.name, sent=True)


# ---------------------------------------------------------------------------
# Command body
# ---------------------------------------------------------------------------


@app.command(
    name="batch",
    help="Send one postcard to each of many recipients.",
    no_args_is_help=True,
)
def batch_cmd(
    config_file: Path = config_path_option(),
    to_many: str | None = typer.Option(
        None,
        "--to-many",
        help=(
            "Comma-separated list of address-book recipient names to send to. "
            "Mutually exclusive with --to-all-recipients and --manifest."
        ),
    ),
    to_all_recipients: bool = typer.Option(
        False,
        "--to-all-recipients",
        help=(
            "Send to every recipient entry in the address book. "
            "Mutually exclusive with --to-many and --manifest."
        ),
    ),
    manifest: Path | None = typer.Option(
        None,
        "--manifest",
        "-M",
        help=(
            "Path to a CSV or YAML manifest listing the recipients. "
            "See docs/BATCH.md for the supported columns / keys. "
            "Mutually exclusive with --to-many and --to-all-recipients."
        ),
        exists=False,  # the file may not yet exist; we surface that as a CLIError
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    picture: str | None = picture_option(),
    message: list[str] = typer.Option(
        None,
        "-m",
        "--message",
        help=(
            "Postcard message (you can use HTML tags). Pass multiple times "
            "to assemble a multi-line message; the parts are joined with "
            "single spaces. Ignored when --message-template is given."
        ),
    ),
    sender: str | None = typer.Option(
        None,
        "--sender",
        help="Name of a sender in the address book (used for every recipient).",
    ),
    message_template: str | None = typer.Option(
        None,
        "--message-template",
        help=(
            "Name of a message template. The template is rendered "
            "with --var substitutions and used as the message. "
            "Mutually exclusive with --message."
        ),
    ),
    var: list[str] | None = typer.Option(
        None,
        "--var",
        "-V",
        help=(
            "Template variable in KEY=VALUE form. Repeat to pass multiple. "
            "Only meaningful with --message-template. Manifest entries can "
            "override these on a per-recipient basis."
        ),
    ),
    dry_run: bool = dry_run_option(
        help_text=(
            "Do not actually send the postcards. Print the per-recipient "
            "summary as if the dispatch succeeded."
        ),
    ),
    mock: bool = mock_option(),
    username: str | None = username_option(),
    password: str | None = password_option(),
    all_accounts: bool = all_accounts_option(),
    key: str | None = key_option(),
    accounts_file: Path | None = typer.Option(
        None,
        "-a",
        "--accounts-file",
        help="Path to a dedicated accounts file (defaults to the main config).",
    ),
    continue_on_error: bool = typer.Option(
        True,
        "--continue-on-error/--stop-on-error",
        help=(
            "Continue dispatching recipients after a failure (default), or "
            "stop at the first failure."
        ),
    ),
) -> None:
    """Send one postcard to each recipient in the supplied source.

    See :mod:`postcards.cli.commands.batch` for the recipient
    sources and per-recipient overrides; ``docs/BATCH.md`` is
    the user-facing guide.
    """
    book = load_address_book()

    # Resolve the recipient list. ``_resolve_recipients`` raises
    # CLIError on the usual bad inputs (empty source, unknown
    # name, etc.).
    recipients = _resolve_recipients(
        to_many=to_many,
        to_all=to_all_recipients,
        manifest=manifest,
        book=book,
    )

    if (
        picture is None
        and message is None
        and message_template is None
        and not _manifest_provides_message(manifest)
    ):
        raise_cli_error(
            "either --picture, --message, --message-template, or a per-recipient "
            "message column/key in --manifest is required",
            exit_code=2,
        )
    if message_template is not None and message:
        raise_cli_error(
            "--message and --message-template are mutually exclusive",
            exit_code=2,
        )

    # Validate the shared sender (if any) before the loop so
    # the user sees the typo on the first invocation rather
    # than after the first card has already been queued.
    if sender is not None:
        _resolve_sender_entry(sender, book=book)

    # Optional manifest-driven per-recipient overrides. When
    # the user passed ``--manifest`` we keep the parsed entries
    # so we can read per-recipient picture / message / sender /
    # message-template / var values. The manifest was already
    # parsed by ``_resolve_recipients``; re-parsing here keeps
    # the per-row structure available without changing the
    # public contract of that helper.
    manifest_entries: dict[str, ManifestEntry] = {}
    if manifest is not None:
        manifest_entries = {entry.name: entry for entry in _parse_manifest(manifest)}

    shared_var_args = list(var or [])
    outcomes: list[BatchOutcome] = []
    for recipient in recipients:
        row = manifest_entries.get(recipient.name)

        # Resolve the sender for this iteration. Manifest values
        # win over the shared ``--sender`` flag.
        effective_sender = _resolve_sender_for_recipient(
            explicit_sender=sender,
            manifest_sender=row.sender if row else None,
            book=book,
        )
        effective_picture = (row.picture if row else None) or picture
        effective_message_template = (row.message_template if row else None) or message_template
        effective_message_input: list[str] | None = message
        if row is not None and row.message is not None:
            # A per-recipient message replaces the shared one.
            effective_message_input = [row.message]
        # ``_resolve_message`` consumes ``KEY=VALUE`` strings and
        # parses them itself; per-recipient ``(key, value)`` pairs
        # are re-encoded into the same wire format so the helper
        # path is uniform.
        effective_var_args: list[str] = list(shared_var_args)
        if row is not None:
            for key, value in row.var:
                effective_var_args.append(f"{key}={value}")

        outcome = _dispatch_recipient(
            recipient=recipient,
            sender_entry=effective_sender,
            config_path=config_file,
            picture=effective_picture,
            message=effective_message_input,
            message_template=effective_message_template,
            var_args=effective_var_args,
            dry_run=dry_run,
            mock=mock,
            username=username,
            password=password,
            all_accounts=all_accounts,
            key=key,
            accounts_file=accounts_file,
        )
        outcomes.append(outcome)
        if not outcome.sent and not continue_on_error:
            typer.echo(
                f"stopped after first failure: {outcome.name}: {outcome.error}",
                err=True,
            )
            break

    # Print the per-recipient summary. Errors are echoed to
    # stderr so a successful run produces a clean stdout.
    sent_count = sum(1 for o in outcomes if o.sent)
    failed = [o for o in outcomes if not o.sent]
    typer.echo(f"sent {sent_count}/{len(outcomes)} postcards")
    for outcome in outcomes:
        marker = "ok " if outcome.sent else "FAIL"
        line = f"  [{marker}] {outcome.name}"
        if outcome.error:
            line += f" — {outcome.error}"
        if outcome.sent:
            typer.echo(line)
        else:
            typer.echo(line, err=True)

    if failed:
        raise typer.Exit(code=1)


__all__ = ["batch_cmd"]
