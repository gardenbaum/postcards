# Constitution

> Authoritative project rules. The gate (see [§3 The gate](#3-the-gate)) is
> enforced by `scripts/check.sh` on every push and pull request. The body of
> this document is normative — exceptions must be called out in a card body
> before they are made in code.

This constitution is established in **M0 (toolchain + CI + constitution)**
and amended by later milestones via the change-management rules in
[§6 Change management](#6-change-management).

---

## 1. The Swiss Post integration is unofficial

`postcards` wraps the **Swiss Postcard Creator consumer web flow** —
`postcard-creator` on PyPI — which is an **unofficial** integration:

- The free tier allows **1 card / day** per SwissID account.
- Authentication is **SwissID** with anomaly detection and 2FA, so it
  cannot be exercised unattended.
- The endpoint is a consumer web API, not a published developer API, and
  may break without notice.

### Invariants

1. **Backend isolation.** All Swiss Post network calls live behind a
   `Backend` interface (to be introduced in M1+). Every code path that
   calls the network MUST go through that interface, and the interface
   MUST have a mocked implementation that the integration test suite uses.
2. **No live auth in CI.** CI MUST NOT call the live Swiss Post endpoint
   or authenticate against SwissID. Live credentials are user-supplied
   and loaded from the runtime config layer; they are never read by
   tests.
3. **No API stability promises.** The project does not promise that the
   CLI works against any specific version of the Swiss Post backend.
   When the upstream breaks, the response is to fix the wrapper, not
   the wrapper's callers.

---

## 2. Secrets and credentials

`postcards` requires a SwissID username and password to send cards. These
are **sensitive user data** and must never enter the repository.

### Invariants

1. **Never commit secrets.** SwissID usernames, passwords, OAuth tokens,
   and the `POSTCARDS_KEY` credential-encryption key MUST NEVER appear
   in a tracked file, a CI log, or a commit message.
2. **Read from env / keyring / gitignored config.** The credential
   resolution order is:
   1. environment variables (`POSTCARDS_USERNAME`, `POSTCARDS_PASSWORD`,
      `POSTCARDS_KEY`);
   2. the OS keyring (via `keyring`);
   3. a user-local config file matched by `.gitignore`
      (`~/.config/postcards/config.json` or similar).
3. **Encrypted credentials are fine in tracked `config.json` files.**
   The repo MAY contain `config.json` files whose account credentials
   are encrypted under the user's `POSTCARDS_KEY`. The plaintext
   credential MUST NOT be reachable without the key.
4. **`.gitignore` covers `config.json` and `accounts.json` at the
   project root.** This is already the case in the legacy `.gitignore`;
   it must not be relaxed.

---

## 3. The gate

Code is mergeable when, and only when, all four of the following are
green on every supported Python version (3.12 and 3.13):

| Tool           | Command                                |
| -------------- | -------------------------------------- |
| ruff lint      | `ruff check .`                         |
| ruff format    | `ruff format --check .`                |
| mypy           | `mypy .`                               |
| pytest + cov   | `pytest` (with `--cov=postcards`)      |

`scripts/check.sh` runs all four in order and exits non-zero on the
first failure. The CI workflow at `.github/workflows/ci.yml` invokes
that script on a `py3.12/3.13` matrix for every push and pull request.

### M0 carve-out

The legacy `postcards/` package has many ruff and mypy violations. To
keep the gate green while the toolchain is being rolled out, M0 exempts
that package via:

- `tool.ruff.lint.per-file-ignores` for `postcards/**/*.py`
- `[[tool.mypy.overrides]]` for `postcards.*` with `ignore_errors = true`

These exemptions are **scheduled to be removed in M1** when the package
is brought up to the lint and type baseline. The exemption is a means,
not an end — new code does not get the same exemption.

---

## 4. Test discipline

- **Unit tests** for every non-trivial piece of logic.
- **At least one integration test** that drives a MOCKED Swiss Post
  backend end-to-end. The mock is the single source of truth for the
  backend's contract; if the live API drifts, we update the mock and
  re-record fixtures, not the test.
- **Live auth is never exercised in CI.** See [§1.2](#invariants).
- **Coverage is reported, not enforced at a fixed threshold in M0.**
  M1 introduces a real coverage floor; the `fail_under = 0` setting in
  M0 is intentional and temporary.

---

## 5. Code style and shape

- **Prefer small, typed, tested modules.** No file over ~500 lines
  without a refactor PR.
- **Public APIs are type-annotated.** Internal helpers should also be
  annotated; `disallow_untyped_defs` is on for `tests/` from M0
  onward and for `postcards/` from M1 onward.
- **The CLI stays usable.** `postcards --help` MUST work after every
  change. The console-script entry points declared in `pyproject.toml`
  are part of the package's contract.
- **Prefer Typer or Click for new subcommands.** The legacy code uses
  `argparse`; new code should use Typer (preferred) or Click. This is
  a soft preference, not a hard rule, and the M1 milestone may decide
  differently based on a spike.

---

## 6. Change management

- The constitution is amended via a card whose body says
  **"CONSTITUTION AMENDMENT"** and lists the exact text changes.
- The M0 version of this document is the seed. Any deviation from it
  in code MUST be justified in the relevant card body before the code
  lands.
- The constitution is the project root of policy; if a card body and
  the constitution disagree, the constitution wins unless the card is
  itself a CONSTITUTION AMENDMENT card.

---

_Last amended: M0._
