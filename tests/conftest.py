"""Shared pytest configuration.

Loads NiceGUI's user-simulation plugin *only when NiceGUI is installed*
(the optional ``app`` extra). The web-app UI test
(:mod:`tests.test_web_app`) ``importorskip``s NiceGUI, so a plain
``pip install -e '.[dev]'`` checkout runs the full suite minus that one
test instead of erroring on a missing plugin.
"""

from __future__ import annotations

try:
    import nicegui  # noqa: F401

    # The ``user`` fixture lives in the user_plugin (no Selenium, unlike
    # the combined ``nicegui.testing.plugin``).
    pytest_plugins = ["nicegui.testing.user_plugin"]
except ImportError:  # pragma: no cover — exercised only on minimal installs
    pass
