# Installing `postcards`

This document covers every supported way to install `postcards`.
If you only want the quick path, see the
[`pipx install .`](#recommended-pipx) recipe below — it is what the
M6 distribution tests verify.

## Requirements

- **Python 3.12 or 3.13.** The package's `requires-python = ">=3.12"`.
  Older versions are no longer supported (the legacy 3.6 reference in
  the README was a vestige of the pre-M0 codebase; see
  [§1 of the constitution](CONSTITUTION.md#3-the-gate) for the
  CI matrix).
- **A SwissID account** with access to the
  [Swiss Postcard Creator](https://postcardcreator.post.ch). The free
  tier allows **one card per day**; the CLI enforces that limit and
  will refuse to send a second card until the next UTC midnight.
- **Network access** to `postcardcreator.post.ch` for live sends.
  Everything else (`postcards doctor`, `postcards status`,
  `postcards send --dry-run`, the address book, the scheduler) works
  offline.

## Recommended: `pipx`

[`pipx`](https://pypa.github.io/pipx/) installs Python CLI tools into
isolated virtualenvs and exposes their entry points on your `PATH`
without polluting the system Python or your project venvs. It is the
recommended install path because the `postcards` runtime dependencies
(`typer`, `keyring`, `requests`, …) do not need to be installed
globally.

```sh
# 1. Install pipx itself (one-off, system-wide).
brew install pipx                    # macOS / Homebrew
sudo apt install pipx                # Debian / Ubuntu 23.04+
python3 -m pip install --user pipx   # anywhere else

# 2. Install postcards from a local checkout.
git clone https://github.com/gardenbaum/postcards.git
cd postcards
pipx install .

# 3. Verify.
postcards --version                  # → postcards 3.0.0
postcards --help
```

`pipx install .` from the repo root builds the wheel defined by
`pyproject.toml` and exposes the five console scripts
(`postcards`, `postcards-folder`, `postcards-yaml`,
`postcards-pexels`, `postcards-chuck-norris`) on your `PATH`.

To install the latest release from PyPI once the package is
published (see [`RELEASE.md`](RELEASE.md)):

```sh
pipx install postcards
pipx upgrade postcards
```

## Alternative: `pip install`

If you do not want `pipx`, a regular `pip install` works equally
well — either into a virtualenv you manage yourself, or into the
system Python with `--user` / `--break-system-packages` (PEP 668).

```sh
# Inside a project venv (recommended for development).
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"              # editable + dev tooling (ruff/mypy/pytest)

# From a local checkout (no extras).
pip install .

# From PyPI once published.
pip install postcards
```

`pip install -e ".[dev]"` is what
[`scripts/check.sh`](../scripts/check.sh) runs before the gate; use it
when you intend to run the test suite or the linter.

## Alternative: Docker

A multi-stage `Dockerfile` at the repo root builds a slim
`python:3.13-slim` image with the CLI pre-installed. This is the
recommended path on hosts where you cannot install Python 3.12+
system-wide (locked-down corporate laptops, Synology / QNAP NAS,
etc.) or where you want a sandboxed environment for batch jobs.

```sh
docker build -t postcards:dev .
docker run --rm -it postcards:dev --help
```

See [`docs/DOCKER.md`](DOCKER.md) for the full recipe (mounting a
config file, persisting the address book via a named volume, running
`postcards doctor` against a host config).

## Verifying the install

```sh
postcards --version          # → postcards 3.0.0
postcards --help             # full Typer-rendered command list
postcards doctor             # diagnose config / credentials / connectivity
```

`postcards doctor` is the best single check after a fresh install:
it verifies the config file loads, the SwissID credentials are
readable (from env, keyring, or a gitignored `config.json`), the
keyring backend is functional, the host can reach
`postcardcreator.post.ch`, and the in-process `MockBackend` (used by
the integration tests) round-trips a fake send. A passing
`postcards doctor` is the green light to send a real card with
`postcards send`.

## Troubleshooting

### `pipx install .` fails with `ERROR: File "..." was not found`

`pipx install .` builds the wheel from the current directory. Make
sure you are at the **repo root** (where `pyproject.toml` lives),
not inside `postcards/`.

### `postcards: command not found` after `pipx install .`

`pipx` installs entry points into `~/.local/bin` on Linux/macOS by
default. Add it to your `PATH`:

```sh
pipx ensurepath               # one-off; updates your shell rc
# …or manually: export PATH="$HOME/.local/bin:$PATH"
```

### `keyring` backend unavailable

The `keyring` subcommand and the credential-resolution layer both
require a working OS keyring. On Linux without a desktop session,
install `gnome-keyring` or `kwallet5`:

```sh
sudo apt install gnome-keyring
# log in to a graphical session once to initialise the keyring,
# then re-run `postcards doctor`.
```

`postcards doctor` reports the active keyring backend and flags the
common failure modes (no D-Bus session, headless server, etc.).

### Live sends fail with `AuthenticationError`

The SwissID backend has **anomaly detection + 2FA**. If you log in
from a new IP / device, SwissID may send a one-time code to your
phone or email. The CLI surfaces the message verbatim; complete the
2FA in your browser and retry. If the failure persists for >24h,
rotate your SwissID password — the upstream has been known to lock
accounts that send from a new IP without warming up.
