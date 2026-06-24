#!/usr/bin/env python

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import yaml

from postcards.plugin_folder.postcards_folder import PostcardsFolder


class PostcardsFolderYaml(PostcardsFolder):
    """
    Send postcards with images from a yaml config
    """

    def can_handle_command(self, command: str) -> bool:
        return command == "validate"

    def handle_command(self, command: str, args: argparse.Namespace) -> None:
        if command == "validate":
            config = self._read_json_file(args.config_file[0], "config")
            payload = config.get("payload")
            if not payload:
                self.logger.warning("error: config file does not contain payload")
                sys.exit(1)

            folder_path, yaml_path = self._validate_cli(payload, args)
            doc = self.validate_and_parse_yaml(folder_path, yaml_path)
            for d in doc:
                self.logger.info(f"> entry: {d}")
            self.logger.info("validation is successful")

    def build_plugin_subparser(self, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser(
            "validate",
            help="validate yaml file",
            description="check that yaml file contains the proper format and that all pictures referenced exist.",
        )
        parser.add_argument(
            "-c",
            "--config",
            nargs=1,
            required=True,
            type=str,
            help="location to the configuration file (default: ./config.json)",
            default=[os.path.join(os.getcwd(), "config.json")],
            dest="config_file",
        )

    def get_img_and_text(self, payload: dict, cli_args: argparse.Namespace) -> dict:
        folder_path, yaml_path = self._validate_cli(payload, cli_args)
        document = self.validate_and_parse_yaml(folder_path, yaml_path)

        if len(document) == 0:
            self.logger.warning("nothing left to do, no more pictures in yaml file left.")
            sys.exit(1)

        remove_yaml = payload.get("remove_yaml")
        if remove_yaml in (True, None):
            text = document.pop(0)
            img_name = document.pop(0)
        else:
            self.logger.info("remove_yaml = False, do not remove entries form yaml")
            text = document[0]
            img_name = document[1]

        img_path = os.path.join(folder_path, img_name)
        self._write_back_yaml(document, yaml_path)

        move_info = "moving to sent directory" if payload.get("move") else "no move"
        self.logger.info(f"choosing image '{img_path}' ({move_info})")
        self.logger.info(f"choosing text '{text}'")

        file = open(img_path, "rb")  # noqa: SIM115 - file handle returned to caller
        if payload.get("move"):
            self._move_to_sent(folder_path, img_path)

        return {"img": file, "text": text}

    def validate_and_parse_yaml(self, folder_path: str, yaml_path: str) -> list[Any]:
        """
        both paths are absolute
        :return: a flat list whose entries alternate (text, image_path)
        """

        data = ""
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = f.read()
        except OSError:
            self.logger.error(f"error: can not read yaml file {yaml_path}")
            sys.exit(1)

        document: list[Any]
        self.logger.info(f"reading yaml file at {yaml_path}")
        try:
            document = yaml.load(data, Loader=yaml.FullLoader)
        except yaml.YAMLError:
            self.logger.error(f"error: can not parse yaml file {yaml_path}")
            sys.exit(2)

        if len(document) % 2 != 0:
            self.logger.error("error: uneven number of entries in yaml file.")
            sys.exit(3)

        i = 1
        while i < len(document):
            img_path = document[i]
            img_abs_path = os.path.join(folder_path, img_path)

            if not os.path.isfile(img_abs_path):
                self.logger.error(
                    f"error: path entry {i}: '{img_abs_path}' in yaml file does not exist on disk.."
                )
                sys.exit(4)

            i += 2

        return document

    def _validate_cli(self, payload: dict, cli_args: argparse.Namespace) -> tuple[str, str]:
        if not payload.get("folder"):
            self.logger.error("no folder set in configuration")
            sys.exit(1)

        folder_location = self._make_absolute_path(str(payload.get("folder")))
        if not os.path.isdir(folder_location):
            self.logger.error(f"picture directory '{folder_location}' does not exist")
            sys.exit(1)

        if not payload.get("yaml"):
            self.logger.error("no yaml file set in configuration")
            sys.exit(1)

        yaml_location = self._make_absolute_path(str(payload.get("yaml")))
        if not os.path.isfile(yaml_location):
            self.logger.error(f"yaml file {yaml_location} does not exist")
            sys.exit(1)

        self.logger.debug("cli validation successful")
        return folder_location, yaml_location

    @staticmethod
    def _write_back_yaml(document: list[Any], location: str) -> None:
        dump = yaml.dump(document)
        with open(location, "w", encoding="utf-8") as file:
            file.write(dump)


def main() -> None:
    PostcardsFolderYaml().main(sys.argv[1:])


if __name__ == "__main__":
    main()
