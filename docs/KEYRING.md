# `postcards keyring` — OS keyring for SwissID credentials

The `keyring` subcommand is the M5 user-facing surface for
the OS keyring. It complements the existing `accounts`
subcommand: `accounts` reads from a JSON config file,
`keyring` reads from the host's native secret store
(macOS Keychain, Windows Credential Manager, Linux
Secret Service, KWallet). The two are interchangeable
sources for `:class:ConfigLayer` (per the
`docs/CONSTITUTION.md` §2.2 resolution order) and the
rest of the CLI does not care which one supplied the
password.

## Why a keyring command at all

The constitution requires the keyring as a credential
source, but before M5 the keyring was reachable only
through the implicit lookup at send time — a user
who wanted to *set* a keyring password had to drop
into Python and call `keyring.set_password("postcards",
"alice", "...")` themselves. The `keyring` subcommand
makes the source first-class: a user can `set`,
`get` (presence only), `delete`, and `status` the
keyring entry from the CLI.

## Subcommands

### `postcards keyring set USERNAME [--password PW]`

Stores `PW` for `USERNAME` in the OS keyring under the
`postcards` service name. The command echoes the
password *length* so the user can confirm the call
worked without revealing the plaintext. If `--password`
is omitted, Typer prompts the user with input hidden
(so the password does not enter the shell history).

```bash
# with --password (suitable for scripts that already
# have the password in a variable)
postcards keyring set alice --password "$MY_SWISSID_PW"
# → stored password for 'alice' (length 12) in the keyring

# interactive (prompts for the password)
postcards keyring set alice
# Password: ****
# → stored password for 'alice' (length 12) in the keyring
```

### `postcards keyring get USERNAME`

Reports whether a password is stored for `USERNAME`.
The command prints `present (length N)` or `absent`;
it never prints the plaintext. The rationale is that
the user already typed the password into `set`; the
read path exists so scripts can confirm a value is
there, not so the user retrieves the plaintext via
the terminal. To copy a password for use in another
application, use the OS's own keyring UI (Keychain
Access, GNOME Keyring, ...) rather than the CLI.

```bash
postcards keyring get alice
# → present (length 12)
```

### `postcards keyring delete USERNAME`

Removes the keyring entry for `USERNAME`. Idempotent:
deleting a username with no entry is reported as
"no keyring entry" and the command exits 0.

```bash
postcards keyring delete alice
# → removed keyring entry for 'alice'

postcards keyring delete alice   # second time
# → no keyring entry for 'alice'
```

### `postcards keyring list`

Prints a one-line explanation rather than a list of
usernames. The OS keyring API (Keychain, Windows
Credential Manager, Secret Service, KWallet) does
not expose a "list entries for service X" call —
that would be a security hole on macOS / Windows
where the application is supposed to access only
its own entries. The subcommand therefore exists
to make the CLI shape consistent with
`accounts list` and to let `postcards doctor` reuse
the same printing helper.

To see which accounts are configured for the CLI,
use `postcards accounts list` (which reads from the
config file) or your OS's keyring UI (which shows
the OS-level entries).

### `postcards keyring status`

Prints a structured `:class:KeyringStatus` for the
active host. Exits 0 when the keyring is available
(returns the backend name), 1 when it is not
(returns a one-line reason). The command is the
same shape `:func:postcards.doctor` uses internally;
exposing it standalone lets the user run a one-line
check without invoking the full diagnostics suite.

```bash
postcards keyring status
# → keyring: available (backend='SecretService')

# on a headless host with no keyring backend
postcards keyring status
# → keyring: unavailable (no usable keyring backend on this host (...))
# exit code: 1
```

## Resolution order

The `:class:ConfigLayer` resolves credentials in this
order (per `docs/CONSTITUTION.md` §2.2):

1. `--username` / `--password` on the CLI
2. `POSTCARDS_USERNAME` + `POSTCARDS_PASSWORD` env vars
3. `POSTCARDS_USERNAME` + keyring lookup
4. The config-file `accounts` list

A `keyring set` populates source 3; the loader
picks it up at send time. The same `source` field
the loader returns (`"keyring"`) is what
`postcards doctor` renders in its report so the
user can see where the active account's password
lives.

## Security notes

* The `keyring` subcommand never prints a stored
  password to stdout. `get` reports presence and
  length only; `set` reports the length of the
  *incoming* password, not the value already in
  the keyring.
* The service name is hard-coded to `postcards`
  (:data:KEYRING_SERVICE) so the keyring entries
  are easy to find / delete in bulk from the OS's
  own UI.
* The `set` and `delete` paths surface backend
  failures as a CLI error (exit 1) with the
  underlying reason in the message. A "locked
  keyring" error from the OS, for example, ends
  up in the user's terminal as
  `error: failed to store password in the keyring: ...`.

## Backends

The `keyring` package (now a hard runtime
dependency, >=24) supports the following backends
out of the box:

| Host | Backend name | Notes |
|------|--------------|-------|
| macOS | `macOS` | Keychain Access is the user-facing UI |
| Windows | `Windows` | Credential Manager (Control Panel → User Accounts) |
| Linux (desktop) | `SecretService` | Requires `gnome-keyring` or `kwallet5` running |
| Linux (headless) | `fail` | The CLI still works; keyring is `warn` in `doctor` |
| Other | depends on the `keyring` package's discovery | Run `postcards keyring status` to see the active backend |

On a headless server (e.g. a CI runner), the
`status` command will report `unavailable` and
`doctor` will `warn` the keyring. The CLI's
credential layer falls through to the config-file
account or the env vars, so the rest of the
flow still works.

## When to use `keyring` vs `accounts`

| Scenario | Use |
|----------|-----|
| Single account, dev machine | Either; `keyring set` is more convenient (no config file to gitignore) |
| Multiple accounts, dev machine | `accounts add` for the per-account list, `keyring set` for the password of each |
| CI / cron job | `POSTCARDS_USERNAME` + `POSTCARDS_PASSWORD` env vars (the keyring is usually unavailable in CI) |
| Sharing a config with someone else | `accounts add` with `-k` for encrypted passwords; the `keyring` is per-user |
| Headless server | `accounts add` (config file) — the keyring is `warn` and not worth the install overhead |

The two are not mutually exclusive: a config file
with `username: alice, password: ""` plus a keyring
entry for `alice` works fine — the loader falls
through to the keyring when the config-file
password is empty.
