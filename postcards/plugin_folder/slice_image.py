import logging
import math
import os
import sys
from time import gmtime, strftime

from PIL import Image

"""slice_image.py: Slice image into tiles."""

__author__ = "Andrin Bertschi. www.abertschi.ch"

LOGGER_NAME = "slice_image"
logger = logging.getLogger(LOGGER_NAME)


def make_tiles(image: Image.Image, tile_width: int, tile_height: int) -> list[list[Image.Image]]:
    """
    slice PIL image to tiles

    :param image: PIL image
    :param tile_width: target tile width
    :param tile_height: target tile height
    :return: 2d array of PIL images
    """
    width_segments = math.floor(image.width / tile_width)
    height_segments = math.floor(image.height / tile_height)

    matrix: list[list[Image.Image]] = [
        [Image.new("RGB", (tile_width, tile_height)) for _ in range(width_segments)]
        for _ in range(height_segments)
    ]

    for h in range(height_segments):
        y_from = h * tile_height
        y_to = y_from + tile_height

        for w in range(width_segments):
            x_from = w * tile_width
            x_to = x_from + tile_width

            frame = image.crop((x_from, y_from, x_to, y_to))
            matrix[h][w] = frame
    return matrix


def store_tiles(
    tiles: list[list[Image.Image]],
    directory: str,
    basename: str | None = None,
) -> None:
    """
    Store generated tiles to disk

    :param tiles: a 2d array of PIL images, as created by #make_tiles function
    :param directory: directory to store images
    :param basename: basename of image, if none is set, default name is chosen
    :return: nothing
    """
    if not basename:
        basename = strftime("sliced_%Y-%m-%d_%H-%M-%S", gmtime())

    if not os.path.exists(directory):
        os.makedirs(directory)
        logger.debug(f"creating {directory}")

    height = len(tiles)
    width = len(tiles[0])
    for h in range(height):
        for w in range(width):
            frame = tiles[h][w]
            filename = basename + f"_{h}-{w}.jpg"
            filepath = os.path.join(directory, filename)
            logger.debug(f"storing {filepath}")
            frame.save(filepath)


def _make_absolute_path(path):
    if os.path.isabs(path):
        return path
    else:
        return str(os.path.join(os.getcwd(), path))


def _from_cli(image_path: str, tile_width: int, tile_height: int) -> None:
    if not os.path.isfile(image_path):
        logger.error(f"file {image_path} does not exist")
        sys.exit(1)

    with open(image_path, "rb") as file, Image.open(file) as image:
        cwd = os.getcwd()
        basename = strftime("sliced_%Y-%m-%d_%H-%M-%S", gmtime())
        directory = os.path.join(cwd, basename)

        tiles = make_tiles(image, tile_width=tile_width, tile_height=tile_height)
        store_tiles(tiles, directory)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s (%(levelname)s): %(message)s")
    logging.getLogger(LOGGER_NAME).setLevel(logging.DEBUG)

    if len(sys.argv) < 4:
        logger.error(
            f"wrong usage. call script python {sys.argv[0]} <image_path> <tile_width> <tile_height>"
        )
        exit(1)

    image_path = _make_absolute_path(sys.argv[1])
    tile_height = int(sys.argv[3])
    tile_width = int(sys.argv[2])
    _from_cli(image_path, tile_width, tile_height)
