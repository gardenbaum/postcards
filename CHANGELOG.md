# Changelog

All notable changes to `postcards` are documented in this file. The format
is loosely based on [Keep a Changelog](https://keepachangelog.com/), and
the project adheres to [Semantic Versioning](https://semver.org/) as far
as is practical for a wrapper around an unofficial upstream API.

## [Unreleased]

### Added

- **M0 — toolchain + CI + constitution.** Replaces `setup.py` with a
  modern `pyproject.toml` (PEP 621, hatchling build backend) targeting
  Python 3.12 and 3.13. Adds and configures `ruff` (lint + format),
  `mypy`, and `pytest` + `pytest-cov`. Introduces `scripts/check.sh` as
  the single-command gate (ruff lint, ruff format check, mypy, pytest)
  and `.github/workflows/ci.yml` running it on a `py3.12/3.13` matrix
  for every push and pull request. Adds `docs/CONSTITUTION.md`
  codifying the project's invariants (unofficial Swiss Post integration,
  no live auth in CI, no committed secrets, the gate, code style). Adds
  a trivial smoke test in `tests/test_version.py` that proves the
  toolchain is wired up end to end. The next milestone, M1, removes
  the M0 lint/type exemptions on the legacy package and brings the
  runtime dependencies up to current releases.

- **M1 — dependency modernization + test suite.** Replaces the upstream
  `postcard-creator==2.2` PyPI package (which transitively depends on
  `Js2Py`/`pyjsparser`, `cookies`, `python-resize-image`, `pytz`,
  `tzlocal`, `six`, `pypexels`, `nltk`, and 2017-era pinned
  `certifi`/`urllib3`/`idna` — none of which install cleanly on Python
  3.12 / 3.13) with an in-tree vendored shim at
  `postcards._vendor.postcard_creator` that exposes the same public
  names (`Token`, `PostcardCreator`, `Postcard`, `Recipient`, `Sender`,
  `PostcardCreatorException`) and constructor signatures. Network
  methods on the shim (`Token.fetch_token`,
  `Token.has_valid_credentials`, `PostcardCreator.send_free_card`,
  `PostcardCreator.has_free_postcard`, `PostcardCreator.get_quota`)
  raise `NotImplementedError` so that any accidental live call is
  caught immediately. Drops `Js2Py`/`pyjsparser` by rewriting
  `plugin_random.random_search_term` in pure Python, drops `nltk` by
  replacing the POS-tagger noun extractor in
  `plugin_chuck_norris` with a regex tokenizer + stoplist, drops
  `pypexels` in favour of `picsum.photos` (no API key required) in
  `plugin_pexels.util.pexels`, and drops `pkg_resources` in favour of
  `importlib.resources`. Removes `requirements.txt` and
  `requirements-dev.txt`; `pyproject.toml` is now the single source of
  truth for both runtime and dev dependencies. Removes the M0
  per-file-ignores / mypy override / format-exclude on the legacy
  `postcards/` package; brings the package up to the same ruff + mypy
  + format baseline as the rest of the codebase. Adds a real test
  suite covering the shim, the CLI surface, the modernized helpers,
  and an end-to-end send-flow integration test against a MOCKED Swiss
  Post backend (`tests/test_send_integration.py`) — the mock is the
  single source of truth for the backend's contract; the live API is
  never exercised in CI.

- **M1 — backend interface + SwissID consumer backend.** Introduces
  the typed `PostcardBackend` Protocol every Swiss Post network call
  must go through (`docs/CONSTITUTION.md` §1.1). The new
  `postcards.backend` package ships:
  - `base.PostcardBackend` — runtime-checkable Protocol with the four
    operations the consumer flow exposes (login, quota, preview,
    send), plus typed payloads (`AddressSpec`, `PostcardSpec`,
    `QuotaInfo`, `PreviewInfo`, `SendResult`) as frozen dataclasses.
  - `mock.MockBackend` — in-memory implementation that records every
    login / preview / send. It is the single source of truth for the
    backend's contract in tests and the `POSTCARDS_BACKEND=mock`
    fallback for developers exercising the CLI surface.
  - `swissid.SwissIdConsumerBackend` — production wrapper around the
    vendored `postcard_creator` shim; translates between the
    protocol's dataclasses and the shim's `Sender` / `Recipient` /
    `Postcard` types.
  - `registry.select_backend` — selection driven by the
    `POSTCARDS_BACKEND` env var or the `backend` field of the config
    file; raises `BackendNotAvailableError` on typos and lists the
    valid names in the message.
  - `postcards.config.ConfigLayer` — typed loader that resolves
    credentials via the constitution's precedence order (CLI >
    `POSTCARDS_USERNAME` + `POSTCARDS_PASSWORD` > OS keyring under
    service `postcards` > gitignored config file). Every
    `AccountConfig` carries a `source` field that records which path
    resolved it for diagnostics.

  No CLI behaviour change yet — the legacy send flow still uses the
  shim directly via `_create_pcc_wrappers`. Routing
  `do_command_send` through `select_backend()` lands in a later
  milestone so the new abstraction becomes the only network path.

  Test count: 73 → 127 (+54). Total coverage: 43% → 73%.
  `postcards/backend/*` is at 94–100% coverage; `postcards/config/*`
  is at 98%. New tests live in `tests/test_backend_selection.py`,
  `tests/test_config_layer.py`, and `tests/test_backend_integration.py`.

