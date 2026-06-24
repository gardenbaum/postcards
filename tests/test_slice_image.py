"""Tests for ``postcards.plugin_folder.slice_image``.

``slice_image`` slices a PIL image into a 2-D matrix of tile PIL
images (``make_tiles``) and writes them to disk (``store_tiles``).
Both helpers are pure functions over the PIL ``Image`` API and have
no I/O dependencies outside the directory passed in.

These tests use small in-memory PIL images (no fixture files on
disk) so they are fast and hermetic.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from postcards.plugin_folder.slice_image import make_tiles, store_tiles


def _new_image(width: int, height: int, color: str = "red") -> Image.Image:
    """Create an in-memory PIL image of the given size and color."""
    return Image.new("RGB", (width, height), color=color)


def test_make_tiles_returns_correct_matrix_dimensions() -> None:
    """``make_tiles`` returns a ``height_segments x width_segments`` matrix.

    Each tile is exactly ``(tile_width, tile_height)`` pixels; the
    right / bottom edge is dropped (matching the legacy
    ``math.floor`` behavior).
    """
    image = _new_image(400, 300)
    tiles = make_tiles(image, tile_width=100, tile_height=150)
    # 400 / 100 = 4, 300 / 150 = 2
    assert len(tiles) == 2
    assert all(len(row) == 4 for row in tiles)
    assert all(tile.size == (100, 150) for row in tiles for tile in row)


def test_make_tiles_drops_partial_edge_tile() -> None:
    """A residual edge that is smaller than ``tile_width`` is dropped."""
    image = _new_image(450, 320)  # 4.5 wide, 2.13 tall at 100x150
    tiles = make_tiles(image, tile_width=100, tile_height=150)
    assert len(tiles) == 2
    assert all(len(row) == 4 for row in tiles)


def test_make_tiles_image_smaller_than_tile() -> None:
    """An image smaller than the tile size yields a single zero-tile matrix."""
    image = _new_image(50, 50)
    tiles = make_tiles(image, tile_width=100, tile_height=100)
    # math.floor(50/100) = 0 in both dimensions -> empty matrix
    assert tiles == []


def test_make_tiles_preserves_color() -> None:
    """Each tile's pixels are sampled from the original image (not blanked)."""
    image = _new_image(200, 200, color="blue")
    tiles = make_tiles(image, tile_width=100, tile_height=100)
    for row in tiles:
        for tile in row:
            # Sample one pixel; should be the source color.
            assert tile.getpixel((0, 0)) == (0, 0, 255)


def test_store_tiles_writes_files_to_disk(tmp_path: Path) -> None:
    """``store_tiles`` writes one file per tile to ``directory``."""
    image = _new_image(200, 200)
    tiles = make_tiles(image, tile_width=100, tile_height=100)
    # 4 tiles total.
    directory = tmp_path / "out"
    store_tiles(tiles, str(directory))
    written = sorted(os.listdir(directory))
    assert len(written) == 4
    assert all(name.endswith(".jpg") for name in written)


def test_store_tiles_creates_missing_directory(tmp_path: Path) -> None:
    """``store_tiles`` creates ``directory`` if it does not exist."""
    image = _new_image(100, 100)
    tiles = make_tiles(image, tile_width=50, tile_height=50)
    target = tmp_path / "deeply" / "nested" / "dir"
    store_tiles(tiles, str(target))
    assert target.is_dir()
    assert len(os.listdir(target)) == 4


def test_store_tiles_with_custom_basename(tmp_path: Path) -> None:
    """Passing ``basename`` overrides the default timestamp-based name."""
    image = _new_image(100, 100)
    tiles = make_tiles(image, tile_width=50, tile_height=50)
    directory = tmp_path / "out"
    store_tiles(tiles, str(directory), basename="tile")
    written = sorted(os.listdir(directory))
    assert all(name.startswith("tile_") for name in written)
