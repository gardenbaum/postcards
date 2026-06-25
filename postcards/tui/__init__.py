"""Optional local TUI for the ``postcards`` CLI.

The TUI is a thin Textual-based layer on top of the existing
``postcards`` pipeline. It is **purely additive**:

* It does not duplicate the credential resolution, address-book
  loading, template rendering, image pipeline, or send flow.
* It does not call the Swiss Post backend directly. Sending
  delegates to :func:`postcards.cli.commands.send.send_cmd` so
  the existing mocked-backend integration tests cover the
  same code path the TUI exercises.
* It is opt-in via the ``postcards[gui]`` extra and lives in its
  own package so the core CLI works on systems where
  :mod:`textual` (or its transitive deps) cannot be installed.

The TUI's job is to be a friendlier front-end for the same
workflow the CLI already supports::

    # CLI
    postcards send --picture cat.jpg --message "Hello from Zuerich"

    # TUI (interactive)
    postcards tui

Rationale
---------

A Web UI (FastAPI + htmx, served on ``localhost``) was the
other option. The TUI was chosen because:

* It does not need a browser, a port, or a second process.
* It runs in the same terminal the rest of the tool runs in —
  no copy-paste of paths, no second window.
* It works over SSH and inside headless containers (the
  Docker image uses ``Environment :: Console``).
* :mod:`textual`'s :class:`textual.pilot.Pilot` makes the
  app fully unit-testable: ``tests/test_tui.py`` drives the
  TUI through a deterministic in-memory harness instead of
  relying on a real terminal.

Public surface
--------------

* :func:`run_tui` — entry point used by the ``postcards tui``
  CLI subcommand.
* :class:`PostcardsApp` — the :class:`textual.app.App` subclass
  tests can instantiate directly.
* :class:`ComposeForm` — the in-memory form model shared by the
  Compose / Preview / Send-Confirm screens.

Safety
------

The TUI defaults to **dry-run**: pressing "Send" with the
default mode renders a preview file and reports what would
have been sent, without contacting Swiss Post. To actually
send, the user must explicitly opt in via the ``--send``
flag (CLI) or by checking the "really send" toggle in the
Compose screen. The :class:`SendConfirmScreen` modal is the
last line of defence.
"""

from __future__ import annotations

from postcards.tui.app import PostcardsApp, run_tui
from postcards.tui.state import ComposeForm

__all__ = ["ComposeForm", "PostcardsApp", "run_tui"]
