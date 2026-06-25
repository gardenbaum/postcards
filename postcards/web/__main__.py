"""``python -m postcards.web`` — launch the WYSIWYG web app.

A thin module-level entry point (importing it starts the server) so the
app can be launched without the ``postcards`` console script, and so
the NiceGUI test harness has a ``ui.run()``-at-import target. The
``postcards app`` CLI command is the supported user-facing launcher.
"""

from __future__ import annotations

from postcards.web.app import run_app

# Allow both the normal run and NiceGUI's multiprocessing reload fork.
if __name__ in {"__main__", "__mp_main__"}:
    run_app()
