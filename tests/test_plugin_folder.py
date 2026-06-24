"""Tests for the ``folder`` M3 plugin.

The folder plugin has no network dependencies — it picks a
random picture from a local directory. Tests use temporary
directories populated with synthetic PNG files (created in
memory via PIL and written to disk) so no fixture files are
needed on disk. PNG is preferred over JPG for assertions
because PNG is lossless and round-trips pixel-exact colors.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from postcards.plugins import PluginResult
from postcards.plugins.builtin.folder import FolderPlugin
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.loader import load_plugin


def _make_picture(path: Path, color: str = "red", fmt: str = "PNG") -> None:
    """Write a tiny 10x10 image to ``path`` in the given format."""
    Image.new("RGB", (10, 10), color=color).save(path, format=fmt)


def _make_picture_bytes(color: str = "red", fmt: str = "PNG") -> bytes:
    """Return the bytes of a tiny image (lossless by default)."""
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=color).save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def picture_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "pics"
    folder.mkdir()
    _make_picture(folder / "a.png", "red")
    _make_picture(folder / "b.png", "green")
    return folder


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_folder_plugin_requires_folder_in_payload(picture_dir: Path) -> None:
    with pytest.raises(PluginConfigError, match="'folder'"):
        load_plugin("folder", {}, registry=None)


def test_folder_plugin_rejects_non_string_folder() -> None:
    with pytest.raises(PluginConfigError):
        load_plugin("folder", {"folder": 42}, registry=None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_folder_plugin_picks_random_image(picture_dir: Path) -> None:
    plugin = load_plugin("folder", {"folder": str(picture_dir)}, registry=None)
    result = plugin.render()
    assert isinstance(result, PluginResult)
    # The picture is returned as an in-memory BytesIO holding the
    # original image bytes. Decode it and check the size + color.
    data = result.image.read()
    picked = Image.open(io.BytesIO(data))
    assert picked.size == (10, 10)
    color = picked.getpixel((0, 0))
    assert color in {(255, 0, 0), (0, 128, 0)}  # red or green


def test_folder_plugin_message_is_none(picture_dir: Path) -> None:
    plugin = load_plugin("folder", {"folder": str(picture_dir)}, registry=None)
    result = plugin.render()
    assert result.message is None


def test_folder_plugin_priority_subdir_is_picked_first(tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    _make_picture(folder / "regular.png", "blue")
    priority = folder / ".priority"
    priority.mkdir()
    _make_picture(priority / "chosen.png", "red")

    # Run several times; every run should pick the priority file.
    plugin = load_plugin("folder", {"folder": str(folder)}, registry=None)
    for _ in range(20):
        result = plugin.render()
        picked = Image.open(result.image)
        assert picked.size == (10, 10)
        # PNG is lossless so the color round-trips exactly.
        assert picked.getpixel((0, 0)) == (255, 0, 0)


def test_folder_plugin_handles_only_priority(tmp_path: Path) -> None:
    """When the root is empty but .priority has files, those win."""
    folder = tmp_path / "pics"
    folder.mkdir()
    priority = folder / ".priority"
    priority.mkdir()
    _make_picture(priority / "only.png", "red")

    plugin = load_plugin("folder", {"folder": str(folder)}, registry=None)
    result = plugin.render()
    picked = Image.open(result.image)
    assert picked.getpixel((0, 0)) == (255, 0, 0)


# ---------------------------------------------------------------------------
# Move semantics
# ---------------------------------------------------------------------------


def test_folder_plugin_move_relocates_picture(picture_dir: Path) -> None:
    plugin = load_plugin("folder", {"folder": str(picture_dir), "move": True}, registry=None)
    result = plugin.render()
    picked_data = result.image.read()

    sent_dir = picture_dir / "sent"
    assert sent_dir.is_dir()
    # The original file (a.png or b.png) should now be in sent/.
    sent_files = list(sent_dir.iterdir())
    assert len(sent_files) == 1
    assert sent_files[0].read_bytes() == picked_data
    # The original location no longer has the file that was moved.
    remaining_files = {p.name for p in picture_dir.iterdir() if p.is_file()}
    # We started with a.png and b.png; exactly one of them is gone
    # (moved into sent/), and ``sent`` itself is now a directory.
    assert len(remaining_files) == 1
    # The directory now contains exactly one file + the sent/ subdir.
    direct_children = list(picture_dir.iterdir())
    assert len(direct_children) == 2
    assert sent_dir in direct_children


def test_folder_plugin_without_move_keeps_picture(picture_dir: Path) -> None:
    plugin = load_plugin("folder", {"folder": str(picture_dir)}, registry=None)
    plugin.render()
    # All original files still present.
    remaining = {p.name for p in picture_dir.iterdir() if p.is_file()}
    assert {"a.png", "b.png"}.issubset(remaining)


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_folder_plugin_empty_folder_raises_render_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    plugin = load_plugin("folder", {"folder": str(empty)}, registry=None)
    with pytest.raises(PluginRenderError, match="no images"):
        plugin.render()


def test_folder_plugin_nonexistent_folder_raises_render_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    plugin = load_plugin("folder", {"folder": str(missing)}, registry=None)
    with pytest.raises(PluginRenderError):
        plugin.render()


def test_folder_plugin_ignores_non_picture_files(tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    (folder / "README.md").write_text("not a picture")
    (folder / "data.json").write_text("{}")

    plugin = load_plugin("folder", {"folder": str(folder)}, registry=None)
    with pytest.raises(PluginRenderError, match="no images"):
        plugin.render()


def test_folder_plugin_supported_extensions() -> None:
    """The plugin accepts .jpg, .jpeg, .png — case-insensitive."""
    assert ".jpg" in FolderPlugin.supported_ext
    assert ".jpeg" in FolderPlugin.supported_ext
    assert ".png" in FolderPlugin.supported_ext


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


def test_folder_plugin_class_metadata() -> None:
    assert FolderPlugin.name == "folder"
    assert "folder" in FolderPlugin.description.lower()


def test_folder_plugin_is_registered_in_default_registry() -> None:
    from postcards.plugins.registry import Registry

    assert Registry.default.has("folder")
    assert Registry.default.get("folder") is FolderPlugin


# ---------------------------------------------------------------------------
# JPG / JPEG support
# ---------------------------------------------------------------------------


def test_folder_plugin_accepts_jpg_and_jpeg(tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    _make_picture(folder / "alpha.jpg", "red", fmt="JPEG")
    _make_picture(folder / "beta.JPEG", "green", fmt="JPEG")
    plugin = load_plugin("folder", {"folder": str(folder)}, registry=None)
    result = plugin.render()
    # Read enough bytes to verify the JPEG SOI marker.
    head = result.image.read(3)
    assert head == b"\xff\xd8\xff"
