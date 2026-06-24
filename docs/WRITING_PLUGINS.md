# Writing postcards plugins

> Audience: anyone who wants to add a new image source (or
> image+message source) for `postcards send`. This document is
> normative for in-tree plugins; external packages follow the
> same contract with one extra step (declaring an entry point).

## What a plugin does

A plugin is a small Python class that, given a `payload` dict
from the user's `config.json`, returns an in-memory picture
stream (and optionally a message string) for one postcard.

The plugin does **not** talk to the Swiss Post backend. That is
the job of `postcards.backend.PostcardBackend`. The plugin only
produces the picture (and optionally the message); the rest of
the pipeline (image validation, sender/recipient encoding, send)
runs elsewhere.

## The protocol

The protocol is `postcards.plugins.Plugin`. It has three
methods and two class variables:

```python
from postcards.plugins import Plugin, PluginResult
from postcards.plugins.base_impl import PluginBase

class MyPlugin(PluginBase):
    name: ClassVar[str] = "my_plugin"
    description: ClassVar[str] = "short one-liner for 'postcards plugins list'"

    def configure(self, payload: Mapping[str, Any]) -> None:
        # Validate and store the payload. Raise PluginConfigError
        # on malformed input. Call super().configure(payload) LAST
        # so a failed validation does not leave the plugin
        # half-configured.
        super().configure(payload)

    def render(self) -> PluginResult:
        # Produce the picture (and optional message). Raise
        # PluginRenderError on failure (network error, empty
        # folder, ...). The caller reads ``result.image`` and
        # hands it to the image pipeline.
        return PluginResult(image=BytesIO(b"..."), message="optional text")
```

### When to inherit from `PluginBase`

`PluginBase` is a small helper that:

* stores the validated payload in `self._payload`
* exposes `self.logger` (scoped to `postcards.plugins.<name>`)
* validates that subclasses set a non-empty `name`

Most plugins should inherit from `PluginBase`. You can also
implement the `Plugin` protocol directly if you need a custom
`__init__` or want a dataclass-style plugin; the loader's
`isinstance(plugin, Plugin)` check works for both shapes thanks
to `@runtime_checkable`.

### When to call `super().configure()`

