# Batch send — `postcards batch`

`postcards batch` is the M4 multi-recipient counterpart of
`postcards send`. Where `send` dispatches a single postcard
to the recipient in the on-disk config (or `--to`), `batch`
dispatches one postcard to each of many recipients — driven
by an address-book filter, an inline name list, or a manifest
file.

The command reuses the same send plumbing as `postcards send`,
so every input the latter accepts (picture, message,
message-template with `--var`, sender) is also accepted by
`batch`. Per-recipient overrides on a manifest row win over
the shared CLI flags; otherwise the flag value is used for
every recipient.

## Recipient sources

Exactly one of the following must be supplied.

### `--to-many NAME1,NAME2,...`

A comma-separated list of address-book recipient names. The
names are validated against the address book before the first
send, so a typo surfaces as a usage error rather than a
mid-batch failure.

```
postcards batch \
    --to-many alice,bob,charlie \
    --message "Hello from Zurich"
```

### `--to-all-recipients`

Every recipient entry in the address book. Senders are
filtered out automatically so a mixed book does not send
to itself.

```
postcards batch \
    --to-all-recipients \
    --message "Hello from Zurich"
```

### `--manifest <file>`

A CSV or YAML file listing the recipients. The format is
chosen from the file extension (`.yaml` / `.yml` → YAML,
otherwise CSV).

#### CSV

The header must include a `to` column. Optional columns:

| Column             | Meaning                                            |
| ------------------ | -------------------------------------------------- |
| `to` (required)    | Address-book recipient name                        |
| `picture`          | Per-recipient picture (overrides `--picture`)      |
| `message`          | Per-recipient message (overrides `--message`)      |
| `message_template` | Per-recipient template name                        |
| `sender`           | Per-recipient sender name                          |
| `var`              | Semicolon-separated `KEY=VALUE` pairs              |

```csv
to,picture,message,var
alice,/pics/alice.jpg,Hi Alice,name=Alice
bob,,Hi Bob,name=Bob
```

#### YAML

Two shapes are accepted: a flat list of names, or a list of
objects with optional per-recipient overrides.

```yaml
recipients:
  - alice
  - bob
  - charlie
```

```yaml
recipients:
  - to: alice
    picture: /pics/alice.jpg
    message: "Hi Alice"
    var:
      name: Alice
  - to: bob
    message_template: greeting
    var: [name=Bob]
```

A top-level YAML list (without a `recipients:` key) is
accepted for symmetry with the flat CSV case:

```yaml
- alice
- bob
```

## Per-recipient overrides

Manifest entries can override any of the shared CLI flags on
a per-row basis. The precedence is:

1. The manifest row's per-recipient value (when present).
2. The shared CLI flag (`--picture`, `--message`,
   `--message-template`, `--var`, `--sender`).

This lets the user express a heterogeneous batch (some
recipients get a custom message, others inherit the shared
one) without giving up the "send the same card to everyone"
ergonomics for the common case.

## Error handling

By default `batch` continues dispatching recipients after a
failure — the bad row is recorded in the per-recipient
summary and the loop moves on. Pass `--stop-on-error` to
abort on the first failure instead.

The summary is printed to stdout (`ok` rows) and stderr
(`FAIL` rows). The process exits non-zero when at least one
recipient fails so cron / CI can detect partial success.

## Examples

Send the same card to every recipient in the address book:

```
postcards batch \
    --to-all-recipients \
    --message "Hello from Zurich" \
    --username USER --password PASS
```

Send to a hand-picked list with a shared picture:

```
postcards batch \
    --to-many oma-luzern,opa-bern \
    --picture /pics/alps.jpg \
    --message "Liebe Grüsse aus den Bergen"
```

Use a manifest with per-recipient overrides:

```
postcards batch \
    --manifest ./birthdays.yaml \
    --username USER --password PASS
```

Dry-run the batch (no cards sent) to see what would happen:

```
postcards batch \
    --to-all-recipients \
    --message "Hello" \
    --dry-run
```