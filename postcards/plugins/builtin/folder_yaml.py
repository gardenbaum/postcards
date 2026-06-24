"""``folder_yaml`` — pick a (text, image) pair from a YAML playlist.

This is the M3 port of the legacy
``postcards.plugin_folder_yaml.postcards_folder_yaml.PostcardsFolderYaml``
plugin. The YAML document is a flat list of alternating
``(text, image_path)`` entries; the plugin picks the first pair,
optionally pops it from the document, and writes the document
back to disk.

Configuration payload
---------------------

``payload.folder`` (required)
    Path to a local directory containing the pictures referenced
    from the YAML document.
``payload.yaml`` (required)
    Path to the YAML document (relative paths inside the YAML
    are resolved against ``payload.folder``).
``payload.move`` (optional, default ``False``)
    When ``True``, move the chosen picture into a ``sent/``
    subdirectory of ``payload.folder`` after a successful pick.
``payload.remove_yaml`` (optional, default ``True``)
    When ``True``, the (text, image) pair is removed from the
    YAML document after being picked so the next run sees a
    fresh pair. When ``False``, the document is left untouched
    and the first pair is picked repeatedly.

YAML format
-----------

The document is a flat YAML list whose entries alternate between
``text`` strings and ``image_path`` strings::

    - "Hi from Zurich!"
    - zurich.jpg
    - "Greetings from Bern"
    - bern.jpg

The list must contain an even number of entries (one text + one
image = two entries per postcard).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from io import BytesIO
from typing import Any, ClassVar

import yaml

from postcards.plugins.base import PluginResult
from postcards.plugins.base_impl import PluginBase
from postcards.plugins.builtin._helpers import make_absolute
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.registry import register


class FolderYamlPlugin(PluginBase):
    """Pick a (text, image) pair from a YAML playlist."""

    name: ClassVar[str] = "folder_yaml"
    description: ClassVar[str] = "pick a (text, image) pair from a YAML playlist"
    sent_subdir: ClassVar[str] = "sent"

    def configure(self, payload: Mapping[str, Any]) -> None:
        folder = payload.get("folder")
        yaml_path = payload.get("yaml")
        if not folder or not isinstance(folder, str):
            raise PluginConfigError(self.name, "'folder' (str) is required in the payload")
        if not yaml_path or not isinstance(yaml_path, str):
            raise PluginConfigError(self.name, "'yaml' (str) is required in the payload")
        super().configure(payload)

    def render(self) -> PluginResult:
        folder = make_absolute(str(self._payload["folder"]))
        yaml_path = make_absolute(str(self._payload["yaml"]))
        move = bool(self._payload.get("move", False))
        remove_yaml = bool(self._payload.get("remove_yaml", True))

        if not os.path.isdir(folder):
            raise PluginRenderError(self.name, f"picture directory {folder!r} does not exist")
        if not os.path.isfile(yaml_path):
            raise PluginRenderError(self.name, f"yaml file {yaml_path!r} does not exist")

        document = self._load_yaml(yaml_path)
        if len(document) < 2:
            raise PluginRenderError(self.name, "nothing left to do, no entries in yaml")

        text, image_rel = str(document[0]), str(document[1])
        image_path = os.path.join(folder, image_rel)
        if not os.path.isfile(image_path):
            raise PluginRenderError(
                self.name, f"image referenced from yaml does not exist: {image_path}"
            )

        if remove_yaml:
            document = document[2:]
            self._save_yaml(yaml_path, document)

        # Read the picture into memory and return a BytesIO so the
        # caller does not have to manage the file-handle lifetime.
        try:
            with open(image_path, "rb") as fp:
                data = fp.read()
        except OSError as exc:
            raise PluginRenderError(self.name, f"cannot read {image_path}: {exc}") from exc

        if move:
            sent_dir = os.path.join(folder, self.sent_subdir)
            os.makedirs(sent_dir, exist_ok=True)
            os.rename(image_path, os.path.join(sent_dir, os.path.basename(image_path)))

        self.logger.info(
            "choosing image %s (move=%s, remove_yaml=%s)", image_path, move, remove_yaml
        )
        self.logger.info("choosing text %r", text)

        return PluginResult(image=BytesIO(data), message=text)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_yaml(self, yaml_path: str) -> list[object]:
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except OSError as exc:
            raise PluginRenderError(self.name, f"cannot read yaml file {yaml_path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise PluginRenderError(
                self.name, f"cannot parse yaml file {yaml_path}: {exc}"
            ) from exc

        if not isinstance(data, list):
            raise PluginRenderError(self.name, "yaml document must be a list")
        if len(data) % 2 != 0:
            raise PluginRenderError(self.name, "yaml document must have an even number of entries")

        # Validate that every odd-indexed entry points to an existing file.
        # Even-indexed entries are the message text.
        for idx in range(1, len(data), 2):
            image_rel = data[idx]
            if not isinstance(image_rel, str):
                raise PluginRenderError(
                    self.name,
                    f"yaml entry {idx} must be a string path, got {type(image_rel).__name__}",
                )
        return list(data)

    def _save_yaml(self, yaml_path: str, document: list[object]) -> None:
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(document, f, allow_unicode=True)


register(FolderYamlPlugin.name, FolderYamlPlugin, description=FolderYamlPlugin.description)


__all__ = ["FolderYamlPlugin"]
