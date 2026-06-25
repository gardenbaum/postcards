# Dockerfile — ship the `postcards` CLI in a slim container.
#
# Build:  docker build -t postcards:dev .
# Run:    docker run --rm -it postcards:dev --help
#         docker run --rm -it postcards:dev doctor
#         docker run --rm -it \
#             -v $PWD/config.json:/home/postcards/config.json:ro \
#             -v postcards-data:/home/postcards/.local/share/postcards \
#             postcards:dev send --config /home/postcards/config.json \
#                 --picture https://picsum.photos/600 --message "Hi"
#
# Image layout:
#   * Base: python:3.13-slim. The constitution (§3) mandates 3.12 / 3.13
#     in CI; 3.13-slim is the smallest image that satisfies that.
#   * User: a non-root `postcards` account. The CLI does not need root,
#     and the address book / schedule data live under
#     /home/postcards/.local/share/postcards (XDG defaults).
#   * Install: `pip install --no-cache-dir /src` from a build context
#     that copies only pyproject.toml + the postcards/ source tree
#     (no .git, no .venv, no .pytest_cache).
#
# See docs/DOCKER.md for the full recipe, including running
# `postcards doctor` in a container to diagnose a host config
# without installing the package locally.

# ---------------------------------------------------------------------------
# Stage 1: build the wheel in an isolated venv so the runtime image does
# not need setuptools / hatchling / build.
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS build

WORKDIR /src

# Copy only what `pip install .` needs to resolve metadata and build the
# wheel. hatchling reads pyproject.toml; the postcards/ source tree
# contains the package. .gitignore is not required at build time.
COPY pyproject.toml README.md LICENSE CHANGELOG.md ./
COPY postcards ./postcards

# Build an isolated, byte-compiled wheel. `pip wheel` writes
# /src/dist/*.whl which the runtime stage installs.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

# ---------------------------------------------------------------------------
# Stage 2: runtime image. Only the wheel + the runtime deps get installed.
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

# Create a non-root user. UID/GID 1000 matches the default `ubuntu` /
# `debian` user on most hosts, so bind-mounts from the host work without
# a permission dance.
RUN groupadd --system --gid 1000 postcards \
    && useradd  --system --uid 1000 --gid postcards \
                --home-dir /home/postcards \
                --shell /bin/bash \
                --create-home \
                postcards

# Install the wheel and its runtime dependencies. --no-cache-dir keeps
# the image small; --no-index ensures we use the bundled wheel and not
# a re-resolved PyPI download (which would defeat the multi-stage
# purpose).
COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links /wheels postcards \
    && rm -rf /wheels

USER postcards
WORKDIR /home/postcards

# Persistent address book + schedule data. Mount a named volume here
# (or a host bind mount) to survive `docker run --rm`.
ENV XDG_DATA_HOME=/home/postcards/.local/share \
    XDG_CONFIG_HOME=/home/postcards/.config \
    XDG_CACHE_HOME=/home/postcards/.cache

# ENTRYPOINT turns the container into a single-purpose CLI:
#   docker run postcards:dev send ...
# CMD supplies the default arguments for `docker run postcards:dev`
# (i.e. show help, so a careless `docker run` does not silently no-op).
ENTRYPOINT ["postcards"]
CMD ["--help"]
