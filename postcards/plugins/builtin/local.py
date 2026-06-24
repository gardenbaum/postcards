"""``local`` — pick a picture from a local directory, deterministically.

This plugin is the **deterministic** counterpart to the
``folder`` plugin. Where ``folder`` picks uniformly at random
(useful for surprise-me sends), ``local`` is meant for
scheduled / cron-driven workflows where the same postcard
should be reproducible day after day:

* It sorts matching files by name so the *first* picture in
  the configured folder is always the *first* one picked.
* After sending, it advances an internal cursor to the next
  picture so the *next* send picks a different one (round-
  robin). The cursor state lives in a tiny JSON file the
  user points at via ``payload.cursor_file``; the cursor
  survives process restarts so a cron job picks up where it
  left off.

Configuration payload
---------------------

``payload.folder`` (required)
    Path to a local directory containing pictures.
``payload.pattern`` (optional, default ``"*"``)
    Glob pattern relative to ``payload.folder``. Defaults to
    every file in the folder; common choices are
    ``"*.jpg"``, ``"landscape/*.png"``, ...
``payload.message`` (optional)
    Postcard message text. When ``None``, the CLI's
    ``-m``/``--message`` option wins.
``payload.cursor_file`` (optional)
    Path to a JSON file holding the round-robin cursor. The
    file format is::

        {"next_index": 0}

    When omitted, the cursor lives in memory only — every
    call to ``postcards send`` restarts at index 0, so the
    same picture is picked on every invocation. This is the
    safe default for ad-hoc use; configure
    ``cursor_file`` when you want cron-driven rotation.

Supported picture extensions
----------------------------

Same set as the ``folder`` plugin: ``.jpg``, ``.jpeg``,
``.png``. The pattern is applied first; extension filtering
is applied second, so a pattern like ``"*.jpg"`` skips the
PNG check.
"""

from __future__ import annotations

import fnmatch
import json
import os
from collections.abc import Mapping
from io import BytesIO
from typing import Any, ClassVar

from postcards.plugins.base import PluginResult
from postcards.plugins.base_impl import PluginBase
from postcards.plugins.builtin._helpers import make_absolute
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.registry import register


class LocalPlugin(PluginBase):
    """Pick a picture from a local folder, deterministically (round-robin)."""

    name: ClassVar[str] = "local"
    description: ClassVar[str] = "pick a picture from a local folder, round-robin"

    #: Picture file extensions the plugin will consider.
    #: Applied **after** the glob pattern, so the pattern
    #: can still pre-filter (e.g. ``landscape/*.jpg``).
    supported_ext: ClassVar[tuple[str, ...]] = (".jpg", ".jpeg", ".png")

    def __init__(self) -> None:
        super().__init__()
        # In-memory cursor. Starts at 0 (first picture) for
        # every plugin instance; advanced on each ``render``
        # call. When ``cursor_file`` is configured, the same
        # value is mirrored to disk so a cron job picking up
        # in a fresh process sees the correct next index.
        self._cursor_index: int = 0
        # Whether ``_cursor_index`` has been hydrated from the
        # on-disk cursor file. We hydrate lazily on the first
        # ``render`` call so plugins that never render do not
        # pay the disk-read cost.
        self._cursor_hydrated: bool = False

    def configure(self, payload: Mapping[str, Any]) -> None:
        folder = payload.get("folder")
        if not folder or not isinstance(folder, str):
            raise PluginConfigError(self.name, "'folder' (str) is required in the payload")

        pattern = payload.get("pattern", "*")
        if not isinstance(pattern, str):
            raise PluginConfigError(self.name, "'pattern' must be a string when present")

        message = payload.get("message")
        if message is not None and not isinstance(message, str):
            raise PluginConfigError(self.name, "'message' must be a string when present")

        cursor_file = payload.get("cursor_file")
        if cursor_file is not None and not isinstance(cursor_file, str):
            raise PluginConfigError(self.name, "'cursor_file' must be a string path when present")

        super().configure(payload)

    def render(self) -> PluginResult:
        folder = make_absolute(str(self._payload["folder"]))
        pattern = str(self._payload.get("pattern", "*"))

        if not os.path.isdir(folder):
            raise PluginRenderError(self.name, f"folder {folder!r} does not exist")

        matches = self._list_matches(folder, pattern)
        if not matches:
            raise PluginRenderError(
                self.name,
                f"no pictures matching pattern {pattern!r} in {folder}",
            )

        # Wrap-around: when the cursor advances past the end of
        # the list, restart at 0 so the plugin never gets stuck.
        index = self._read_cursor(folder)
        chosen_rel = matches[index % len(matches)]
        chosen_path = os.path.join(folder, chosen_rel)
        self.logger.info("choosing image %s (index %d of %d)", chosen_path, index, len(matches))

        try:
            with open(chosen_path, "rb") as fp:
                data = fp.read()
        except OSError as exc:
            raise PluginRenderError(self.name, f"cannot read {chosen_path}: {exc}") from exc

        # Advance the cursor AFTER producing the picture so a
        # render-time failure does not move the pointer forward.
        # The in-memory counter is the source of truth; the disk
        # mirror is updated only when ``cursor_file`` is set.
        self._cursor_index = index + 1
        self._write_cursor(folder, self._cursor_index)

        message_raw = self._payload.get("message")
        message: str | None = str(message_raw) if isinstance(message_raw, str) else None

        return PluginResult(image=BytesIO(data), message=message)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _list_matches(self, folder: str, pattern: str) -> list[str]:
        """Return the matching picture paths, sorted by name.

        The list contains paths **relative to** ``folder`` so
        the cursor remains stable across folder renames
        (only the file order matters for round-robin).
        """
        all_names = sorted(os.listdir(folder))
        by_pattern = [n for n in all_names if fnmatch.fnmatch(n, pattern)]
        return [n for n in by_pattern if n.lower().endswith(self.supported_ext)]

    def _read_cursor(self, folder: str) -> int:
        # Lazy hydrate: on the first render of a brand-new
        # plugin instance, mirror the on-disk cursor (if any)
        # into ``_cursor_index``. Subsequent renders trust the
        # in-memory value.
        if not self._cursor_hydrated:
            self._cursor_index = self._read_disk_cursor()
            self._cursor_hydrated = True
        return self._cursor_index

    def _read_disk_cursor(self) -> int:
        path = self._cursor_path()
        if not path or not os.path.isfile(path):
            return 0
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("cannot read cursor %s (%s); restarting at 0", path, exc)
            return 0
        index = data.get("next_index", 0) if isinstance(data, dict) else 0
        try:
            return int(index)
        except (TypeError, ValueError):
            return 0

    def _cursor_path(self) -> str:
        """Return the on-disk cursor path, or empty string for in-memory."""
        cursor_raw = self._payload.get("cursor_file")
        if not isinstance(cursor_raw, str):
            return ""
        return make_absolute(cursor_raw)

    def _write_cursor(self, folder: str, next_index: int) -> None:
        path = self._cursor_path()
        if not path:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"next_index": next_index}, f)
        os.replace(tmp_path, path)


register(LocalPlugin.name, LocalPlugin, description=LocalPlugin.description)


__all__ = ["LocalPlugin"]
