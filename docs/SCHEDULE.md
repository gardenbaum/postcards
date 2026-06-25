# Scheduling — `postcards schedule`

`postcards schedule` is the M4 send-later / recurring-sends
command group. It persists a local queue of jobs and runs
them against the configured backend on a schedule that you
control — by `cron`, by hand, or by `schedule run --fake-now`
during development.

The scheduler respects the upstream 1-card-per-day quota
implicitly: every dispatch goes through `backend.quota()`
first, and a quota-exhausted job is rescheduled to the next
UTC midnight instead of being dropped. A failed job stays
in the queue and is surfaced in `schedule list` / `schedule
show` so you can fix the underlying issue and retry.

## Where the queue lives

The schedule book is a single JSON file in the XDG data
directory:

| Variable        | Resolved path                                  |
| --------------- | ---------------------------------------------- |
| `$XDG_DATA_HOME` | `$XDG_DATA_HOME/postcards/schedule.json`      |
| fallback        | `$HOME/.local/share/postcards/schedule.json`   |

The file is written atomically (sibling temp file +
`os.replace`) so a crash mid-write cannot corrupt the queue.
You can override the directory with `POSTCARDS_DATA_DIR`.

## Concepts

A **job** is a deferred postcard send. It carries the
recipient / sender / message / picture inputs the runner
needs to dispatch without re-reading the CLI flags, plus:

* a **status** — `pending` / `running` / `completed` /
  `failed` / `cancelled`.
* a **`next_run_at`** — the wall-clock time the runner
  should fire the job next.
* a **recurrence rule** — `none` (one-shot), `every:Nd` (every
  N days), or `weekly:mon[,tue,...]` (on the named weekdays).

The runner walks the book on every `schedule run` and
dispatches every job whose status is `pending` and whose
`next_run_at` is at or before "now". Recurring jobs
auto-advance on success; quota-exhausted jobs are rescheduled
to the next UTC midnight.

## Commands

### `postcards schedule add`

Queue a new job. The required flags are `--to` (an
address-book recipient) and at least one of `--picture`,
`--message`, or `--message-template`.

```
postcards schedule add \
    --to alice \
    --message "Happy birthday!" \
    --username USER --password PASS
```

Add a job for a specific future time:

```
postcards schedule add \
    --at "2026-12-25 09:00" \
    --to alice \
    --message "Merry Christmas" \
    --username USER --password PASS
```

Add a recurring weekly Monday send:

```
postcards schedule add \
    --recurring weekly:mon \
    --to alice \
    --message-template greeting \
    --var name=Alice \
    --username USER --password PASS
```

Add a recurring every-7-days send at 08:30:

```
postcards schedule add \
    --recurring every:7d \
    --to bob \
    --message "Weekly check-in" \
    --username USER --password PASS
```

The `--at` flag is ignored for recurring jobs (the
recurrence rule determines the next run time); the CLI
prints a warning when both are supplied.

### `postcards schedule list`

Print every job in a tabular layout. `--status pending`
filters to pending jobs; other valid values are `running`,
`completed`, `failed`, `cancelled`.

```
postcards schedule list
postcards schedule list --status failed
```

### `postcards schedule show <id>`

Print a single job in full detail, including the
recurrence rule, the resolved send inputs, and any
bookkeeping from the last run.

```
postcards schedule show a1b2c3d4
```

### `postcards schedule remove <id>`

Delete a job from the book. Useful for one-shots that
fired successfully (they stay in the book in `completed`
state for audit purposes) or for cancelling a recurring
job you no longer want.

```
postcards schedule remove a1b2c3d4
```

### `postcards schedule retry <id>`

Reset a `failed` job back to `pending` so the next
`schedule run` picks it up. The next run time is set to
"now" so the retry fires immediately. No-op when the
job is already pending.

```
postcards schedule retry a1b2c3d4
```

### `postcards schedule run`

Walk the schedule book and dispatch every due job against
the configured backend. This is the only command that
touches the network — `add` / `list` / `show` / `remove`
/ `retry` are purely local.

```
postcards schedule run
postcards schedule run --dry-run          # preview without sending
postcards schedule run --quiet            # cron-friendly: errors only
postcards schedule run --backend mock     # exercise against the in-memory backend
```

`--fake-now ISO-TIMESTAMP` overrides the wall clock for the
duration of the run; this is intended for tests and is
hidden from `--help`.

## Cron usage

The recommended cron line is:

```
*/5 * * * *  cd ~/postcards && postcards schedule run --quiet
```

`--quiet` keeps the per-job summary off stdout on
successful ticks; failures still go to stderr so they
show up in the cron log. A five-minute interval is short
enough to feel responsive without spamming the backend
with quota checks.

## Quota handling

The runner calls `backend.login(...)` and `backend.quota()`
before every dispatch:

* If quota is available, the postcard is sent.
* If quota is exhausted, the job is rescheduled to the next
  UTC midnight (`next_available_at` from the upstream is
  used when present; UTC midnight is the fallback).
* A login failure or send error marks the job `failed` and
  surfaces the message in `last_error`. Recurring jobs are
  not advanced on failure — they stay at their original
  `next_run_at` so the next `schedule run` retries.

## Tests

The schedule runner is unit-tested against the in-memory
`MockBackend` (`postcards.backend.mock`) and the
`FakeClock` injected via `clock=...` — no live Swiss Post
call, no real time travel. See
`tests/test_schedule_runner.py` and
`tests/test_schedule_cli.py` for the contract.