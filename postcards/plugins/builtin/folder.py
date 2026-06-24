"""``folder`` — pick a random local image as postcard picture.

This is the M3 port of the legacy
``postcards.plugin_folder.postcards_folder.PostcardsFolder`` plugin.
It supports the same configuration shape as the legacy version
(``payload.folder``, optional ``payload.move``) but uses the modern
:class:`Plugin` API and has no dependency on the legacy
:class:`postcards.postcards.Postcards` class.

Configuration payload
---------------------

``payload.folder`` (required)
    Path to a local directory containing pictures.
``payload.move`` (optional, default ``False``)
    When ``True``, move the chosen picture into a ``sent/``
    subdirectory after a successful pick, so the same picture
    is not sent twice in a row.

The plugin picks the next picture in the following order:

1. Files in a ``.priority`` subdirectory of ``payload.folder``
   (chosen uniformly at random).
2. Files directly in ``payload.folder`` (chosen uniformly at random).

Supported picture extensions are ``.jpg``, ``.jpeg``, ``.png``.
The plugin does not validate the picture content — that is the
caller's responsibility (``postcards send`` runs the image
pipeline on the resulting bytes).
"""

from __future__ import annotations

import os
import random
from collections.abc import Mapping
from io import BytesIO
from typing import Any, ClassVar

from postcards.plugins.base import PluginResult
from postcards.plugins.base_impl import PluginBase
from postcards.plugins.builtin._helpers import make_absolute
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.registry import register


class FolderPlugin(PluginBase):
    """Pick a random picture from a local folder."""

    name: ClassVar[str] = "folder"
    description: ClassVar[str] = "pick a random picture from a local folder"

    #: Picture file extensions the plugin will pick.
    supported_ext: ClassVar[tuple[str, ...]] = (".jpg", ".jpeg", ".png")
    #: Subdirectory name scanned before ``payload.folder`` itself.
    high_prio_subdir: ClassVar[str] = ".priority"
    #: Subdirectory the chosen picture is moved into when ``move=True``.
    sent_subdir: ClassVar[str] = "sent"

    def configure(self, payload: Mapping[str, Any]) -> None:
        folder = payload.get("folder")
        if not folder or not isinstance(folder, str):
            raise PluginConfigError(self.name, "'folder' (str) is required in the payload")
        super().configure(payload)

    def render(self) -> PluginResult:
        folder = make_absolute(str(self._payload["folder"]))
        move = bool(self._payload.get("move", False))

        priority, regular = self._list_candidates(folder)
        if priority:
            chosen = random.choice(priority)
            self.logger.debug("picked from .priority/: %s (out of %d)", chosen, len(priority))
        elif regular:
            chosen = random.choice(regular)
            self.logger.debug("picked from root: %s (out of %d)", chosen, len(regular))
        else:
            raise PluginRenderError(self.name, f"no images found in {folder}")

        picture_path = os.path.join(folder, chosen)
        self.logger.info("choosing image %s", picture_path)

        # Read the picture into memory so we can close the source
        # file handle immediately. The backend hands the bytes to
        # PIL and discards them — leaving a file handle open
        # until PIL is done would tie a file-descriptor to the
        # plugin's lifetime for no reason.
        try:
            with open(picture_path, "rb") as fp:
                data = fp.read()
        except OSError as exc:
            raise PluginRenderError(self.name, f"cannot read {picture_path}: {exc}") from exc

        if move:
            self._move_to_sent(folder, picture_path)
            self.logger.debug("moved %s to %s/", picture_path, self.sent_subdir)

        return PluginResult(image=BytesIO(data))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _list_candidates(self, folder: str) -> tuple[list[str], list[str]]:
        """Return ``(priority, regular)`` candidate lists for ``folder``.

        ``priority`` is non-empty when the ``.priority/`` subdir
        contains any supported picture — callers should pick from
        ``priority`` exclusively in that case. ``regular`` holds
        pictures in ``folder`` itself.

        Raises
        ------
        PluginRenderError
            When ``folder`` does not exist.
        """
        if not os.path.isdir(folder):
            raise PluginRenderError(self.name, f"folder {folder!r} does not exist")

        priority_dir = os.path.join(folder, self.high_prio_subdir)
        priority: list[str] = []
        if os.path.isdir(priority_dir):
            for entry in os.listdir(priority_dir):
                if self._is_supported(entry):
                    priority.append(os.path.join(self.high_prio_subdir, entry))

        regular: list[str] = []
        for entry in os.listdir(folder):
            if self._is_supported(entry):
                regular.append(entry)

        return priority, regular

    def _is_supported(self, filename: str) -> bool:
        return filename.lower().endswith(self.supported_ext)

    def _move_to_sent(self, picture_folder: str, image_path: str) -> None:
        sent_dir = os.path.join(picture_folder, self.sent_subdir)
        os.makedirs(sent_dir, exist_ok=True)
        os.rename(image_path, os.path.join(sent_dir, os.path.basename(image_path)))


# Register the plugin with the package-wide registry. Importing
# :mod:`postcards.plugins.builtin` triggers this call.
register(FolderPlugin.name, FolderPlugin, description=FolderPlugin.description)


__all__ = ["FolderPlugin"]