Always last, after all your validation. `PluginBase.configure`
does `self._payload = dict(payload)` (a shallow copy so the
caller cannot accidentally mutate the user's config). If your
validation raises, the payload is never stored, and the next
`render()` call would crash on a missing field — which is
correct behaviour.

## PluginResult

`postcards.plugins.PluginResult` is a frozen dataclass:

```python
@dataclass(frozen=True)
class PluginResult:
    image: BinaryIO
    message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

* `image` is required. It must be a `BinaryIO` (typically
  `io.BytesIO`) because the postcard backend hands it to PIL
  and discards it. Returning an open file handle leaks file
  descriptors in callers that forget to close.
* `message` is optional. When `None`, the CLI's `-m`/`--message`
  option wins (see `docs/CONSTITUTION.md` §5 — "the CLI stays
  usable"). Plugin authors who always supply a message should
  still leave the door open for CLI overrides.
* `metadata` is an open-ended bag for plugin-specific
  debugging data (request URLs, response codes, picked-file
  paths). The postcard backend ignores it; the CLI may surface
  it in `--verbose` logs.

## Configuration

`payload` is whatever the user puts under the `payload` key in
their `config.json`:

```json
{
  "send": {
    "sender": { ... },
    "recipient": { ... },
    "payload": {
      "plugin": "url",
      "url": "https://example.com/picture.jpg",
      "message": "hi from zurich"
    }
  }
}
```

`payload.plugin` selects the plugin. The remaining keys are the
plugin's configuration. There is no central schema; each plugin
owns its payload shape. Validate aggressively in `configure()`:
if the user typo'd a key, raise `PluginConfigError` so the CLI
prints a clean error message instead of an obscure
`KeyError`.

## Errors

Use the typed exception hierarchy in
`postcards.plugins.errors`:

* `PluginConfigError(self.name, "<message>")` — payload
  validation failed.
* `PluginRenderError(self.name, "<message>")` — `render()`
  failed (network error, empty folder, missing key in API
  response, ...).
* `PluginNotFoundError("<name>")` — only the loader raises
  this; plugin authors do not need to.

The `self.name` is prepended to the message in the rendered
error so the user sees which plugin failed without reading a
traceback.

## Configuration of secrets

If your plugin needs an API key or other secret, read it from
the environment. **Never** read it from a tracked config file
— `docs/CONSTITUTION.md` §2 forbids secrets in the repo.

Pattern:

```python
import os

def render(self) -> PluginResult:
    api_key = os.environ.get("POSTCARDS_MY_PLUGIN_KEY", "").strip()
    if not api_key:
        raise PluginRenderError(
            self.name,
            "POSTCARDS_MY_PLUGIN_KEY is not set; "
            "sign up at https://example.com and export the key",
        )
    ...
```

`POSTCARDS_<NAME>_KEY` is the project's convention; the
`unsplash` plugin's `POSTCARDS_UNSPLASH_ACCESS_KEY` is the
canonical example.

## Testing

Tests for plugins follow the project's existing pattern:

* **Network plugins** (`pexels`, `unsplash`, `url`): patch
  `urllib.request.urlopen` or `requests.get` *where the plugin
  imports it* (use `monkeypatch.setattr` against
  `postcards.plugins.builtin.<name>.requests.get`,
  not the bare `requests.get`).
* **Filesystem plugins** (`folder`, `folder_yaml`, `local`):
  write synthetic PNG/JPEG files into a `tmp_path` fixture
  using PIL, then assert on the picked file's contents.

There are no shared fixtures; copy the `_FakeResponse` /
`_FakeGet` helpers from `tests/test_plugin_unsplash.py` if you
need a `requests` stand-in. Tests must run in CI without
network access — see `docs/CONSTITUTION.md` §4.

A plugin's tests should cover:

1. **Configuration validation** — every required field is
   required, every optional field's type is checked.
2. **Happy path** — at least one render that produces the
   expected `PluginResult`.
3. **Failure modes** — network error, HTTP error, empty
   payload (where applicable), missing required config.
4. **Plugin metadata** — `name` and `description` class
   variables are set; the plugin is in
   `Registry.default`.

## Publishing as a third-party package

A third-party plugin is just a class that follows the protocol.
To make `postcards` discover it, declare an entry point in the
package's `pyproject.toml`:

```toml
[project.entry-points."postcards.plugins"]
my_plugin = "my_package.my_plugin:MyPlugin"
```

The entry-point value is the dotted path to the plugin *class*
(not an instance). `Registry.discover()` iterates the
`postcards.plugins` entry-point group and registers every class
it finds.

Naming: lower-snake-case, no leading digit, no `postcards-`
prefix (the prefix is reserved for the legacy console-script
entry points). `name` collisions are resolved by letting
programmatic registration win — see
`postcards.plugins.registry.Registry.discover`.

## Reference: in-tree plugins

For real-world examples, read the in-tree plugins:

* `postcards/plugins/builtin/folder.py` — random pick from a
  local folder; uses `os.listdir` + `random.choice`.
* `postcards/plugins/builtin/local.py` — round-robin pick from
  a local folder; uses `os.listdir` + sorted order + persistent
  cursor.
* `postcards/plugins/builtin/folder_yaml.py` — text + image
  pair from a YAML document; demonstrates the `message` field
  on `PluginResult`.
* `postcards/plugins/builtin/pexels.py` — random image from
  picsum.photos via `urllib`.
* `postcards/plugins/builtin/unsplash.py` — random photo from
  the Unsplash API via `requests`; demonstrates env-var config
  and a two-step HTTP call (API + download).
* `postcards/plugins/builtin/url.py` — fetch a user-supplied
  URL via `requests`; demonstrates custom headers and timeout.
* `postcards/plugins/builtin/chuck_norris.py` — pick a joke
  from a bundled JSON file; demonstrates bundled data files
  + a side-effecting exclusion list.
