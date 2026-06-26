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
- **Backend = SwissID** (live): reaches the real Swiss Post service. Three
  ways to authenticate (see below) — **SMS login** (for accounts whose
  second factor is an SMS code), **browser login** (works with any 2FA:
  push / passkey / SMS), or direct **e-mail + password** (no-2FA accounts).
- **Dry-run** (on by default): validates the card with the selected
  backend *without* actually mailing it. With the live backend this
  checks the card upstream **without consuming your daily quota**. Turn it
  off to mail a real postcard.

### Logging in to SwissID

A live send performs the **real** SwissID OAuth + SAML login and posts
the card to the Swiss Post Postcard Creator mobile API. The same engine
backs the CLI `postcards send`.

**SMS login (recommended if your second factor is an SMS code).** An SMS
code is the one second factor that can be entered directly in the app — you
just read it off your phone and type it in:

1. Enter your SwissID **e-mail + password** above, then click
   **1 · Login & send SMS**. The app submits them and SwissID texts you a
   code.
2. Type the code into **SMS code** and click **2 · Confirm SMS code**. The
   app finishes the login (`api-login` → SAML → token) and is authenticated.
   Send as usual.

Push and passkey *cannot* be answered headlessly (there is nothing to type),
so for those use the browser login below.

> **Note on SMS support.** No public client has ever implemented SwissID's
> headless SMS step — its exact wire format is undocumented. This app drives
> the documented `api-login` state machine and finalizes the SMS step from
> the real challenge it captures on your **first** attempt (surfaced in the
> app and logged). If the first try doesn't complete, the captured challenge
> is exactly what's needed to finish wiring it — report it. The browser login
> always works as a fallback.

**Browser login (works with any 2FA: push / passkey / SMS).** The app hands
the login to your real browser, so any second factor works:

1. Click **1 · Open SwissID login** — a new tab opens the SwissID login.
2. Log in and **approve the push in your SwissID app** (or passkey / SMS).
3. The browser ends on a page it *can't* open — an address starting
   `ch.post.pcc://…` containing `?code=…`. **Copy that whole address**
   (or just the `code`) and paste it into the app.
4. Click **2 · Complete login** — the app exchanges the code for a token
   (PKCE) and is then authenticated. Send as usual.

**Direct e-mail + password** (the fields + *Check login & quota*) works
**only for accounts without 2FA** — the upstream consumer flow never
supported an interactive second factor
([`postcard_creator_wrapper` #40](https://github.com/abertschi/postcard_creator_wrapper/issues/40)).
If your account enforces 2FA, use the SMS or browser login above.

Other notes:

- The free tier is **one card per day** per SwissID account.
- **Prerequisite:** the account must have signed in to the official
  Postcard Creator app at least once to activate the free tier.
- The unofficial endpoints can change server-side, so a live send may
  break regardless of this app — that fragility is why the test suite /
  CI only use the mock backend. For unattended/business use, Swiss Post's
  official [PostCard Creator API](https://developer.post.ch/en/technical-specifications-of-postcard-api)
  (OAuth2 + contract) is the robust route (not wired in here).
- Credentials are used for the single login only and never stored unless
  you click *Save to keyring*; never commit credentials.

## How it fits together

The app is a thin UI over a network-free service layer
(`postcards.web.service`), which is fully unit-tested against the mock
backend. The UI calls the same `Backend` interface, image pipeline and
renderer as the CLI, so behaviour is consistent across both front-ends.
