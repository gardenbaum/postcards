# Robustness — retries, quota, structured logging

This document is the M5 reference for the project's behaviour
when the network wobbles, the daily quota is gone, or a job
fails to dispatch. It is meant to be read alongside
`docs/CONSTITUTION.md` — that document owns the *rules*,
this one owns the *implementation*.

## What M5 changed

| Area | Before M5 | After M5 |
|------|-----------|----------|
| Network errors | Bubbled up raw; the user saw a stack trace | Retry with full-jitter exponential backoff; if all attempts fail, the user sees an actionable message naming the failure cause and pointing at `--verbose` |
| Daily quota | `postcards quota` printed "no free postcard" and exited non-zero; nothing waited | `postcards quota --wait` blocks until the quota opens (with `--max-wait` cap); `--no-fail` opts out of the non-zero exit; the next-available timestamp is included in every quota-related error message |
| Errors | `str(exc)` surfaced verbatim; the user had to read the docs to figure out what to do | A single translator (`postcards.backend.messages.translate`) turns every typed backend exception into a sentence with an embedded next-step hint |
| Logs | Default WARNING; no documented `-v` levels | Default WARNING; `-v`/`-vv`/`-vvv` map to INFO/DEBUG/TRACE; every dispatch step emits an INFO/DEBUG/WARN line so `schedule run -vv` shows exactly where a job got stuck |

## Retry / backoff

`postcards.retry.with_retries()` is the only retry primitive
in the project. Every Swiss Post network call
(`backend.login`, `backend.quota`, `backend.send`) is wrapped
in it.

### Policy

The default policy, defined in `postcards.backend.swissid._DEFAULT_RETRY_POLICY`,
is:

| Field | Value | Meaning |
|-------|-------|---------|
| `attempts` | `4` | Four total attempts (one initial + three retries) |
| `base_delay` | `0.5` | First backoff window: `[0s, 0.5s)` |
| `multiplier` | `2.0` | Each subsequent window doubles |
| `max_delay` | `8.0` | Hard ceiling — the worst-case sleep is `8s` |

The sleep is drawn from the AWS "full jitter" formula:

```
sleep_n = random.uniform(0, min(max_delay, base * multiplier ** (n - 1)))
```

