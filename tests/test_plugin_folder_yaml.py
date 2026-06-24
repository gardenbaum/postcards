"""Tests for the ``folder_yaml`` M3 plugin.

The folder_yaml plugin reads a YAML playlist of (text, image)
pairs and returns the first pair. Tests use temporary
directories with synthetic PNG files written via PIL (PNG
because the assertions are pixel-exact; JPG would introduce
JPEG-compression noise).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
import yaml
from PIL import Image

from postcards.plugins import PluginResult
from postcards.plugins.builtin.folder_yaml import FolderYamlPlugin
from postcards.plugins.errors import PluginConfigError, PluginRenderError
from postcards.plugins.loader import load_plugin


def _make_picture(path: Path, color: str = "red") -> None:
    Image.new("RGB", (10, 10), color=color).save(path, format="PNG")


def _make_picture_bytes(color: str = "red") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (10, 10), color=color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def yaml_folder(tmp_path: Path) -> tuple[Path, Path, Path]:
    folder = tmp_path / "pics"
    folder.mkdir()
    _make_picture(folder / "first.png", "red")
    _make_picture(folder / "second.png", "green")
    yaml_path = tmp_path / "playlist.yaml"
    return folder, yaml_path, tmp_path


def _write_yaml(path: Path, entries: list[object]) -> None:
    path.write_text(yaml.safe_dump(entries, allow_unicode=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_folder_yaml_requires_folder_and_yaml_keys() -> None:
    with pytest.raises(PluginConfigError, match="'folder'"):
        load_plugin("folder_yaml", {}, registry=None)


def test_folder_yaml_requires_yaml_key(tmp_path: Path) -> None:
    with pytest.raises(PluginConfigError, match="'yaml'"):
        load_plugin("folder_yaml", {"folder": str(tmp_path)}, registry=None)


def test_folder_yaml_rejects_non_string_folder(tmp_path: Path) -> None:
    with pytest.raises(PluginConfigError):
        load_plugin(
            "folder_yaml",
            {"folder": 42, "yaml": str(tmp_path / "y.yaml")},
            registry=None,
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_folder_yaml_picks_first_pair(yaml_folder: tuple[Path, Path, Path]) -> None:
    folder, yaml_path, _ = yaml_folder
    _write_yaml(yaml_path, ["Hi from Zurich", "first.png", "Greetings", "second.png"])
    plugin = load_plugin(
        "folder_yaml",
        {"folder": str(folder), "yaml": str(yaml_path)},
        registry=None,
    )
    result = plugin.render()
    assert isinstance(result, PluginResult)
    assert result.message == "Hi from Zurich"
    # The PNG bytes for the red "first.png" should be in the result.
    picked = Image.open(result.image)
    assert picked.getpixel((0, 0)) == (255, 0, 0)


def test_folder_yaml_remove_yaml_truncates_document(yaml_folder: tuple[Path, Path, Path]) -> None:
    folder, yaml_path, _ = yaml_folder
    _write_yaml(yaml_path, ["first text", "first.png", "second text", "second.png"])
    plugin = load_plugin(
        "folder_yaml",
        {"folder": str(folder), "yaml": str(yaml_path)},
        registry=None,
    )
    plugin.render()

    # The yaml file should now contain only the second pair.
    remaining = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert remaining == ["second text", "second.png"]


def test_folder_yaml_remove_yaml_false_keeps_document(yaml_folder: tuple[Path, Path, Path]) -> None:
    folder, yaml_path, _ = yaml_folder
    _write_yaml(yaml_path, ["first text", "first.png", "second text", "second.png"])
    plugin = load_plugin(
        "folder_yaml",
        {
            "folder": str(folder),
            "yaml": str(yaml_path),
            "remove_yaml": False,
        },
        registry=None,
    )
    plugin.render()

    # The yaml file should still contain the original document.
    remaining = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert remaining == ["first text", "first.png", "second text", "second.png"]


def test_folder_yaml_move_relocates_picture(yaml_folder: tuple[Path, Path, Path]) -> None:
    folder, yaml_path, _ = yaml_folder
    first_bytes = _make_picture_bytes("red")
    _write_yaml(yaml_path, ["text", "first.png"])
    plugin = load_plugin(
        "folder_yaml",
        {"folder": str(folder), "yaml": str(yaml_path), "move": True},
        registry=None,
    )
    plugin.render()
    sent_dir = folder / "sent"
    assert sent_dir.is_dir()
    assert (sent_dir / "first.png").read_bytes() == first_bytes
    assert not (folder / "first.png").exists()


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_folder_yaml_missing_folder_raises(tmp_path: Path) -> None:
    yaml_path = tmp_path / "y.yaml"
    _write_yaml(yaml_path, ["text", "img.png"])
    plugin = load_plugin(
        "folder_yaml",
        {"folder": str(tmp_path / "nope"), "yaml": str(yaml_path)},
        registry=None,
    )
    with pytest.raises(PluginRenderError, match="picture directory"):
        plugin.render()


def test_folder_yaml_missing_yaml_file_raises(tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    plugin = load_plugin(
        "folder_yaml",
        {"folder": str(folder), "yaml": str(tmp_path / "nope.yaml")},
        registry=None,
    )
    with pytest.raises(PluginRenderError, match="yaml file"):
        plugin.render()


def test_folder_yaml_empty_document_raises(yaml_folder: tuple[Path, Path, Path]) -> None:
    folder, yaml_path, _ = yaml_folder
    _write_yaml(yaml_path, [])
    plugin = load_plugin(
        "folder_yaml",
        {"folder": str(folder), "yaml": str(yaml_path)},
        registry=None,
    )
    with pytest.raises(PluginRenderError, match="nothing left to do"):
        plugin.render()


def test_folder_yaml_uneven_document_raises(yaml_folder: tuple[Path, Path, Path]) -> None:
    folder, yaml_path, _ = yaml_folder
    _write_yaml(yaml_path, ["text", "first.png", "orphan text"])  # odd count
    plugin = load_plugin(
        "folder_yaml",
        {"folder": str(folder), "yaml": str(yaml_path)},
        registry=None,
    )
    with pytest.raises(PluginRenderError, match="even number"):
        plugin.render()


def test_folder_yaml_missing_image_raises(yaml_folder: tuple[Path, Path, Path]) -> None:
    folder, yaml_path, _ = yaml_folder
    _write_yaml(yaml_path, ["text", "nonexistent.png"])
    plugin = load_plugin(
        "folder_yaml",
        {"folder": str(folder), "yaml": str(yaml_path)},
        registry=None,
    )
    with pytest.raises(PluginRenderError, match="does not exist"):
        plugin.render()


def test_folder_yaml_rejects_non_list_document(yaml_folder: tuple[Path, Path, Path]) -> None:
    folder, yaml_path, _ = yaml_folder
    yaml_path.write_text("not: a list\n", encoding="utf-8")
    plugin = load_plugin(
        "folder_yaml",
        {"folder": str(folder), "yaml": str(yaml_path)},
        registry=None,
    )
    with pytest.raises(PluginRenderError, match="must be a list"):
        plugin.render()


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


def test_folder_yaml_class_metadata() -> None:
    assert FolderYamlPlugin.name == "folder_yaml"
    assert "yaml" in FolderYamlPlugin.description.lower()


def test_folder_yaml_is_registered_in_default_registry() -> None:
    from postcards.plugins.registry import Registry

    assert Registry.default.has("folder_yaml")
    assert Registry.default.get("folder_yaml") is FolderYamlPlugin