- **M1 — A6 image pipeline + postcard model.** Adds the typed
  user-facing domain models the CLI builds before handing a card
  to a backend:

  * `postcards.models` — `Recipient`, `Sender` (aliases over
    `AddressSpec` so call sites read like the upstream Swiss Post
    API), `Message` (frozen dataclass, ≤ 500 chars, with a
    `from_text` builder), and `Postcard` (the high-level model with
    a `from_image` classmethod that runs the pipeline).
  * `postcards.image` — the A6 image pipeline:
    - `dimensions.py` — `Orientation` (StrEnum), `A6_LANDSCAPE_*` /
      `A6_PORTRAIT_*` pixel sizes (1500×1062 / 1062×1500, the
      exact dimensions the Swiss Postcard Creator accepts),
      `A6_ASPECT_RATIO` (148/105 ≈ √2), `SUPPORTED_FORMATS`
      (`{"JPEG", "PNG"}`).
    - `pipeline.py` — `load_image` (path / bytes / `BinaryIO`),
      `normalize_orientation` (EXIF transpose, preserves format),
      `validate_format`, `detect_orientation`,
      `center_crop_to_aspect`, `resize_to_a6` (LANCZOS,
      RGBA → white flatten, grayscale → RGB), `encode_jpeg`,
      and the convenience wrapper `prepare_postcard_image`. All
      public entry points raise `ImageError` on failure so callers
      catch a single exception type.

  The protocol's `PostcardBackend.send` / `.preview` now accept the
  user-facing `Postcard` (carrying processed JPEG **bytes**) instead
  of the protocol-level `PostcardSpec` (carrying a file-like
  `BinaryIO`). `SwissIdConsumerBackend.send` translates the
  `Postcard` → shim types internally and wraps the picture bytes
  in `io.BytesIO` for the shim. `PostcardSpec` remains as the
  internal transport payload (frozen dataclass with
  `BinaryIO | None`) for callers that want to short-circuit the
  user-facing layer.

  Test count: 127 → 205 (+78). Total coverage: 73% → 77%.
  `postcards/image/*` and `postcards/models/*` are at 100% coverage.
  New tests live in `tests/test_image_pipeline.py` (46 tests),
  `tests/test_postcard_model.py` (32 tests), and an additional
  end-to-end pipeline → Postcard → MockBackend integration test in
  `tests/test_backend_integration.py`.

