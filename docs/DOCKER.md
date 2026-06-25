# Running `postcards` in Docker

The repo ships a multi-stage `Dockerfile` at the repo root that builds
a slim `python:3.13-slim` image with the CLI pre-installed. This
document covers the supported build, run, and mount patterns.

## Why a Docker image?

- **Portable runtime.** No host Python 3.12 / 3.13 install required;
  useful on locked-down corporate laptops, NAS devices, and CI
  runners where installing Python system-wide is not an option.
- **Reproducible.** The image pins `python:3.13-slim` (matching the
  CI matrix in `docs/CONSTITUTION.md` §3) and installs a wheel built
  from the repo's `pyproject.toml`. A rebuild from the same commit
  produces a byte-identical image.
- **Sandboxed.** Live SwissID credentials live in a bind-mounted file
  or an env var, never baked into a layer. The image runs as the
  unprivileged `postcards` user (UID 1000), not root.

The image is **not** a long-running daemon. It is a single-purpose
CLI runner: `docker run` invokes `postcards` exactly once and exits.
The CLI does not need to stay running between sends.

## Build

```sh
# From the repo root.
docker build -t postcards:dev .

# Or with a specific version label.
docker build -t postcards:3.0.0 .
```

The build is multi-stage:

1. **Build stage** (`python:3.13-slim`, label `build`): installs
   `hatchling` via `pip wheel`, builds the wheel into `/wheels`.
2. **Runtime stage** (`python:3.13-slim`, label `runtime`): copies
   `/wheels` from the build stage, installs the wheel with
   `pip install --no-index`, creates the unprivileged `postcards`
   user, sets the default entry point.

Total image size: ~150 MB (matches `python:3.13-slim` + 7 runtime
deps from `pyproject.toml`).

## Run

The `ENTRYPOINT` is `["postcards"]` and `CMD` is `["--help"]`, so
bare `docker run` shows the help text:

```sh
docker run --rm -it postcards:dev
# → Usage: postcards [OPTIONS] COMMAND [ARGS]...
# → ...
```

Append a subcommand to invoke it:

```sh
docker run --rm -it postcards:dev doctor
docker run --rm -it postcards:dev status
docker run --rm -it postcards:dev quota
```

## Mounting a config file

The CLI reads the config from `--config <path>` (or
`$POSTCARDS_CONFIG`, defaulting to `./config.json`). Mount a host
config into the container read-only:

```sh
docker run --rm -it \
    -v "$PWD/config.json":/home/postcards/config.json:ro \
    postcards:dev send --config /home/postcards/config.json \
        --picture https://picsum.photos/600 \
        --message "Hi from Docker!"
```

For the address book + scheduler, mount a named volume so the data
survives `docker run --rm`:

```sh
docker volume create postcards-data
docker run --rm -it \
    -v "$PWD/config.json":/home/postcards/config.json:ro \
    -v postcards-data:/home/postcards/.local/share/postcards \
    postcards:dev addresses add alice \
        --prename Alice --lastname Zuercher \
        --street "Bahnhofstrasse 1" --zip-code 8000 --place Zurich

docker run --rm -it \
    -v "$PWD/config.json":/home/postcards/config.json:ro \
    -v postcards-data:/home/postcards/.local/share/postcards \
    postcards:dev batch --to alice \
        --picture https://picsum.photos/600 \
        --message "Hi Alice"
```

The `XDG_*` env vars in the `Dockerfile` pin the data directory to
`/home/postcards/.local/share/postcards` inside the container; that
path matches `docker volume create postcards-data` above.

## Credentials in Docker

There are three credential-resolution paths
(see [`CONSTITUTION.md` §2](CONSTITUTION.md#2-secrets-and-credentials)):

1. **Env vars.** Pass `POSTCARDS_USERNAME` and `POSTCARDS_PASSWORD`
   at `docker run` time:

   ```sh
   docker run --rm -it \
       -e POSTCARDS_USERNAME -e POSTCARDS_PASSWORD \
       postcards:dev send --config /home/postcards/config.json ...
   ```

   Pull the values from a secrets manager (1Password CLI, `pass`,
   `doppler`, AWS SSM, etc.) — **do not** put them in a Dockerfile
   `ARG` or `ENV` layer; the SwissID password is a real secret.

2. **OS keyring.** The image has the `keyring` package and the
   `postcards keyring` subcommand works inside the container, but
   the **OS keyring is not portable across containers**. Bind-mount
   a keyring database or use env vars for container runs.

3. **Config file.** A `config.json` whose `accounts[*].password` is
   encrypted under the user's `POSTCARDS_KEY` (see `postcards
   encrypt <plaintext>`) is safe to bind-mount read-only. The
   password is never written to disk in plaintext.

## `postcards doctor` inside a container

`postcards doctor` is the best way to validate a config without
sending a real card:

```sh
docker run --rm -it \
    -v "$PWD/config.json":/home/postcards/config.json:ro \
    -e POSTCARDS_USERNAME -e POSTCARDS_PASSWORD \
    postcards:dev doctor --config /home/postcards/config.json
```

The doctor runs five checks (config load, credentials resolve,
keyring functional, network reaches `postcardcreator.post.ch`,
in-process mock backend round-trips) and exits non-zero on the
first failure. Useful as a smoke test in CI / GitHub Actions.

## Building a one-off image with a custom config

For batch / scheduled sends where you want the config baked into
the image (only safe with **encrypted** credentials):

```dockerfile
FROM postcards:3.0.0
COPY --chown=postcards:postcards config.json /home/postcards/config.json
USER postcards
ENTRYPOINT ["postcards"]
CMD ["batch", "--manifest", "/home/postcards/manifest.yaml"]
```

The constitution explicitly forbids checking the resulting image
into a registry with **plaintext** credentials — the image is safe
to publish only if `config.json` is the encrypted-at-rest form that
`postcards encrypt` produces.

## Limitations

- The image is **linux/amd64 + linux/arm64** only (matches the
  `python:3.13-slim` manifest). Building on macOS or Windows hosts
  uses Docker's buildx QEMU emulation by default, which is fine
  for the small build but slow.
- The image runs the CLI as `postcards` (UID 1000). On hosts where
  the only available user is `root` (some NAS firmwares), bind
  mounts may need `--user 0:0` or a custom UID. The image does not
  attempt to auto-detect this — pass `--user` explicitly.
- No `HEALTHCHECK`. The CLI is short-lived; the `CMD` is a single
  `postcards --help` invocation, which exits 0 if the package is
  importable. For liveness checks, run `docker run --rm postcards:dev
  doctor --skip-mock` from your orchestrator.
