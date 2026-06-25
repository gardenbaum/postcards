# `postcards doctor` — diagnostics reference

The `doctor` command is the M5 entry point for "what is wrong
with my setup". It runs a fixed set of offline-friendly checks
and prints a tabular report; the exit code is non-zero when
any check fails. This document describes the checks, the
report's shape, and the scenarios where the doctor is the
right tool to reach for.

## Why a dedicated command

The upstream SwissID login is fragile: anomaly-detection,
2FA, and credential rejection all surface as the same
`AuthenticationError` from the backend, and a `KeyringError`
from the host's secret service can be a "you don't have a
keyring backend installed" issue, a "your keyring is
locked" issue, or a "you have never set a password for this
service" issue. Without a single command that walks the
user through every layer, debugging turns into a reading
exercise. The doctor is that command.

The doctor is also the canonical first step in any "send
fails" support thread: the report tells both the user and
the maintainer which layer is broken (config, credentials,
keyring, network, or pipeline plumbing).

## The five checks

The doctor runs the following checks in order. Each check
records a `:class:CheckResult` (status, summary, optional
hint) and the report aggregates them. The overall status
is the worst of any individual result (`fail` beats
`warn` beats `ok`).

| # | Check | What it does | Failure exit code |
|---|-------|--------------|-------------------|
| 1 | **config** | Verifies the resolved config file exists and parses as JSON. A missing file is `warn` (env-only credentials are still valid). | n/a (or `fail` if present but broken) |
| 2 | **credentials** | Tries to resolve at least one account via `:class:ConfigLayer`. The `source` field is rendered verbatim so the user can see *where* the loader looked. | `fail` |
| 3 | **keyring** | Probes the OS keyring via `:class:KeyringStore.status`. A missing backend is `warn` — the loader falls through to env / config-file credentials. | n/a (or `fail` if the backend is locked) |
| 4 | **connectivity** | A single `GET` to `https://www.postcard-creator.post.ch/` with a 5s timeout. The check never follows redirects, never submits credentials, and never reaches SwissID. | `fail` on timeout / connection error / 5xx |
| 5 | **mock login** | Drives `:class:MockBackend.login` with the resolved credentials. Skipped automatically if no account resolves. Opt-out via `--skip-mock`. | `fail` on any backend exception |

The connectivity check is opt-out via `--skip-network`
(useful in air-gapped CI). The mock-login check is
opt-out via `--skip-mock` (useful when the user is
deliberately running with a `--backend` override that
should not be exercised).

## Running the doctor

```bash
# default — runs all five checks
postcards doctor

# skip the network probe
postcards doctor --skip-network

# skip the mock-login smoke test
postcards doctor --skip-mock

# use a non-default config file
postcards doctor -c /path/to/config.json
```

### Example output

A successful run (all checks pass) on a host with a
working keyring backend and an env-credentialed account:

```
config         ok    config.json present at /home/user/.config/postcards/config.json
credentials    ok    1 account(s) resolved (source=env); active: 'alice'
keyring        ok    backend='SecretService'
connectivity   ok    https://www.postcard-creator.post.ch/ reachable (HTTP 200)
mock login     ok    login() succeeded against backend=mock (account='alice')
```

A run that fails on credentials (no env, no keyring, no
config-file accounts):

```
config         warn  no config file at /home/user/postcards/config.json (env-only credentials are still valid)
                       hint: run 'postcards config init' to create one, or set POSTCARDS_USERNAME / POSTCARDS_PASSWORD in your shell
credentials    fail  could not resolve any account: no accounts found in /home/user/postcards/config.json and POSTCARDS_USERNAME is not set
                       hint: set POSTCARDS_USERNAME and POSTCARDS_PASSWORD, or add an account with 'postcards accounts add', or store a password in the keyring with 'postcards keyring set'
keyring        warn  unavailable (backend='fail Keyring'); the 'keyring' Python package is not installed
                       hint: the keyring is one of three credential sources — the CLI will still work with POSTCARDS_USERNAME / POSTCARDS_PASSWORD or a config-file account
connectivity   ok    https://www.postcard-creator.post.ch/ reachable (HTTP 200)
mock login     fail  could not resolve credentials: no accounts found in /home/user/postcards/config.json and POSTCARDS_USERNAME is not set
                       hint: fix the credentials check above; the mock login depends on it
```

The exit code is `1` because at least one check failed.
A shell script can use the command as a gate:

```bash
if ! postcards doctor --skip-network --skip-mock > /dev/null; then
  echo "postcards setup is broken; run 'postcards doctor' for details"
  exit 1
fi
```

## Interpreting the report

| Symptom in the report | Likely root cause | What to do |
|-----------------------|-------------------|------------|
| `config` `fail` with "is not valid JSON" | The config file was hand-edited and has a syntax error | Fix the JSON near the line / col the report names; `postcards config init --force` to start fresh |
| `credentials` `fail` with "could not resolve any account" | None of the three credential sources (env, keyring, config-file) yielded a value | Set `POSTCARDS_USERNAME` / `POSTCARDS_PASSWORD` in the shell, or add an account with `postcards accounts add`, or store a password in the keyring with `postcards keyring set` |
| `keyring` `warn` with "no usable keyring backend" | The host has no Secret Service / Keychain / KWallet (e.g. a headless server) | The CLI still works; the `warn` is informational. If you want the keyring back, install the OS-level keyring (e.g. `gnome-keyring` on Linux) |
| `connectivity` `fail` with "timed out" | The Swiss Post host is unreachable from this network, or a corporate proxy is intercepting the request | Check your network; try a different network. The CLI does not yet honour `HTTPS_PROXY` (open follow-up) |
| `connectivity` `fail` with "503" | The Swiss Post consumer host is up but the landing page is unhealthy | Try again later; the upstream's status page at https://www.post.ch/ is the authoritative source |
| `mock login` `fail` with a backend exception name | A regression in the CLI plumbing — the mock backend is supposed to be a known-good fallback | File a bug with the report's exact text; the maintainer needs both the report and the `postcards --version` output |

## CI usage

A typical CI script runs the doctor before invoking
`postcards send` so a misconfigured runner fails fast
without burning the daily quota:

```yaml
- name: postcards smoke
  run: |
    postcards doctor --skip-network --skip-mock || {
      echo "postcards doctor failed; see report above" >&2
      exit 1
    }
```

The `--skip-network` and `--skip-mock` flags keep the
CI run hermetic — no outbound traffic, no real
authentication. The remaining three checks (config,
credentials, keyring) catch the most common CI misconfig
("you forgot to set `POSTCARDS_USERNAME` in the runner
secret store").

## When *not* to use the doctor

The doctor intentionally does not exercise the live
Swiss Post endpoint. If the doctor reports everything
green and the next `postcards send` still fails, the
problem is in the upstream flow itself (rate limiting,
anomaly detection, 2FA, ...) and the actionable
message comes from the typed error translator at
`postcards.backend.messages.translate`. The two are
complementary: doctor is for "is my setup right?",
the translator is for "what just went wrong upstream?".

For the upstream-flow branch, see the
`AuthenticationError` cases in `docs/ROBUSTNESS.md`.