- **M2 — Typer-based CLI.** Migrates the user-facing
  ``postcards`` console script from the legacy ``argparse`` parser
  in :mod:`postcards.postcards` to a Typer-based command tree
  under the new :mod:`postcards.cli` package. The new commands are:

  * ``postcards send`` — send a card. Honours ``--dry-run`` and
    ``--all-accounts``; falls back to the active account from the
    config file when ``--username`` / ``--password`` are not
    passed.
  * ``postcards preview`` — show what ``send`` would do, without
    actually sending. Same arguments as ``send``. Accepts
    ``--output PATH`` (a ``.png`` / ``.jpg`` / ``.jpeg`` / ``.pdf``
    path) to render the would-be card to a local file via the new
    :mod:`postcards.render` module — no network call, no SwissID
    login, no quota consumption. URL pictures are rejected so the
    preview stays a strict offline dry-run.
  * ``postcards generate`` — write the bundled starter config to
    a given path. Refuses to clobber an existing file unless
    ``--force`` is passed.
  * ``postcards config {init,show,set}`` — manage the config
    file. ``init`` is an alias for ``generate``; ``show`` prints
    the resolved config (passwords masked by default; opt in via
    ``--no-redact``); ``set`` mutates a dotted key path
    (``recipient.city``, ``accounts.0.username``, ...).
  * ``postcards accounts {add,list,use}`` — manage the
    multi-account list. ``add`` appends a username / password
    pair (and refuses to create a duplicate); ``list`` shows
    the accounts with passwords masked; ``use`` sets the
    ``active_account`` field.
  * ``postcards quota`` — print the free-card quota for an
    account. Honours ``--backend mock`` for offline exercises
    of the code path.
  * ``postcards status`` — print the resolved CLI configuration
    (config path, backend, account, version).
  * ``postcards encrypt`` / ``postcards decrypt`` — the
    credential crypto commands, migrated from the legacy
    ``argparse`` form.
  * ``postcards legacy run`` — escape hatch that delegates to
    the pre-M2 ``argparse`` parser. The dedicated plugin entry
    points (``postcards-folder``, ``postcards-yaml``,
    ``postcards-pexels``, ``postcards-random``,
    ``postcards-chuck-norris``) keep their argparse-based
    implementation and are unaffected by the M2 migration.

  All subcommands render rich ``--help`` with grouped options
  and use the constitution's ``raise_cli_error`` helper for
  user-facing errors (typed ``NoReturn`` so ``str | None``
  arguments are narrowed to ``str`` by mypy after a guard).
  The production entry point :func:`postcards.cli.main.main`
  calls :data:`postcards.cli.app.app` directly (not the
  ``typer.testing.CliRunner``) so help text and error messages
  reach the real stdout / stderr. The test entry point
  :func:`postcards.cli.runner.run` continues to wrap
  ``typer.testing.CliRunner`` for hermetic test assertions.

  Test count: 205 → 239 (+34). The new tests live in
  ``tests/test_typer_cli.py`` and cover: every subcommand's
  ``--help`` (10 subcommands), top-level help listing all
  subcommands, ``postcards`` with no args, ``--version``,
  ``send`` validation (requires ``--picture`` or ``--message``,
  ``--dry-run`` flow against a mocked Swiss Post shim),
  ``preview`` end-to-end (mocked shim), ``generate`` clobber
  protection, ``config init`` / ``show`` / ``set`` (including
  password redaction defaults and list-index paths),
  ``accounts add`` / ``list`` / ``use`` (including duplicate
  detection and active-account marking), ``status`` output,
  ``quota --backend mock`` plus the missing-username error
  path, ``encrypt`` / ``decrypt`` round-trip and wrong-key
  behavior, the ``legacy`` subcommand, the ``CLIError`` class
  contract, ``-vv`` logging configuration, and the
  :func:`main` entry point.

