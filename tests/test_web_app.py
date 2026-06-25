"""Smoke test that the NiceGUI compose page builds without error.

Uses NiceGUI's headless user-simulation (no browser/Selenium) to run
the ``@ui.page`` builder end-to-end — this catches NiceGUI API misuse
that import-time checks miss. Skipped automatically when the optional
``app`` extra (NiceGUI) is not installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

from nicegui.testing import User


@pytest.mark.nicegui_main_file("postcards/web/__main__.py")
async def test_compose_page_builds(user: User) -> None:
    """The page renders its key sections without raising."""
    await user.open("/")
    await user.should_see("Live preview")
    await user.should_see("Recipient")
    await user.should_see("Sender")
    await user.should_see("Message")
    await user.should_see("Send postcard")
