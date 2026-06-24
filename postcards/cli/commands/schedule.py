"""``postcards schedule`` — manage the local send queue.

This module is the user-facing surface for the
:class:`postcards.schedule` package. The CLI exposes the
queue as a small command group with five subcommands:

* ``postcards schedule add`` — enqueue a new (one-shot or
  recurring) send.
* ``postcards schedule list`` — print every job in the queue
  in a tabular form.
* ``postcards schedule show <id>`` — print a single job in
  detail.
* ``postcards schedule remove <id>`` — delete a job.
* ``postcards schedule run`` — fire every due job against the
  configured backend.

The ``add`` / ``list`` / ``show`` / ``remove`` subcommands are
purely local — they read and write the schedule book. ``run``
delegates to :func:`postcards.schedule.runner.run_due_jobs`
which is the only path that touches the backend. The runner
honours the 1-card/day quota by checking
:meth:`PostcardBackend.quota` before each dispatch and
rescheduling exhausted jobs to the next UTC midnight.

Cron usage
----------

The recommended cron invocation is::

    */5 * * * *  postcards schedule run --quiet

The ``--quiet`` flag suppresses the per-job summary so cron
mail does not balloon on a successful tick. Failures still go
to stderr so they show up in the cron log.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import typer

from postcards import __version__ as _postcards_version
from postcards.cli.app import app
from postcards.cli.errors import raise_cli_error
from postcards.cli.options import backend_option, password_option, username_option
from postcards.schedule import (
    FakeClock,
    JobStatus,
    RecurrenceRule,
    ScheduledJob,
    ScheduleBook,
    SystemClock,
    load_schedule_book,
    new_job_id,
    run_due_jobs,
    save_schedule_book,
)
from postcards.schedule.models import ScheduleError

# ``_postcards_version`` is imported to keep the build graph
# honest (the CLI uses it via ``--version``); the import itself
# is the coupling.
_ = _postcards_version


# ---------------------------------------------------------------------------
# ``postcards schedule`` sub-app
# ---------------------------------------------------------------------------

schedule_app = typer.Typer(
    name="schedule",
    help="Manage the local send queue (delayed + recurring sends).",
    no_args_is_help=True,
    rich_markup_mode=None,
    add_completion=False,
)
app.add_typer(schedule_app)


# ---------------------------------------------------------------------------
# ``add`` — queue a new job
# ---------------------------------------------------------------------------


def _parse_at(value: str) -> datetime:
    """Parse an ISO-8601 / ``YYYY-MM-DD HH:MM`` ``--at`` value.

    Accepts ISO-8601 (``2026-06-25T08:00:00``,
    ``2026-06-25 08:00:00``) and the common shorthand
    ``YYYY-MM-DD HH:MM``. A naive timestamp is treated as
    UTC; an aware timestamp is converted to UTC. Raises
    :class:`typer.Exit` (exit code 2) on malformed input so
    the user sees a friendly message instead of a Python
    traceback.
    """
    candidates = (
        value,
        value.replace(" ", "T"),
        value.replace("T", " "),
    )
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        else:
            parsed = parsed.astimezone(UTC)
        return parsed
    raise_cli_error(
        f"--at {value!r} is not a valid timestamp; expected "
        "'YYYY-MM-DD', 'YYYY-MM-DD HH:MM', or ISO-8601",
        exit_code=2,
    )


@schedule_app.command(
    name="add",
    help="Queue a new send (one-shot or recurring).",
    no_args_is_help=True,
)
def schedule_add_cmd(
    at: str | None = typer.Option(
        None,
        "--at",
        help=(
            "When to dispatch the first send (ISO-8601 or 'YYYY-MM-DD HH:MM'). "
            "Defaults to 'now'. Ignored when --recurring is set; the recurrence "
            "rule determines the next run time instead."
        ),
    ),
    recurring: str | None = typer.Option(
        None,
        "--recurring",
        help=(
            "Recurrence rule. Accepted values: 'none' (default for one-shot), "
            "'every:Nd' (every N days), 'weekly:mon[,tue,...]' (on the named "
            "weekdays). When set, --at is ignored; the first run is the next "
            "matching slot after now."
        ),
    ),
    to: str = typer.Option(
        ...,
        "--to",
        help="Name of a recipient in the address book.",
    ),
    sender: str | None = typer.Option(
        None,
        "--sender",
        help="Name of a sender in the address book. Optional.",
    ),
    picture: str | None = typer.Option(
        None,
        "-p",
        "--picture",
        help="Path or URL to the picture to print on the front of the card.",
    ),
    message: list[str] = typer.Option(
        None,
        "-m",
        "--message",
        help=(
            "Postcard message. Pass multiple times to assemble a multi-line "
            "message. Ignored when --message-template is given."
        ),
    ),
    message_template: str | None = typer.Option(
        None,
        "--message-template",
        help="Name of a message template. Mutually exclusive with --message.",
    ),
    var: list[str] | None = typer.Option(
        None,
        "--var",
        "-V",
        help="Template variable in KEY=VALUE form. Repeat to pass multiple.",
    ),
    username: str | None = username_option(),
    password: str | None = password_option(),
    backend: str | None = backend_option(),
) -> None:
    """Queue a new :class:`ScheduledJob` in the schedule book.

    The command validates the recipient / sender against the
    address book before writing the job so a typo surfaces as
    a usage error rather than a runtime failure on the next
    ``schedule run``.
    """
    from postcards.cli.commands.send import _parse_var as parse_var  # local import to avoid cycle
    from postcards.addressbook.storage import load_address_book
    from postcards.addressbook.models import AddressCategory

    if message_template is not None and message:
        raise_cli_error(
            "--message and --message-template are mutually exclusive",
            exit_code=2,
        )
    if picture is None and message is None and message_template is None:
        raise_cli_error(
            "either --picture, --message, or --message-template is required",
            exit_code=2,
        )

    book = load_address_book()
    recipient = book.find(to)
    if recipient is None:
        raise_cli_error(
            f"no address-book entry named {to!r}; create it with 'postcards addresses add' first",
            exit_code=2,
        )
    if recipient.category is not AddressCategory.RECIPIENT:
        raise_cli_error(
            f"address-book entry {to!r} is a {recipient.category.value}, not a recipient",
            exit_code=2,
        )
    if sender is not None:
        sender_entry = book.find(sender)
        if sender_entry is None:
            raise_cli_error(
                f"no address-book entry named {sender!r}",
                exit_code=2,
            )
        if sender_entry.category is not AddressCategory.SENDER:
            raise_cli_error(
                f"address-book entry {sender!r} is a {sender_entry.category.value}, not a sender",
                exit_code=2,
            )

    # Parse recurrence first so the failure mode is clear.
    rule = RecurrenceRule.from_string(recurring) if recurring else RecurrenceRule.one_shot()

    clock = SystemClock()
    now = clock.now()
    if rule.kind == "none":
        if at is None:
            next_run = now
        else:
            next_run = _parse_at(at)
    else:
        # ``advance`` returns strictly-after ``current``, so the
        # first fire of a recurring job is always in the future.
        next_run = rule.advance(now)
        if at is not None:
            typer.echo(
                f"note: --at {at!r} is ignored for recurring jobs; "
                f"first run scheduled for {next_run.isoformat()}",
                err=True,
            )

    template_vars: dict[str, str] = {}
    for raw in var or []:
        key, value = parse_var(raw)
        template_vars[key] = value

    job = ScheduledJob(
        id=new_job_id(),
        created_at=now,
        next_run_at=next_run,
        recurrence=rule,
        status=JobStatus.PENDING,
        recipient_name=to,
        sender_name=sender,
        picture=picture,
        message=" ".join(message) if message else None,
        message_template_name=message_template,
        template_variables=template_vars,
        username=username,
        password=password,
        backend=backend,
    )
    schedule_book = load_schedule_book().add(job)
    save_schedule_book(schedule_book)
    typer.echo(f"queued job {job.id}")
    typer.echo(f"  to:      {to}")
    if sender:
        typer.echo(f"  sender:  {sender}")
    if picture:
        typer.echo(f"  picture: {picture}")
    if message:
        typer.echo(f"  message: {' '.join(message)}")
    if message_template:
        typer.echo(f"  template: {message_template}")
        if template_vars:
            typer.echo(f"  vars:    {template_vars}")
    typer.echo(f"  next:    {next_run.isoformat()}")
    typer.echo(f"  repeat:  {rule.describe()}")


# ---------------------------------------------------------------------------
# ``list`` — show every job
# ---------------------------------------------------------------------------


@schedule_app.command(
    name="list",
    help="List every job in the schedule book.",
)
def schedule_list_cmd(
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter by status (pending/running/completed/failed/cancelled).",
    ),
) -> None:
    """Print a tabular summary of the schedule book."""
    book = load_schedule_book()
    if status:
        try:
            filter_status = JobStatus(status.lower())
        except ValueError:
            valid = ", ".join(s.value for s in JobStatus)
            raise_cli_error(
                f"unknown --status {status!r}; expected one of: {valid}",
                exit_code=2,
            )
        book = book.filter(status=filter_status)
    if book.is_empty():
        typer.echo("(no jobs)")
        return
    typer.echo(f"{'id':<34} {'status':<11} {'next_run_at':<26} {'recurrence':<24} recipient")
    for job in book:
        typer.echo(
            f"{job.id:<34} {job.status.value:<11} "
            f"{job.next_run_at.isoformat():<26} {job.recurrence.describe():<24} "
            f"{job.recipient_name}"
        )


# ---------------------------------------------------------------------------
# ``show`` — print a single job in detail
# ---------------------------------------------------------------------------


@schedule_app.command(
    name="show",
    help="Show the details of a single scheduled job.",
    no_args_is_help=True,
)
def schedule_show_cmd(
    job_id: str = typer.Argument(..., help="Job id (printed by 'schedule list')."),
) -> None:
    """Print the full state of ``job_id``."""
    book = load_schedule_book()
    try:
        job = book.get(job_id)
    except ScheduleError as exc:
        raise_cli_error(str(exc), exit_code=2)
    typer.echo(f"id:          {job.id}")
    typer.echo(f"status:      {job.status.value}")
    typer.echo(f"created:     {job.created_at.isoformat()}")
    typer.echo(f"next_run_at: {job.next_run_at.isoformat()}")
    typer.echo(f"recurrence:  {job.recurrence.describe()}")
    typer.echo(f"recipient:   {job.recipient_name}")
    if job.sender_name:
        typer.echo(f"sender:      {job.sender_name}")
    if job.picture:
        typer.echo(f"picture:     {job.picture}")
    if job.message:
        typer.echo(f"message:     {job.message}")
    if job.message_template_name:
        typer.echo(f"template:    {job.message_template_name}")
        if job.template_variables:
            typer.echo(f"vars:        {dict(job.template_variables)}")
    if job.username:
        typer.echo(f"username:    {job.username}")
    if job.backend:
        typer.echo(f"backend:     {job.backend}")
    if job.last_run_at:
        typer.echo(f"last_run_at: {job.last_run_at.isoformat()}")
    if job.last_confirmation:
        typer.echo(f"confirmation: {job.last_confirmation}")
    if job.last_error:
        typer.echo(f"last_error:  {job.last_error}")


# ---------------------------------------------------------------------------
# ``remove`` — delete a job
# ---------------------------------------------------------------------------


@schedule_app.command(
    name="remove",
    help="Remove a job from the schedule book.",
    no_args_is_help=True,
)
def schedule_remove_cmd(
    job_id: str = typer.Argument(..., help="Job id (printed by 'schedule list')."),
) -> None:
    """Delete ``job_id`` from the schedule book."""
    book = load_schedule_book()
    try:
        new_book = book.remove(job_id)
    except ScheduleError as exc:
        raise_cli_error(str(exc), exit_code=2)
    save_schedule_book(new_book)
    typer.echo(f"removed job {job_id}")


# ---------------------------------------------------------------------------
# ``retry`` — reset a failed job to pending
# ---------------------------------------------------------------------------


@schedule_app.command(
    name="retry",
    help="Reset a failed job back to pending so the next run picks it up.",
    no_args_is_help=True,
)
def schedule_retry_cmd(
    job_id: str = typer.Argument(..., help="Job id (printed by 'schedule list')."),
) -> None:
    """Reset ``job_id`` to :attr:`JobStatus.PENDING`."""
    book = load_schedule_book()
    try:
        job = book.get(job_id)
    except ScheduleError as exc:
        raise_cli_error(str(exc), exit_code=2)
    if job.status is JobStatus.PENDING:
        typer.echo(f"job {job_id} is already pending; nothing to do")
        return
    reset = job.with_status(
        JobStatus.PENDING,
        next_run_at=SystemClock().now(),
        last_error=None,
    )
    save_schedule_book(book.update(reset))
    typer.echo(f"reset job {job_id} to pending")


# ---------------------------------------------------------------------------
# ``run`` — dispatch every due job
# ---------------------------------------------------------------------------


def _select_runtime_backend(env_override: str | None) -> tuple[str, object]:
    """Return ``(env_var, payload)`` for ``select_backend``.

    Splits out the backend-selection plumbing so :func:`schedule_run_cmd`
    stays readable.
    """
    from postcards.backend.registry import select_backend

    env: dict[str, str] | None = None
    if env_override:
        env = {"POSTCARDS_BACKEND": env_override}
    return env_override or "", select_backend(env=env)


@schedule_app.command(
    name="run",
    help="Dispatch every due job against the configured backend.",
    no_args_is_help=True,
)
def schedule_run_cmd(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Walk the book and report what would be dispatched, without sending.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress the per-job summary on a successful tick.",
    ),
    backend: str | None = backend_option(),
    fake_now: str | None = typer.Option(
        None,
        "--fake-now",
        help=(
            "Override the current time (ISO-8601 or 'YYYY-MM-DD HH:MM'). "
            "Intended for testing the scheduler without sleeping; "
            "the runner uses this in place of the wall clock."
        ),
        hidden=True,
    ),
) -> None:
    """Walk the schedule book, dispatch every due job.

    The command is safe to invoke repeatedly — ``schedule run``
    is what a cron line should call every few minutes:

        */5 * * * *  postcards schedule run --quiet

    Quota-exhausted jobs are rescheduled to the next UTC midnight;
    failing jobs are marked :attr:`JobStatus.FAILED` and stay in
    the queue so ``schedule list`` shows them.
    """
    book = load_schedule_book()
    if book.is_empty():
        if not quiet:
            typer.echo("(no jobs)")
        return

    clock = FakeClock(_parse_at(fake_now)) if fake_now else SystemClock()
    backend_name, backend_instance = _select_runtime_backend(backend)

    def factory() -> object:
        return backend_instance

    new_book, results = run_due_jobs(
        book,
        clock=clock,
        backend_factory=factory,
        dry_run=dry_run,
    )

    if new_book is not book:
        save_schedule_book(new_book)

    if not quiet or any(r.outcome.value in {"failed", "skipped_quota"} for r in results):
        for result in results:
            typer.echo(f"[{result.outcome.value:<24}] {result.job_id}: {result.message}")

    failed_count = sum(1 for r in results if r.outcome.value == "failed")
    if failed_count:
        raise typer.Exit(code=1)


__all__ = ["schedule_app"]