- **M2 — offline preview rendering.** Adds the
  :mod:`postcards.render` package and a ``--output`` option on
  ``postcards preview`` so the user can render the would-be
  card (front image + back with message and addresses) to a
  local PNG / JPEG / PDF without contacting Swiss Post. The
  renderer runs the A6 image pipeline on the supplied picture
  so the rendered front is the exact JPEG bytes the backend
  would transmit. The back panel paints the recipient block
  at the top of the right half and the sender block at the
  bottom (with a ``From:`` label) using a word-wrapped message
  on the left half; HTML in the message is normalised to
  plain text with ``<br>`` becoming a newline. URL pictures
  are rejected so the preview stays a strict offline
  dry-run; unsupported output extensions raise
  :class:`postcards.render.RenderError`. The renderer is
  dependency-light (Pillow only — no network, no SwissID
  code, no quota consumption).

  Test count: 239 → 269 (+30). The new tests live in
  ``tests/test_postcard_render.py`` (25 unit tests on the
  renderer: front / back dimensions, placeholder,
  picture-byte decoding, garbage-bytes error, file
  extension dispatch, PNG / JPEG / PDF output sanity,
  parent-directory creation, address formatting, HTML
  normalisation, word-wrap edge cases) and
  ``tests/test_typer_cli.py`` (5 CLI integration tests
  on the ``--output`` flow: PNG / PDF writes, text-only
  cards, URL rejection, unsupported-extension rejection).

