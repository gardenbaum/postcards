#!/usr/bin/env python

from __future__ import annotations

import argparse
import ntpath
import os
import random
import sys
from time import gmtime, strftime
from typing import ClassVar

from PIL import Image

from postcards.plugin_folder.slice_image import make_tiles, store_tiles
from postcards.postcards import Postcards


class PostcardsFolder(Postcards):
    """
    Send postcards with images from a local folder
    """

    supported_ext: ClassVar[list[str]] = [".jpg", ".jpeg", ".png"]
    high_prio_folder: ClassVar[str] = ".priority"

    def can_handle_command(self, command: str) -> bool:
        return command == "slice"

    def handle_command(self, command: str, args: argparse.Namespace) -> None:
        if command == "slice":
            self.slice_image(
                source_image=self._make_absolute_path(args.picture),
                tile_width=args.width,
                tile_height=args.height,
            )

    def build_plugin_subparser(self, subparsers: argparse._SubParsersAction) -> None:
        parser_slice = subparsers.add_parser(
            "slice",
            help="slice an image into tiles",
            description=(
                "slice an image into tiles to create a poster. \n"
                "tiles need to be a multiple of 154x111 pixels in order not to be cropped."
            ),
        )
        parser_slice.add_argument("picture", type=str, help="path to a picture to slice into tiles")
        parser_slice.add_argument("width", type=int, help="tile width")
        parser_slice.add_argument("height", type=int, help="tile height")

    def get_img_and_text(self, payload: dict, cli_args: argparse.Namespace) -> dict:
        if not payload.get("folder"):
            self.logger.error("no folder set in configuration")
            sys.exit(1)

        folder = self._make_absolute_path(str(payload.get("folder")))
        img_path = self._pick_image(folder)

        if not img_path:
            self.logger.error("no images in folder " + folder)
            sys.exit(1)

        move_info = "moving to sent folder" if payload.get("move") else "no move"

        self.logger.info(f"choosing image {img_path} ({move_info})")
        file = open(img_path, "rb")  # noqa: SIM115 - file handle returned to caller

        if payload.get("move"):
            self._move_to_sent(folder, img_path)

        return {"img": file, "text": ""}

    def slice_image(self, source_image: str, tile_width: int, tile_height: int) -> None:
        if not os.path.isfile(source_image):
            self.logger.error(f"file {source_image} does not exist")
            sys.exit(1)

        with (
            open(source_image, "rb") as file,
            Image.open(file) as image,
        ):
            cwd = os.getcwd()
            basename = strftime("slice_%Y-%m-%d_%H-%M-%S", gmtime())
            directory = os.path.join(cwd, basename)

            self.logger.info(f"slicing picture {source_image} into tiles")
            tiles = make_tiles(image, tile_width=tile_width, tile_height=tile_height)
            store_tiles(tiles, directory)
            self.logger.info(f"picture sliced into {len(tiles)} tiles {directory}")

    def _pick_image(self, folder: str) -> str:
        candidates: list[str] = []
        high_prio = os.path.join(folder, self.high_prio_folder)
        if os.path.exists(high_prio):
            for file in os.listdir(high_prio):
                for ext in self.supported_ext:
                    if file.lower().endswith(ext):
                        candidates.append(os.path.join(self.high_prio_folder, file))

        if not candidates:
            for file in os.listdir(folder):
                for ext in self.supported_ext:
                    if file.lower().endswith(ext):
                        candidates.append(file)

        if not candidates:
            self.logger.error("no images in folder " + folder)
            sys.exit(1)

        img_name = random.choice(candidates)
        return os.path.join(folder, img_name)

    def _move_to_sent(self, picture_folder: str, image_path: str) -> None:
        sent_folder = os.path.join(picture_folder, "sent")
        if not os.path.exists(sent_folder):
            os.makedirs(sent_folder)
            self.logger.debug(f"creating folder {sent_folder}")

        img_name = self._get_filename(image_path)
        sent_img_path = os.path.join(sent_folder, img_name)
        os.rename(image_path, sent_img_path)
        self.logger.debug(f"moving image from {image_path} to {sent_img_path}")

    @staticmethod
    def _get_filename(path: str) -> str:
        head, tail = ntpath.split(path)
        return tail or ntpath.basename(head)

    @staticmethod
    def _make_absolute_path(path: str) -> str:
        if not os.path.isabs(path):
            return os.path.join(os.getcwd(), path)
        return path


def main() -> None:
    PostcardsFolder().main(sys.argv[1:])


if __name__ == "__main__":
    main()
