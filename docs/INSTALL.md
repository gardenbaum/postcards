# Installing `postcards`

`postcards` ships an interactive web app (`postcards app`) and a
scriptable CLI. The project standardises on
[`uv`](https://docs.astral.sh/uv/) for environments, dependencies and
execution — `pyproject.toml` is the single source of truth. **Do not use
`pipx`.**

## Requirements

- Python **3.12** or **3.13** (uv can fetch one for you).
- `uv` ([install](https://docs.astral.sh/uv/getting-started/installation/)):

  ```sh
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

## Extras

| Extra | Pulls in | For |
| ----- | -------- | --- |
| _(none)_ | `typer`, `Pillow`, `requests`, `keyring`, … | the CLI |
| `app` | `nicegui` | the interactive web app (`postcards app`) |
| `dev` | `pytest`, `pytest-asyncio`, `ruff`, `mypy` | contributing |

## Run the app without installing

```sh
uvx --from '.[app]' postcards app        # ephemeral env, opens the browser
```

## Project environment (recommended)

```sh
uv venv                                   # .venv with a compatible Python
uv pip install -e '.[app]'                # CLI + web app
uv run postcards --version
uv run postcards app                      # http://127.0.0.1:8080
```

Drop `[app]` if you only want the CLI:

```sh
uv pip install -e .
```

## Install the CLI as a global tool

```sh
uv tool install .                         # exposes `postcards` on PATH
postcards doctor
```

`uv tool install '.[app]'` includes the web app in the tool env.

## From PyPI (once published)

```sh
uv tool install postcards                 # CLI
uvx --from 'postcards[app]' postcards app # app
```

## Docker

A `python:3.13-slim` image builds the CLI (no web app). See
[`DOCKER.md`](DOCKER.md):

```sh
docker build -t postcards:dev .
docker run --rm -it postcards:dev --help
```

## Verifying the install

```sh
postcards --version       # → postcards 4.0.0
postcards doctor          # environment + config smoke test
postcards app             # launches the web app
```

## Troubleshooting

### `postcards: command not found` after `uv tool install`

Ensure uv's tool-bin directory is on your `PATH`:

```sh
uv tool update-shell      # adds it to your shell profile
# …or manually: export PATH="$HOME/.local/bin:$PATH"
```

### The app won't start / `ModuleNotFoundError: nicegui`

The web app needs the `app` extra. Reinstall with it:

```sh
uv pip install -e '.[app]'      # or: uvx --from '.[app]' postcards app
```

### `keyring` backend unavailable

On headless Linux there may be no D-Bus secret service. Either use
environment variables (`POSTCARDS_USERNAME` / `POSTCARDS_PASSWORD`) or
log in to a graphical session once to initialise the keyring, then
re-run `postcards doctor`.

### Live sends fail with `AuthenticationError`

SwissID uses anomaly detection and may require 2FA, so a live login is a
manual, interactive step — it cannot run unattended. See the README
Troubleshooting section and [`APP.md`](APP.md).