- **M3 — modern plugin architecture.** Replaces the
  inheritance-based plugin system (each plugin subclassed
  ``postcards.postcards.Postcards`` and overrode
  ``get_img_and_text`` / ``build_plugin_subparser`` /
  ``can_handle_command``) with a small typed, registry-based
  API. The new public surface lives in ``postcards.plugins``:

  - ``Plugin`` (``Protocol``) — three methods (`configure`,
    `render`, `cli_help`) and two `ClassVar`s (`name`,
    `description`); `@runtime_checkable` so tests can use
    `isinstance`.
  - ``PluginResult`` — frozen dataclass carrying `image`
    (`BinaryIO`) + optional `message` (`str`) + optional
    `metadata`.
  - ``PluginContext`` — frozen dataclass carrying per-render
    `options` (`Mapping[str, Any]`) + scoped `logger`.
  - ``Registry`` — `name → plugin class` lookup with
    `importlib.metadata` entry-point discovery under the
    ``postcards.plugins`` group. The in-tree plugins
    (``folder``, ``folder_yaml``, ``pexels``,
    ``chuck_norris``) register themselves at import time
    and are also advertised via the entry-point group so
    third-party tools can enumerate them.
  - ``load_plugin(name, payload, registry=None)`` — build,
    configure, and return a ready-to-render plugin instance.
    Wraps plugin `configure` exceptions in `PluginConfigError`.
  - Typed exception hierarchy: ``PluginError`` and
    subclasses (``PluginNotFoundError``,
    ``PluginConfigError``, ``PluginRenderError``).

  The four in-tree plugins are ported to the new API:

  - ``folder`` — pick a random picture from a local
    directory. Supports a ``.priority/`` subdirectory that
    is sampled exclusively when populated, and an optional
    ``move=True`` that relocates the chosen picture into
    ``sent/``. Reads the file into memory and returns an
    in-memory `BytesIO` so callers do not manage a file
    handle.
  - ``folder_yaml`` — pick the first `(text, image)` pair
    from a YAML playlist; optionally truncate the document
    after picking and optionally move the picture.
  - ``pexels`` — fetch a random photo from
    `picsum.photos` via the legacy
    `postcards.plugin_pexels.util.pexels` helper. The
    `keyword` payload field becomes the picsum seed so
    different keywords land on different photos.
  - ``chuck_norris`` — pick a random joke from a bundled
    JSON dataset and fetch a matching picture. Supports
    `category` filtering and a `duplicate_file` exclusion
    list. The keyword extractor is a regex-based stopword
    filter (no `nltk`).

  - ``url`` — fetch a picture from a user-supplied
    `http://` or `https://` URL. Accepts optional
    custom `headers` (for `Authorization` etc.) and a
    per-request `timeout`. Network errors and HTTP
    4xx/5xx responses are surfaced as
    `PluginRenderError` so the CLI prints a clean
    message instead of a `requests` traceback.

  - ``local`` — deterministic round-robin picker over a
    local folder. The first render returns the
    alphabetically-first matching picture; subsequent
    renders advance through the sorted list and wrap to
    0. Supports an optional `pattern` glob (e.g.
    `landscape/*.jpg`) and an optional `cursor_file`
    that persists the round-robin position across
    processes (for cron-driven sends). Complements the
    `folder` plugin, which picks uniformly at random.

  - ``unsplash`` — fetch a random photo from the
    Unsplash API. Reads the access token from the
    environment variable
    `POSTCARDS_UNSPLASH_ACCESS_KEY` (no config-file
    fallback — secrets never live in the repo, see
    `docs/CONSTITUTION.md` §2). Supports an optional
    `query` (search term), `orientation`
    (`landscape`/`portrait`/`squarish`, default
    landscape), and `count` (1-30, picks one at
    random from the returned list). Issues two HTTP
    calls per render: the `/photos/random` lookup and
    the JPEG download — both with the default 30 s
    timeout.

  The new plugins are documented in
  `docs/WRITING_PLUGINS.md`, which walks through the
  protocol, the `PluginResult` return type, the
  configuration payload, the typed exception
  hierarchy, and the entry-point publishing protocol
  for third-party plugins. The document also lists
  every in-tree plugin as a real-world example.

  The ``send`` CLI subcommand and ``postcards.postcards.Postcards.do_command_send``
  pick up the new plugin path automatically: when
  ``config.json`` carries a ``payload.plugin`` field, the
  modern registry path runs; otherwise the legacy
  ``_is_plugin()`` branch (used by the `postcards-folder`,
  `postcards-yaml`, ... console scripts) is preserved for
  backward compatibility. CLI ``-m`` / ``-p`` options win
  over the plugin's `message` / `image`.

  A new ``postcards plugins list`` Typer subcommand
  enumerates the registered plugins (in-tree +
  entry-point).

  The legacy ``postcards.plugin_random`` Bing-image-scraper
  plugin is **removed**. Bing's image-search HTML format
  dropped the `murl` JSON attribute on `<a class="iusc">`
  elements in 2023, so the plugin's scraper returns zero
  results on every request. The `pexels` plugin covers the
  "I just want a random picture" use case. The
  ``postcards-random`` console script, the
  ``postcards.plugin_random.*`` packages, the bundled
  ``random.html``/``random.js``/``random_search_term``
  assets, and the ``tests/test_random_search_term.py``
  tests are all deleted.

  ``Postcards._read_picture`` now reads the picture into
  memory and returns a `BytesIO` instead of an open file
  handle. The legacy behavior leaked file descriptors in
  callers that forgot to close the handle; the M2
  integration tests started triggering `ResourceWarning`
  errors under ``filterwarnings = "error"`` after the
  legacy `_is_plugin()` path was retired.

  Test count: 269 → 454 (+185). The new tests live in
  ``tests/test_plugin_base.py`` (10 unit tests on the
  `Plugin` Protocol / `PluginResult` dataclass /
  `PluginBase` helper), ``tests/test_plugin_errors.py`` (5
  exception tests), ``tests/test_plugin_registry.py`` (16
  registry tests including entry-point discovery),
  ``tests/test_plugin_loader.py`` (10 loader tests),
  ``tests/test_plugin_folder.py`` (15 tests),
  ``tests/test_plugin_folder_yaml.py`` (12 tests),
  ``tests/test_plugin_pexels.py`` (9 tests, network
  mocked via `urllib.request.urlopen`),
  ``tests/test_plugin_chuck_norris.py`` (19 tests, network
  mocked, keyword extractor unit tests),
  ``tests/test_plugin_url.py`` (21 tests, network
  mocked via `requests.get`),
  ``tests/test_plugin_local.py`` (24 tests, round-robin
  cursor + glob pattern + cursor_file persistence),
  ``tests/test_plugin_unsplash.py`` (37 tests, network
  mocked — covers the two-step API + download flow,
  env-var config, error envelopes), and
  ``tests/test_plugin_send_integration.py`` (7
  end-to-end tests that drive ``do_command_send`` through
  the mocked upstream `Token` / `PostcardCreator` with a
  config-driven plugin producing the picture).

### Notes

- The gate installs the package with `pip install -e ".[dev]"` (M1
  modernized the runtime dep set, so `--no-deps` is no longer
  required) and falls back to `uv pip install --python X` when pip is
  missing from the venv (the case for `uv venv`-managed venvs in CI).