with `n` 1-based. So the maximum wall time for the worst-case
4-attempt burst is `0.5 + 1 + 2 + 4 = 7.5s` (plus the final
attempt's network round-trip), well under a minute.

### Classifier

Only `TransientBackendError` is retried by default. The
SwissID backend extends the classifier with the network-layer
exceptions (`requests.exceptions.ConnectionError`,
`Timeout`, `5xx HTTPError`) so a real-world network blip
survives the retry helper. `AuthenticationError` and
`QuotaExhaustedError` are **never** retried:

- Wrong credentials will fail the same way on every attempt.
- The quota is a daily budget; retrying within the same
  minute does not change the answer.

### Why hand-rolled instead of `tenacity`

The retry helper is ~120 lines of pure Python with no extra
dependencies. Adding `tenacity` would expand the dependency
surface for a feature with exactly one callsite per backend
method. The full set of retry-related tests
(`tests/test_retry.py`, 18 cases) is also shorter than the
configuration matrix `tenacity` would need.

### How to observe

Run any command with `--verbose` (or `-v`) to see the
per-attempt lines:

```
$ postcards send --backend mock --to alice --message hi -v
INFO  postcards.backend.swissid: send attempt 1/4
WARN  postcards.backend.swissid: send attempt 1/4 failed (TransientBackendError: connection reset); retrying in 0.42s
INFO  postcards.backend.swissid: send attempt 2/4
INFO  postcards.backend.swissid: send succeeded on attempt 2/4
```

A `-vv` (DEBUG) run additionally shows the underlying shim's
HTTP traffic; a `-vvv` (TRACE) run shows the retry helper's
internal classification decisions.

## Quota awareness

The free tier is **1 card / day** per SwissID account. Three
places in the project deal with that constraint:

### `postcards quota`

The primary surface. By default the command exits non-zero on
exhaustion so shell scripts see the failure; `--no-fail`
opts out. `--wait` blocks until the quota opens (capped by
`--max-wait`, default 24h), with `--poll` (default 30s)
controlling how often the backend is asked.

```
$ postcards quota --backend mock --wait --max-wait 60
no free postcard; next available at 2026-06-26T00:00:00+00:00
  quota exhausted; sleeping 30s (next attempt at 2026-06-25T01:23:45+00:00)
free postcard available now
```

### Schedule runner

When `schedule run` encounters a quota-exhausted job it does
**not** mark the job as failed — it reschedules the job to
the next UTC midnight (a safe approximation of "the next
quota window" that matches the legacy behaviour) and records
the outcome as `SKIPPED_QUOTA` in the run summary. The job
keeps its `PENDING` status so the next `schedule run` picks
it up as soon as the window opens.

### Type-level contract

Every quota-related code path raises
`postcards.backend.exceptions.QuotaExhaustedError`, which
carries `next_available_at: datetime | None` and
`retention_days: int`. The CLI prints the timestamp; the
schedule runner uses it to compute the reschedule time.

## Structured logging

The single source of truth is `postcards.log`:

- `LOG_LEVEL_TRACE = 5` — defined here so other modules
  can `logger.log(LOG_LEVEL_TRACE, ...)` without redefining
  the level.
- `verbosity_to_level(count)` — the canonical mapping
  `-v` → INFO, `-vv` → DEBUG, `-vvv` → TRACE.
- `configure(level, stream, fmt, brief_fmt)` — installs a
  `StreamHandler` on the root logger that picks the brief
  format for WARNING+ and the standard format for INFO and
  below. The `postcard_creator` logger is pinned to
  `min(level, DEBUG)` so the Swiss Post library's own logs
  survive at `-vv`.

The format strings:

- `DEFAULT_FORMAT = "%(asctime)s %(name)s [%(levelname)s] %(message)s"`
- `BRIEF_FORMAT = "%(asctime)s %(name)s: %(message)s"`

Every log record flows through one of these. To redirect to
a file:

```python
from postcards.log import configure
configure(level=10, stream=open("postcards.log", "a"))
```

Or in a cron line:

```
*/5 * * * *  postcards schedule run --quiet 2>>/var/log/postcards.log
```

## Actionable error messages

The translator at `postcards.backend.messages.translate`
maps every typed backend exception to a `(message, exit_code)`
pair. The message ends with a hint about the next step; the
exit code follows the Unix convention (1 = general failure,
75 = `EX_TEMPFAIL` = "retry later").

| Exception | Exit code | Hint text |
|-----------|-----------|-----------|
| `AuthenticationError` | 1 | "Check POSTCARDS_USERNAME / POSTCARDS_PASSWORD, update your accounts file with `postcards accounts add`, or pass `--backend=mock`" |
| `QuotaExhaustedError` | 1 | "Use `postcards quota --wait` to block until the quota opens, or schedule the job for tomorrow with `postcards schedule add --at ...`" |
| `TransientBackendError` | 75 | "Retry with `--verbose` to see the per-attempt log, or pass `--backend=mock`" |
| `RetryExhaustedError` | 75 | "Check your network connection, run with `--verbose` to see the per-attempt retry log, or pass `--backend=mock`" |
| `BackendError` (other) | 1 | "backend error: <message>" |

The translator is consumed in two places:

1. **CLI commands** — `postcards.cli.backend_errors.raise_for_backend_error`
   converts the `(message, exit_code)` pair into a
   `typer.Exit(code=exit_code)` after printing
   `error: <message>` to stderr. Every command that touches
   the backend calls it from its top-level `try/except`.

2. **Schedule runner** — the runner's `except Exception`
   branch stores the translator's message in
   `job.last_error` and surfaces it in the
   `ExecutionResult.message` so `schedule list` /
   `schedule show <id>` shows the actionable hint instead
   of the raw `str(exc)`.

## Testing the M5 surface

The new modules ship with focused tests:

- `tests/test_retry.py` — 18 tests on the policy, the
  classifier, the attempt history, and the `KeyboardInterrupt`
  passthrough.
- `tests/test_log.py` — 12 tests on the verbosity mapping,
  TRACE registration, format switching at WARNING+, and the
  `make_record_capture` test helper.
- `tests/test_backend_exceptions.py` — 11 tests on the
  hierarchy, the typed payload, and the re-export from
  `postcards.backend`.
- `tests/test_backend_errors_cli.py` — 19 tests on the
  translator's exit-code map and message substrings.
- `tests/test_backend_integration.py` — adds four tests
  that drive the SwissID backend with a mocked shim and
  assert the retry helper actually retries transient
  failures.
- `tests/test_schedule_runner.py` — adds three tests
  asserting the runner's structured log lines and the
  actionable error messages.
- `tests/test_typer_cli.py` — adds five tests for the
  quota command's new flags and the error-translator wiring.

The total test count grew from 768 (M4 baseline) to 857
(M5 shipped).
