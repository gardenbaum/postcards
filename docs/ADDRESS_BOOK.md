# Address book + message templates

`postcards` keeps your recipients, senders, and reusable message
text in a small per-user store under the XDG data directory.
This makes a single canonical address book and template book
that every `postcards` project on your machine can share — the
book is not project-local the way `config.json` is.

## Where the data lives

The default location follows the [XDG Base Directory
Specification]:

[XDG Base Directory Specification]: https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html

| Variable        | Resolved path                          |
| --------------- | -------------------------------------- |
| `$XDG_DATA_HOME` | `$XDG_DATA_HOME/postcards/`           |
| fallback        | `$HOME/.local/share/postcards/`        |

Two files live there:

* `addressbook.json` — the address book.
* `templates.json`   — the message templates.

You can override the directory with the `POSTCARDS_DATA_DIR`
environment variable, which is what tests use to keep their
fixtures hermetic. The CLI never reads from a project-local
`config.json` for this data — the books are user data, not
project data.

## Address book

The address book holds named recipients and senders. Each
entry is keyed by a short, shell-safe identifier
(`[a-z0-9][a-z0-9._-]*`, max 64 chars) so you can reference it
from `postcards send --to NAME` without quoting.

### Commands

```sh
# Add a recipient.
postcards addresses add alice \
    --prename Alice --lastname Zuercher \
    --street "Bahnhofstrasse 1" --zip-code 8000 --place Zurich \
    --salutation "Ms." \
    --notes "vacation 2024"

# Add a sender (the return address).
postcards addresses add home \
    --category sender \
    --prename Andrin --lastname Bertschi \
    --street "Lagerstrasse 1" --zip-code 8000 --place Zurich --country CH

# List the entries (filterable by category).
postcards addresses list
postcards addresses list --category recipient

# Show one entry in full.
postcards addresses show alice

# Update fields; pass `--notes ""` to clear a field explicitly.
postcards addresses update alice --place Bern --street "Marktgasse 5"

# Remove (prompts unless --yes is given).
postcards addresses remove alice --yes
```

The CLI distinguishes between "option not supplied" and
"option supplied as empty string". `addresses update` uses
this to keep existing values unless you explicitly override
them — and to clear a field you pass `--notes ""`.

### Use in `send`

```sh
postcards send \
    -c config.json \
    --to alice \
    --sender home \
    --picture pic.jpg \
    --username ... --password ... \
    --dry-run
```

`--to` requires a recipient entry; pointing it at a sender
(or vice-versa) is a CLI error with exit code 2, not a
silent fallback.

## Message templates

Templates are short messages with `$name` / `${name}`
placeholders. The substitution uses Python's
[`string.Template`][string.Template] syntax, which is the
standard choice for user-authored text: `$name` for simple
identifiers, `${name}` when the placeholder is followed by
text (e.g. `${name}!`), and `$$` to render a literal dollar
sign.

[string.Template]: https://docs.python.org/3/library/string.html#template-strings

### Commands

```sh
# Add a template (body via --body, --file, or stdin).
postcards templates add greeting \
    --description "default greeting" \
    --body 'Hi $name, greetings from Zurich'

postcards templates add birthday \
    --description "birthday wishes" \
    --body 'Happy birthday, $name!'

# Read the body from a file.
postcards templates add letter \
    --file ./letter.txt

# Or from stdin (piped content).
echo 'Hello $name' | postcards templates add piped --body -

# List templates.
postcards templates list

# Show one template.
postcards templates show greeting

# Render with variable substitution (uses CLI-friendly errors).
postcards templates render greeting --var name=Alice
# -> "Hi Alice, greetings from Zurich"

# Update a template's body or description.
postcards templates update greeting --body 'Hello $name!'

# Remove (prompts unless --yes is given).
postcards templates remove greeting --yes
```

### Strict missing-key semantics

If a template references a variable that is not supplied,
`templates render` and `send --message-template` refuse to
proceed with a clear error message. This is intentional —
it is much better to surface a missing `--var` than to send
a postcard with the literal placeholder text in it.

### Use in `send`

```sh
postcards send \
    -c config.json \
    --to alice \
    --message-template greeting \
    --var name=Alice \
    --picture pic.jpg \
    --username ... --password ... \
    --dry-run
```

`--message-template` and `--message` are mutually exclusive.
Supplying `--var` without `--message-template` is also
rejected, so a stray `--var` does not silently have no
effect.

## On-disk schema

Both files are JSON with a `version` field for forward
compatibility. The current schema is:

```json
{
    "version": 1,
    "entries": [
        {
            "name": "alice",
            "category": "recipient",
            "address": {
                "prename": "Alice",
                "lastname": "Zuercher",
                "street": "Bahnhofstrasse 1",
                "zip_code": "8000",
                "place": "Zurich",
                "company": "",
                "country": "",
                "salutation": "Ms.",
                "company_addition": ""
            },
            "notes": "vacation 2024"
        }
    ]
}
```

```json
{
    "version": 1,
    "templates": [
        {
            "name": "greeting",
            "body": "Hi $name, greetings from Zurich",
            "description": "default greeting"
        }
    ]
}
```

Both files are written atomically (sibling temp file +
`os.replace` + `fsync`) so a crash mid-write cannot corrupt
an existing book. The directory itself carries `0o700`
permissions on POSIX systems so other users on the same
machine cannot read the address book.

## Privacy

The address book is **not** part of the project's
`.gitignore` because it is not in the project. It lives under
`$XDG_DATA_HOME`, which is outside the repository by
construction. You should still keep the file out of any
backups you would not want shared.

The `templates.json` file can contain whatever message text
you like. The templates are user-authored and never
transmitted to the network — only the *rendered* message is
sent when you actually post a card.