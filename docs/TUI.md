# `postcards tui` — the local TUI

The `postcards tui` command launches a small [Textual]-based
terminal UI for composing, previewing, and (with an explicit
confirmation step) sending a postcard. It is the friendly
front-end on top of the same CLI pipeline `postcards send`
already uses.

[Textual]: https://textual.textualize.io/

## Why a TUI and not a web UI

The M6 card offered a choice between a Textual TUI and a
local FastAPI + htmx web UI. The TUI was chosen because:

* **No browser, no port, no second process.** The TUI runs in
  the same terminal the rest of the tool runs in. No copying
  paths into a separate window.
* **SSH / Docker / headless-friendly.** The TUI works over
  SSH and inside the Docker image (the package already
  advertises `Environment :: Console`). A web UI would need
  port-forwarding, a browser, and a working `xdg-open` on the
  client side.
* **Single dep.** `textual` pulls in `rich` and a small set
  of well-behaved transitive deps. The web-UI alternative
  would have added `fastapi`, `uvicorn`, `jinja2`, and the
  HTML/CSS/JS weight.
* **Deterministic tests.** `textual.pilot.Pilot` drives the
  TUI through an in-memory harness, so the same end-to-end
  test that drives the CLI via `typer.testing.CliRunner`
  drives the TUI through `Pilot`. `tests/test_tui.py` covers
  the form, the screens, and the full Compose → Send-dry-run
  flow with the mocked Swiss Post backend.

## Install

The TUI is opt-in via the `gui` extra:

```bash
pip install 'postcards[gui]'
# or, in a virtualenv / pipx install:
pipx install 'postcards[gui]'
```

Without the extra, `postcards tui` exits with a clear
"install `postcards[gui]`" message — the core CLI keeps
working.

## Launch

```bash
postcards tui
```

Optional flags:

| Flag | Purpose |
| --- | --- |
| `--config PATH` / `-c PATH` | Path to `config.json`. Mirrors `postcards send --config`. |
| `--accounts-file PATH` / `-a PATH` | Dedicated accounts file (mirrors `--accounts-file`). |
| `--send` | Disable the default dry-run mode. The TUI still asks for an explicit `YES` confirmation before sending for real. |

## Screens

### Main menu

The landing screen lists the four entry points:

* **Compose** (c) — open the compose form.
* **Browse addresses** (a) — read-only browser for the
  address book. Edits happen via
  `postcards addresses add NAME --prename ...`, never inside
  the TUI.
* **Browse templates** (t) — read-only browser for message
  templates.
* **Help / about** (?) — keyboard shortcuts and pointers to
  `postcards doctor`.
* **Quit** (q).

### Compose

The form is a Textual `Input` + `Select` + `Checkbox` layout:

* **Recipient** — pick from the address book. Entries are
  loaded once on startup from
  `$XDG_DATA_HOME/postcards/addressbook.json`.
* **Sender** — pick from the address book, or accept the
  default ("use recipient as sender") to mirror the CLI's
  behaviour.
* **Picture** — local path to an image, or
  `<plugin>:<value>` (e.g. `folder:birthday`).
* **Message** — up to 500 characters. The character counter
  turns red as the limit approaches.
* **Template** — optional. Pick a message template from the
  template book; the body is rendered on send with the
  supplied `KEY=VALUE` variables.
* **Dry-run** — checked by default. Uncheck to enable the
  "Send real" button (which still asks for `YES`).

### Buttons

* **Preview (Ctrl+P)** — renders the postcard to a temp PNG
  via the same `postcards.render` pipeline `postcards preview
  --output` uses, and shows the path on a modal.
* **Send dry-run (Ctrl+S)** — runs the full send pipeline
  with `--mock` so the swissID login + free-card allowance are
  never touched.
* **Send real (Ctrl+Shift+S)** — opens the confirm modal.
  The Send button stays disabled until you type `YES`
  (uppercase, exactly).

### Preview modal

The modal shows the path and size of the rendered PNG so you
can open it with your image viewer. No network was touched;
no quota was consumed.

### Send confirm modal

The last-line-of-defence modal. Type `YES` (uppercase) to
enable the Send button. Typing `yes`, `Yes`, or anything else
keeps the button disabled.

## Safety model

The TUI defaults to **dry-run** mode. The send path is gated
by two independent checks:

1. The "Dry-run" checkbox in the Compose screen must be off
   before the Send-real button will open the confirm modal.
2. The confirm modal requires an exact `YES` to enable the
   final Send button.

This matches the CLI's `postcards send --dry-run` /
`postcards send --send` distinction while keeping the safety
in the user's hands.

## How the TUI talks to the rest of `postcards`

The TUI is a **thin layer** on top of the existing pipeline.
It does not duplicate any of the logic — it builds the same
`argparse.Namespace` shape the CLI uses and calls
`Postcards.do_command_send(args, config_dict=...)`.

| TUI action | Underlying call |
| --- | --- |
| Preview render | `postcards.render.render_postcard(postcard, path)` |
| Compose config | Built in-memory from the address-book entry + form |
| Send | `Postcards().do_command_send(args, config_dict=cfg)` |
| Address book read | `load_address_book()` (one-shot on startup) |
| Template render | `MessageTemplate.render({key: value, ...})` |

The mocked Swiss Post shim is patched by
`tests/test_tui.py` exactly the way the CLI integration
tests patch it — the same `Token.has_valid_credentials`,
`PostcardCreatorBase.has_free_postcard`, and
`PostcardCreatorBase.send_free_card` triple.

## Keyboard reference

| Key | Action |
| --- | --- |
| `c` | Compose |
| `a` | Browse addresses |
| `t` | Browse templates |
| `?` | Help |
| `q` / `Ctrl+C` | Quit |
| `Esc` | Back / cancel |
| `Ctrl+P` | Preview the current form |
| `Ctrl+S` | Send (dry-run) |
| `Ctrl+Shift+S` | Send (real) — opens the confirm modal |

## Troubleshooting

**"the TUI requires the 'gui' extra"**
Install it: `pip install 'postcards[gui]'`.

**"no recipient selected" / "no address-book entry named X"**
Run `postcards addresses add NAME --prename ... --lastname ...
--street ... --zip-code ... --place ... --category recipient`
to create one, then re-launch the TUI.

**Picture fails to load**
The preview path only reads local files. If you want to use a
plugin image (`folder:birthday`, `pexels:cat`, ...) pass it in
the Picture field as `<plugin>:<value>`; the Send path
resolves plugins through the registry the same way the CLI
does.

**The TUI does not render correctly**
Try resizing your terminal to at least 80x24. The Textual
grid is responsive but expects a real terminal; things like
`cat foo.txt | postcards tui` will not work because the
event loop needs a TTY.

## Why the TUI is read-only against the address book and templates

The TUI uses the existing atomic-JSON writers under
`$XDG_DATA_HOME/postcards/`, but it never *writes* to them.
Mutations happen via the dedicated `postcards addresses ...
add/update/remove` and `postcards templates ... add/...`
commands, where the on-disk format and validation rules live
in one place. The TUI's job is to compose and send; the CLI's
job is to curate the data.

## Reference

* [`postcards.tui`](../postcards/tui/) — Python package.
* [`postcards.cli.commands.tui`](../postcards/cli/commands/tui.py) —
  the `postcards tui` Typer subcommand.
* [`tests/test_tui.py`](../tests/test_tui.py) — Pilot-driven
  unit + integration tests.
* [Textual docs](https://textual.textualize.io/) — the
  underlying TUI framework.
