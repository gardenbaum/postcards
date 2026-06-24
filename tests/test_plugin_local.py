"""Tests for the ``local`` M3 plugin.

The local plugin picks a picture from a folder deterministically
(round-robin through a sortable, sorted candidate list). Tests
use temporary directories populated with synthetic PNG/JPEG
files so no fixture files are needed on disk.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from postcards.plugins import PluginResult
from postcards.plugins.builtin.local import LocalPlugin
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
    """A folder with three distinguishable PNGs in stable sort order."""
    folder = tmp_path / "pics"
    folder.mkdir()
    _make_picture(folder / "a.png", "red")
    _make_picture(folder / "b.png", "green")
    _make_picture(folder / "c.png", "blue")
    return folder


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_local_plugin_requires_folder_in_payload() -> None:
    with pytest.raises(PluginConfigError, match="folder"):
        load_plugin("local", {}, registry=None)


def test_local_plugin_rejects_non_string_folder() -> None:
    with pytest.raises(PluginConfigError):
        load_plugin("local", {"folder": 7}, registry=None)


def test_local_plugin_rejects_non_string_pattern() -> None:
    with pytest.raises(PluginConfigError, match="pattern"):
        load_plugin("local", {"folder": "/tmp", "pattern": 7}, registry=None)


def test_local_plugin_rejects_non_string_message() -> None:
    with pytest.raises(PluginConfigError, match="message"):
        load_plugin("local", {"folder": "/tmp", "message": 7}, registry=None)


def test_local_plugin_rejects_non_string_cursor_file() -> None:
    with pytest.raises(PluginConfigError, match="cursor_file"):
        load_plugin("local", {"folder": "/tmp", "cursor_file": 7}, registry=None)


# ---------------------------------------------------------------------------
# Happy path — deterministic ordering
# ---------------------------------------------------------------------------


def test_local_plugin_picks_first_match_deterministically(picture_dir: Path) -> None:
    """``local`` always picks the alphabetically first file on first run."""
    plugin = load_plugin("local", {"folder": str(picture_dir)}, registry=None)
    result = plugin.render()
    picked = Image.open(result.image)
    # a.png is the first by sort order; red is its color.
    assert picked.getpixel((0, 0)) == (255, 0, 0)


def test_local_plugin_message_is_none_by_default(picture_dir: Path) -> None:
    plugin = load_plugin("local", {"folder": str(picture_dir)}, registry=None)
    result = plugin.render()
    assert result.message is None


def test_local_plugin_forwards_configured_message(picture_dir: Path) -> None:
    plugin = load_plugin(
        "local",
        {"folder": str(picture_dir), "message": "postcard from lucerne"},
        registry=None,
    )
    result = plugin.render()
    assert result.message == "postcard from lucerne"


def _pixel(rgb: object) -> tuple[int, int, int]:
    """Coerce the wide ``Image.getpixel`` return type to a 3-tuple."""
    assert isinstance(rgb, tuple) and len(rgb) == 3
    r, g, b = rgb
    assert isinstance(r, int) and isinstance(g, int) and isinstance(b, int)
    return (r, g, b)


def _close_to(actual: tuple[int, int, int], expected: tuple[int, int, int]) -> bool:
    """JPEG is lossy; colours may drift by a few units per channel."""
    return all(abs(a - e) <= 2 for a, e in zip(actual, expected, strict=True))


def test_local_plugin_round_robin_advances_without_cursor_file(
    picture_dir: Path,
) -> None:
    """Without a cursor_file, the cursor is in-memory and resets per process."""
    plugin = load_plugin("local", {"folder": str(picture_dir)}, registry=None)
    # Two renders, same instance → in-memory cursor advances.
    first = plugin.render()
    second = plugin.render()
    first_color = _pixel(Image.open(io.BytesIO(first.image.read())).getpixel((0, 0)))
    second_color = _pixel(Image.open(io.BytesIO(second.image.read())).getpixel((0, 0)))
    assert first_color == (255, 0, 0)  # a.png
    assert second_color == (0, 128, 0)  # b.png


def test_local_plugin_wraps_around_after_last_picture(picture_dir: Path) -> None:
    """After enough renders, the cursor wraps back to 0."""
    plugin = load_plugin("local", {"folder": str(picture_dir)}, registry=None)
    colors: list[tuple[int, int, int]] = []
    for _ in range(4):  # 3 files + 1 wrap
        result = plugin.render()
        colors.append(_pixel(Image.open(io.BytesIO(result.image.read())).getpixel((0, 0))))
    assert colors == [
        (255, 0, 0),  # a.png
        (0, 128, 0),  # b.png
        (0, 0, 255),  # c.png
        (255, 0, 0),  # wraps to a.png
    ]


# ---------------------------------------------------------------------------
# Cursor persistence (cron use case)
# ---------------------------------------------------------------------------


def test_local_plugin_persists_cursor_across_instances(picture_dir: Path, tmp_path: Path) -> None:
    """A cursor_file lets a cron job pick up where the previous run left off."""
    cursor = tmp_path / "state.json"
    first = load_plugin(
        "local", {"folder": str(picture_dir), "cursor_file": str(cursor)}, registry=None
    )
    first.render()  # consumes index 0 (a.png), advances to 1

    # Brand-new plugin instance — simulates a fresh process invocation.
    second = load_plugin(
        "local", {"folder": str(picture_dir), "cursor_file": str(cursor)}, registry=None
    )
    result = second.render()
    color = Image.open(io.BytesIO(result.image.read())).getpixel((0, 0))
    assert color == (0, 128, 0)  # b.png
    # Cursor file holds the advanced index for the *next* call.
    assert json.loads(cursor.read_text())["next_index"] == 2


def test_local_plugin_creates_cursor_directory(tmp_path: Path) -> None:
    """The plugin creates the parent directory of the cursor file."""
    folder = tmp_path / "pics"
    folder.mkdir()
    _make_picture(folder / "only.png", "red")
    cursor = tmp_path / "deep" / "nested" / "state.json"
    plugin = load_plugin(
        "local", {"folder": str(folder), "cursor_file": str(cursor)}, registry=None
    )
    plugin.render()
    assert cursor.is_file()


def test_local_plugin_handles_corrupt_cursor_gracefully(picture_dir: Path, tmp_path: Path) -> None:
    """A malformed cursor file falls back to index 0."""
    cursor = tmp_path / "state.json"
    cursor.write_text("this is not json")
    plugin = load_plugin(
        "local", {"folder": str(picture_dir), "cursor_file": str(cursor)}, registry=None
    )
    result = plugin.render()
    color = Image.open(io.BytesIO(result.image.read())).getpixel((0, 0))
    assert color == (255, 0, 0)


# ---------------------------------------------------------------------------
# Pattern + extension filtering
# ---------------------------------------------------------------------------


def test_local_plugin_filters_by_glob_pattern(tmp_path: Path) -> None:
    """``pattern`` narrows the candidate set."""
    folder = tmp_path / "pics"
    folder.mkdir()
    _make_picture(folder / "alpha.jpg", "red", fmt="JPEG")
    _make_picture(folder / "beta.png", "green", fmt="PNG")
    plugin = load_plugin("local", {"folder": str(folder), "pattern": "*.jpg"}, registry=None)
    result = plugin.render()
    head = result.image.read(3)
    assert head == b"\xff\xd8\xff"


def test_local_plugin_pattern_can_match_subsets(tmp_path: Path) -> None:
    """The pattern is applied before the extension filter."""
    folder = tmp_path / "pics"
    folder.mkdir()
    _make_picture(folder / "landscape.jpg", "red", fmt="JPEG")
    _make_picture(folder / "portrait.jpg", "green", fmt="JPEG")
    plugin = load_plugin("local", {"folder": str(folder), "pattern": "landscape*"}, registry=None)
    result = plugin.render()
    color = _pixel(Image.open(io.BytesIO(result.image.read())).getpixel((0, 0)))
    assert _close_to(color, (255, 0, 0))


def test_local_plugin_supports_jpg_and_png(tmp_path: Path) -> None:
    """Same extension set as ``folder`` plugin."""
    folder = tmp_path / "pics"
    folder.mkdir()
    _make_picture(folder / "alpha.jpg", "red", fmt="JPEG")
    _make_picture(folder / "beta.png", "green", fmt="PNG")
    _make_picture(folder / "gamma.JPEG", "blue", fmt="JPEG")
    plugin = load_plugin("local", {"folder": str(folder)}, registry=None)
    # Render three times and collect colors — order is alphabetical.
    colors: list[tuple[int, int, int]] = []
    for _ in range(3):
        result = plugin.render()
        colors.append(_pixel(Image.open(io.BytesIO(result.image.read())).getpixel((0, 0))))
    assert _close_to(colors[0], (255, 0, 0))  # alpha.jpg → red (lossy JPEG)
    assert colors[1] == (0, 128, 0)  # beta.png → green (lossless PNG)
    assert _close_to(colors[2], (0, 0, 255))  # gamma.JPEG → blue (lossy JPEG)


def test_local_plugin_ignores_non_picture_files(tmp_path: Path) -> None:
    """README.md and similar non-pictures are filtered out by the extension set."""
    folder = tmp_path / "pics"
    folder.mkdir()
    (folder / "README.md").write_text("not a picture")
    (folder / "data.json").write_text("{}")
    _make_picture(folder / "real.png", "red")
    plugin = load_plugin("local", {"folder": str(folder)}, registry=None)
    result = plugin.render()
    color = Image.open(io.BytesIO(result.image.read())).getpixel((0, 0))
    assert color == (255, 0, 0)


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_local_plugin_nonexistent_folder_raises_render_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    plugin = load_plugin("local", {"folder": str(missing)}, registry=None)
    with pytest.raises(PluginRenderError):
        plugin.render()


def test_local_plugin_no_matches_raises_render_error(tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    (folder / "README.md").write_text("not a picture")
    plugin = load_plugin("local", {"folder": str(folder)}, registry=None)
    with pytest.raises(PluginRenderError, match="no pictures"):
        plugin.render()


def test_local_plugin_no_matches_with_pattern_raises_render_error(tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    _make_picture(folder / "real.jpg", "red", fmt="JPEG")
    plugin = load_plugin("local", {"folder": str(folder), "pattern": "*.png"}, registry=None)
    with pytest.raises(PluginRenderError, match=r"pattern"):
        plugin.render()


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


def test_local_plugin_class_metadata() -> None:
    assert LocalPlugin.name == "local"
    assert (
        "local" in LocalPlugin.description.lower()
        or "round-robin" in LocalPlugin.description.lower()
    )


def test_local_plugin_is_registered_in_default_registry() -> None:
    from postcards.plugins.registry import Registry

    assert Registry.default.has("local")
    assert Registry.default.get("local") is LocalPlugin


def test_local_plugin_result_is_pluginresult(picture_dir: Path) -> None:
    plugin = load_plugin("local", {"folder": str(picture_dir)}, registry=None)
    result = plugin.render()
    assert isinstance(result, PluginResult)


def test_local_plugin_image_is_bytesio(picture_dir: Path) -> None:
    plugin = load_plugin("local", {"folder": str(picture_dir)}, registry=None)
    result = plugin.render()
    assert isinstance(result.image, io.BytesIO)
