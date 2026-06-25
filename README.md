# Postcards

> Unofficial app + CLI for the Swiss Postcard Creator. Compose a real
> physical postcard with a live WYSIWYG preview, or script it from the
> terminal.

[![PyPI version](https://img.shields.io/badge/pypi-4.0.0-blue.svg)](#)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](#)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-963%20passing-brightgreen.svg)](#)

`postcards` sends real postcards through the
[Swiss Postcard Creator](https://postcardcreator.post.ch) — the free
Swiss Post service that lets a SwissID account mail one physical
postcard per day to any address in the world.

Two front-ends, one engine:

- **`postcards app`** — an interactive web app (NiceGUI) with a **live,
  print-accurate WYSIWYG preview** of both sides of the card: the Front
  (A6 landscape, 3 mm bleed, safe area) and the Back (message, recipient
  / sender address, postage box) update as you type. This is the primary
  way to compose a card.
- **`postcards …`** — a lean, scriptable CLI for automation, batch sends
  and scheduling.

Every Swiss Post network call goes through a typed, swappable `Backend`
interface. The same code runs against real Swiss Post infrastructure and
against a MOCKED test backend; the mock is what the 963-test suite (and
CI) exercises — the live API is **never** called in tests.

> **Disclaimer.** This is an **unofficial** integration. The free
> tier is **1 card / day** per SwissID account. SwissID uses
> anomaly detection and 2FA, so live sends cannot run unattended
> in CI. See [`docs/CONSTITUTION.md`](docs/CONSTITUTION.md) for the
> project policy.

## Install

The project uses [`uv`](https://docs.astral.sh/uv/) for environments,
dependencies and running — `pyproject.toml` is the single source of
truth. (Do **not** use `pipx`.)

Run the interactive web app without a persistent install:

```sh
uvx --from '.[app]' postcards app      # builds an ephemeral env, opens the app
```

Or set up a project environment:

```sh
uv venv                                 # create .venv (Python 3.12+)
uv pip install -e '.[app]'              # CLI + web app (NiceGUI)
uv run postcards app                    # launch the app
```

For development (lint + types + tests, incl. the app's UI build test):

```sh
uv pip install -e '.[dev,app]'
uv run bash scripts/check.sh            # ruff + mypy + pytest gate
```

Install the CLI as a global tool (no app deps):

```sh
uv tool install .                       # exposes `postcards` on PATH
```

Or via Docker (no host Python needed; CLI/batch/scheduling — the web
app is not bundled in the image):

```sh
docker build -t postcards:dev .
docker run --rm -it postcards:dev --help
```

See [`docs/APP.md`](docs/APP.md) for the web-app guide,
[`docs/INSTALL.md`](docs/INSTALL.md) for per-OS install paths, and
[`docs/DOCKER.md`](docs/DOCKER.md) for the container recipe.

## The app

```sh
postcards app                # opens http://127.0.0.1:8080 in your browser
postcards app --port 9000 --no-browser
```

Upload or skip a picture, type the message, fill in the recipient and
sender — the Front and Back previews redraw live, showing exactly what
Swiss Post prints (A6, 3 mm bleed, safe area, postage box, address
zone). Toggle **Print guides** off for a clean look. Choose the
**Mock** backend (default — nothing is sent) or **SwissID** (live), keep
**Dry-run** on to validate without consuming your daily quota, then
**Send**. See [`docs/APP.md`](docs/APP.md).

> **2-factor auth is not supported** by this unofficial flow — a live
> send works only for SwissID accounts that log in with e-mail + password
> alone, and the account must have used the official Postcard Creator app
> at least once. SwissID anomaly detection / endpoint drift can also break
> a live send, so it stays a manual step. Credentials are read only for
> that send and never stored or committed. (For unattended/business use,
> Swiss Post's official [PostCard Creator API](https://developer.post.ch/en/technical-specifications-of-postcard-api)
> uses OAuth2 + a contract — not wired in here.)

## Quickstart

```sh
# 1. Verify the install.
postcards --version              # → postcards 4.0.0
postcards doctor                 # 5-check smoke test

# 2. Configure SwissID credentials (one of three options).
#    Option A — environment variables (preferred for CI / Docker).
export POSTCARDS_USERNAME="alice@example.ch"
export POSTCARDS_PASSWORD="..."      # never commit this
#    Option B — OS keyring.
postcards keyring set alice@example.ch
#    Option C — encrypted config file.
postcards generate --output config.json
postcards encrypt "$(read -rs PW && echo "$PW")" >> config.json  # append encrypted password

# 3. Send your first card.
postcards send \
    --config config.json \
    --to "Bahnhofstrasse 1, 8000 Zurich" \
    --picture https://picsum.photos/seed/postcard/600 \
    --message "Hello from the terminal!"
```

The free tier is **1 card / day**; `postcards send` will refuse a
second card with a clear error until the next UTC midnight. Use
`postcards quota` to check the current state.

## Commands

The CLI is built on [Typer](https://typer.tiangolo.com/). Run
`postcards --help` for the live list; the table below is the stable
shape.

| Command | Purpose |
| --- | --- |
| `postcards send` | Send a single postcard (the main entry point) |
| `postcards preview` | Show what `send` would do, without sending |
| `postcards quota` | Show the daily-quota state for the given account |
| `postcards status` | Print the resolved config path, backend, account |
| `postcards doctor` | Run 5 diagnostic checks (config, credentials, keyring, connectivity, mock backend) |
| `postcards generate` | Generate a starter `config.json` |
| `postcards encrypt` / `decrypt` | Encrypt / decrypt a credential for the config file |
| `postcards addresses` | Manage the persistent address book (`add`, `list`, `show`, `rm`) |
| `postcards templates` | Manage reusable message templates (`add`, `list`, `show`, `rm`) |
| `postcards batch` | Send one card to many recipients (`--to-many`, `--to-all-recipients`, `--manifest`) |
| `postcards schedule` | Add / list / run / remove recurring postcard jobs |
| `postcards keyring` | Manage SwissID credentials in the OS keyring (`set`, `delete`, `status`) |
| `postcards config` | Inspect / patch the merged config layer |
| `postcards app` | Launch the interactive WYSIWYG web app (opt-in via `postcards[app]`) |

The legacy plugin entry points (`postcards-folder`, `postcards-yaml`,
`postcards-pexels`, `postcards-chuck-norris`) are also installed for
backward compatibility; new code should use the unified
`postcards send --plugin <name>` interface.

## Configuration

The CLI reads its config in this order (later sources override
earlier):

1. **Built-in defaults.**
2. **Config file** (`--config <path>` or `$POSTCARDS_CONFIG`,
   default `./config.json`). Plain JSON; the file is matched by
   `.gitignore` and never committed.
3. **Environment variables** (`POSTCARDS_USERNAME`,
   `POSTCARDS_PASSWORD`, `POSTCARDS_KEY`, `POSTCARDS_CONFIG`,
   `POSTCARDS_DATA_DIR`, …).
4. **OS keyring** (via the `keyring` package; managed with
   `postcards keyring set`).

The credential-resolution contract is in
[`docs/CONSTITUTION.md` §2](docs/CONSTITUTION.md#2-secrets-and-credentials).
Plaintext SwissID passwords **must never** appear in a tracked file,
a CI log, or a commit message. The
`postcards encrypt` subcommand produces an entry whose password is
encrypted under `POSTCARDS_KEY`; that file is safe to commit.

A minimal config (generated by `postcards generate`):

```json
{
  "accounts": [
    {
      "username": "alice@example.ch",
      "password": "<ENCRYPTED: see 'postcards encrypt'>"
    }
  ],
  "sender": {
    "prename": "Alice",
    "lastname": "Muster",
    "street": "Bahnhofstrasse 1",
    "zip-code": "8000",
    "place": "Zurich"
  }
}
```

## Plugins

The CLI ships with seven image-source plugins. Use any of them via
`postcards send --plugin <name>`. The picture / message can always
be overridden with `--picture` / `--message`.

| Plugin | Source | Network | Notes |
| --- | --- | --- | --- |
| `folder` | Local folder of images | no | Default `--plugin folder` if `--folder` is set in config |
| `folder_yaml` | Folder + YAML manifest (text + image per card) | no | YAML entries are consumed in order |
| `local` | Single local image, repeated or rotated | no | Useful for "send the same picture N times" |
| `url` | HTTP(S) URL fetched at send time | yes | Single image; raises if the URL fails |
| `pexels` | `picsum.photos` placeholder | yes | No API key needed (M3 replaced the legacy Pexels API) |
| `unsplash` | Unsplash Source API | yes | Random image by keyword; no API key |
| `chuck_norris` | Static joke database + keyword extractor | no | Local-only; great for offline demos |

### Example: `folder` plugin

```sh
postcards send --plugin folder \
    --config config.json \
    --folder ./pictures \
    --message "From the terminal"
```

Pictures are moved to `./pictures/sent/` after a successful send
(`--move false` keeps them in place).

### Example: `url` plugin

```sh
postcards send --plugin url \
    --config config.json \
    --picture https://picsum.photos/seed/postcard/600 \
    --message "One specific image, fetched fresh"
```

### Build your own plugin

See [`docs/WRITING_PLUGINS.md`](docs/WRITING_PLUGINS.md) for the
plugin protocol. The short version: subclass `PluginBase`, implement
`fetch()`, register via the `postcards.plugins` entry-point group,
and `pip install` — no fork needed.

## Address book & templates

The address book lives under `$XDG_DATA_HOME/postcards/` (default
`~/.local/share/postcards/`). Templates live alongside it.

```sh
# Add a recipient.
postcards addresses add alice \
    --prename Alice --lastname Zuercher \
    --street "Bahnhofstrasse 1" --zip-code 8000 --place Zurich

# Add a reusable message template.
postcards templates add greeting --body 'Hi $name, greetings from Zurich'

# Send to the address-book entry, with template variables.
postcards send --config config.json \
    --to alice --picture pic.jpg \
    --message-template greeting --var name=Alice
```

See [`docs/ADDRESS_BOOK.md`](docs/ADDRESS_BOOK.md) for the full
guide.

## Batch send

`postcards batch` sends one card to each of many recipients,
honouring the daily quota. Three modes:

```sh
# Hand-picked list of names from the address book.
postcards batch --to-many alice,bob,charlie \
    --picture pic.jpg --message "Hi folks"

# Every recipient in the address book.
postcards batch --to-all-recipients \
    --picture pic.jpg --message "Happy coding!"

# YAML / CSV manifest with per-recipient overrides.
postcards batch --manifest ./birthdays.yaml
```

See [`docs/BATCH.md`](docs/BATCH.md) for the manifest format and
quota-aware retry behaviour.

## Scheduling

`postcards schedule` queues recurring postcard jobs. The runner
honours the daily quota automatically — a weekly job will skip a
day if the quota was already consumed.

```sh
# Queue a recurring weekly Monday postcard.
postcards schedule add \
    --recurring weekly:mon \
    --to alice \
    --message "Monday motivation!" \
    --username USER --password PASS

# Run the scheduler once (cron-friendly).
postcards schedule run --quiet
```

See [`docs/SCHEDULE.md`](docs/SCHEDULE.md) for the full model,
XDG paths, and a sample cron entry.

## OS keyring

The `postcards keyring` subcommand stores SwissID passwords in the
host's native secret store (macOS Keychain, Windows Credential
Manager, Linux Secret Service / KWallet).

```sh
postcards keyring set alice@example.ch   # prompts for password
postcards keyring status                 # which backend is active
postcards keyring delete alice@example.ch
```

The credential-resolution layer in the constitution (§2) reads the
keyring before falling back to a config file. See
[`docs/KEYRING.md`](docs/KEYRING.md) for the full guide.

## Diagnostics

`postcards doctor` runs five checks and exits non-zero on the first
failure:

1. **Config.** Loads and validates the config file.
2. **Credentials.** Resolves the active account's username +
   password (env, keyring, or encrypted config).
3. **Keyring.** The keyring backend is functional on this host.
4. **Connectivity.** The host can reach `postcardcreator.post.ch`.
5. **Mock backend.** The in-process `MockBackend` round-trips a
   fake send (the same code the integration tests exercise).

Use it after every config change, after a host migration, and as a
smoke test in CI:

```sh
postcards doctor --config config.json
```

See [`docs/DOCTOR.md`](docs/DOCTOR.md) for the full output format
and the `--skip-*` flags for offline use.

## Troubleshooting

### `postcards send` fails with `AuthenticationError`

SwissID uses anomaly detection + 2FA. New IPs / devices may trigger
a one-time code; complete the 2FA in your browser and retry. If it
persists for >24h, rotate your SwissID password — the upstream has
been known to lock accounts that send from a fresh IP without
warming up.

### `postcards send` fails with `QuotaExhaustedError`

The free tier is **1 card / day**. The CLI surfaces the
`next_available_at` timestamp from the upstream API. Use
`postcards quota --wait` to block until the next slot opens.

### `keyring` is unavailable

On Linux without a desktop session, install `gnome-keyring` or
`kwallet5`:

```sh
sudo apt install gnome-keyring
```

`postcards doctor` reports the active keyring backend and the common
failure modes (no D-Bus session, headless server, missing
`secretstorage` Python binding).

### Live sends fail in CI

By design. CI uses the `MockBackend`; live SwissID credentials are
never read by the test suite (see
[`docs/CONSTITUTION.md` §1](docs/CONSTITUTION.md#1-the-swiss-post-integration-is-unofficial)).

### Other issues

Search the [issue tracker](https://github.com/gardenbaum/postcards/issues),
or open a new issue with the output of `postcards doctor` (redact
usernames / addresses).

## FAQ

**Why an unofficial client?** The Swiss Postcard Creator has no
public API; this package reverse-engineers the consumer web flow.
When the upstream changes, we update the wrapper, not the
wrapper's callers.

**Why `uv` (and not `pip`/`pipx`)?** `uv` manages the environment,
dependencies and execution from a single `pyproject.toml`, and is fast
and reproducible. `uv tool install .` exposes the CLI on your `PATH` in
an isolated env; `uvx --from '.[app]' postcards app` runs the app with
no persistent install.

**Why a Docker image?** Hosts that cannot install Python 3.12+
system-wide (locked-down laptops, NAS devices, CI runners) get a
slim `python:3.13-slim` runtime. See
[`docs/DOCKER.md`](docs/DOCKER.md).

**Why mock the backend?** The constitution (§1) requires it: live
SwissID auth has anomaly detection + 2FA, so it cannot run in CI.
The mock is the single source of truth for the backend's contract;
when the upstream drifts, we update the mock, not the tests.

**Why a web app and not a TUI?** A postcard is a visual object, so the
one thing the tool must get right is *seeing the card before you send
it*. A browser canvas renders the real A6 layout — bleed, safe area,
postage box, address zone — pixel-for-pixel via the same Pillow renderer
the CLI's `postcards preview` uses; a terminal can't. The previous
Textual TUI only opened a temp PNG externally, so v4 replaced it with
the NiceGUI app. The `app` extra keeps NiceGUI off the default install
path. See [`docs/APP.md`](docs/APP.md).

## Development

```sh
# 1. Clone and bootstrap.
git clone https://github.com/gardenbaum/postcards.git
cd postcards
uv venv
uv pip install -e ".[dev,app]"

# 2. The gate (runs all four checks).
uv run bash scripts/check.sh

# 3. Run the tests.
uv run pytest

# 4. Run a single test file.
pytest tests/test_backend_integration.py -v
```

The gate (`scripts/check.sh`) runs four checks: `ruff check`,
`ruff format --check`, `mypy .`, and `pytest`. All four must exit
0 on every supported Python version (3.12 + 3.13) before a push;
see [`docs/CONSTITUTION.md` §3](docs/CONSTITUTION.md#3-the-gate).

The CI workflow at `.github/workflows/ci.yml` runs the gate on
both Python versions for every push and pull request.

## Contributing

Bug reports, plugin submissions, and small docs fixes are welcome.
For larger changes, open an issue first to discuss the design.

The constitution ([`docs/CONSTITUTION.md`](docs/CONSTITUTION.md))
is the project's policy root; deviations from it must be called
out in the relevant card body before they land in code.

## License

[MIT](LICENSE). Original author: [Andrin Bertschi](https://github.com/abertschi)
and contributors. Active development fork:
[gardenbaum/postcards](https://github.com/gardenbaum/postcards).

## Related

- [postcard_creator_wrapper](https://github.com/abertschi/postcard_creator_wrapper)
  — the original Python wrapper around the Swiss Postcard Creator.
- [Swiss Postcard Creator](https://postcardcreator.post.ch) — the
  upstream consumer web app this CLI drives.

## See also

- [`docs/INSTALL.md`](docs/INSTALL.md) — per-OS install paths.
- [`docs/DOCKER.md`](docs/DOCKER.md) — container build / run / mount.
- [`docs/RELEASE.md`](docs/RELEASE.md) — cutting a release.
- [`docs/CONSTITUTION.md`](docs/CONSTITUTION.md) — project policy.
- [`docs/ADDRESS_BOOK.md`](docs/ADDRESS_BOOK.md) — address book & templates.
- [`docs/BATCH.md`](docs/BATCH.md) — batch send.
- [`docs/SCHEDULE.md`](docs/SCHEDULE.md) — recurring jobs.
- [`docs/DOCTOR.md`](docs/DOCTOR.md) — diagnostics.
- [`docs/KEYRING.md`](docs/KEYRING.md) — OS keyring.
- [`docs/ROBUSTNESS.md`](docs/ROBUSTNESS.md) — retries / quota / logging.
- [`docs/WRITING_PLUGINS.md`](docs/WRITING_PLUGINS.md) — plugin protocol.
- [`CHANGELOG.md`](CHANGELOG.md) — per-version history.
