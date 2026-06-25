# The `postcards` web app

`postcards app` launches an interactive web app (built with
[NiceGUI](https://nicegui.io/)) for composing a postcard with a **live,
print-accurate WYSIWYG preview** and then sending it. It is the primary
way to use `postcards`; the CLI remains for automation, batch and
scheduling.

## Install & launch

The app needs the optional `app` extra (NiceGUI):

```sh
uvx --from '.[app]' postcards app        # no persistent install
# or
uv pip install -e '.[app]' && uv run postcards app
```

Options:

```sh
postcards app                 # opens http://127.0.0.1:8080 in your browser
postcards app --port 9000     # different port
postcards app --no-browser    # don't auto-open the browser
postcards app --host 0.0.0.0  # bind all interfaces (e.g. in a container)
```

Without the extra, `postcards app` exits with a clear
`install 'postcards[app]'` message.

## The layout

The page is a form on the left, a live preview on the right.

**Form (left):**

- **Picture** — upload an image (any format Pillow reads). It is run
  through the A6 image pipeline (orient → centre-crop → resize → JPEG)
  immediately, so the preview is instant. Leave it empty for a text-only
  card; *Clear picture* removes it.
- **Message** — free text, max 500 characters (a live counter turns red
  if you exceed it). `<b>`, `<i>`, `<br>` are accepted by Swiss Post; the
  preview renders the message as plain text.
- **Recipient** / **Sender** — name, street, ZIP, place (required) and an
  optional country.
- **Send** — pick the backend; for SwissID, manage credentials (see
  below), toggle dry-run, and send.

### Credentials & login (SwissID)

When you select the **SwissID** backend, the app surfaces the full
credential machinery so you never have to drop to the CLI:

- **Auto-resolve** — on load the app resolves accounts in the
  constitution's order (env vars → OS keyring → config file) and
  prefills the e-mail (and password, if found), showing where it came
  from. A **Saved account** dropdown appears when more than one is
  configured.
- **Load password** — fetch the stored password for the entered e-mail
  from the keyring / config into the (masked) field.
- **Save to keyring** — store the entered password in the OS keyring so
  future sessions resolve it automatically.
- **Check login & quota** — perform a real (or mock) login and report
  whether a card is available today (the 1/day free tier) — without
  sending anything.

The app only ever writes a secret when you click **Save to keyring**;
nothing is logged or committed.

**Preview (right):** the **Front** (A6 landscape) and **Back** redraw on
every change.

## What the preview shows

The preview is produced by the same Pillow renderer
(`postcards.render`) the CLI's `postcards preview` uses, so it matches
what Swiss Post prints:

- **Front:** your picture at A6 landscape (1500×1062), with the **3 mm
  bleed** line (cyan, solid — everything outside is trimmed) and the
  inner **safe area** (green, dashed — keep important content inside).
- **Back:** the message on the left, the recipient address in the
  standard zone on the right, the sender below it, and the **postage /
  stamp box** reserved in the top-right corner where Swiss Post prints
  the indicium.

Toggle **Print guides** off to see the card without the overlay.

## Sending

- **Backend = Mock** (default): nothing is sent. Every "send" is recorded
  in-memory — ideal for trying the app out. Safe and offline.
- **Backend = SwissID** (live): reaches the real Swiss Post service. Enter
  your SwissID e-mail and password; they are used only for that send and
  are never stored or logged by the app.
- **Dry-run** (on by default): validates the card with the selected
  backend *without* actually mailing it. With the live backend this
  checks the card upstream **without consuming your daily quota**. Turn it
  off to mail a real postcard.

### SwissID, 2FA & quota — read this before a live send

A live send performs the **real** SwissID OAuth + SAML login and posts
the card to the Swiss Post Postcard Creator mobile API — it is no longer
a stub. The same flow backs the CLI `postcards send`.

- The free tier is **one card per day** per SwissID account.
- **Two-factor authentication is NOT supported.** This is a limitation of
  the unofficial consumer flow (upstream
  [`postcard_creator_wrapper` #40](https://github.com/abertschi/postcard_creator_wrapper/issues/40)):
  the login performs only the e-mail + password + device-fingerprint
  steps. It therefore works **only for SwissID accounts that can log in
  with e-mail + password alone** (no SMS / passkey / push prompt). If your
  account enforces a second factor, the login will fail — disable 2FA on
  the account, or use the official business
  [PostCard Creator API](https://developer.post.ch/en/technical-specifications-of-postcard-api)
  instead (OAuth2 + contract; not wired in here).
- **Prerequisite:** the account must have signed in to the official
  Postcard Creator app at least once to activate the free tier.
- SwissID also uses **anomaly detection**; the unofficial endpoints can
  change server-side, so a live send may break regardless of this app.
  That fragility is exactly why the test suite / CI only use the mock
  backend.
- Credentials are read from the form for the single send only. For the
  CLI, prefer environment variables or the OS keyring (see the README) —
  never commit credentials.

## How it fits together

The app is a thin UI over a network-free service layer
(`postcards.web.service`), which is fully unit-tested against the mock
backend. The UI calls the same `Backend` interface, image pipeline and
renderer as the CLI, so behaviour is consistent across both front-ends.
