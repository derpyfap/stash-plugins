from stashapi.stashapp import StashInterface
from stashapi import log
import pathlib
import re


MOVE_FILE_MUTATION = """
mutation MoveFiles($input: MoveFilesInput!) {
    moveFiles(input: $input)
}
"""


def get_parent_studio_chain(stash, scene):
    current_studio = scene.get("studio", {})
    parent_chain = [current_studio.get("name", "")]

    while current_studio.get("parent_studio"):
        current_studio = stash.find_studio(current_studio.get("parent_studio"))

        parent_chain.append(current_studio.get("name"))

    return "/".join(reversed(parent_chain))

def key_getter(key):
    return lambda _, data: data.get(key, "")

FILE_VARIABLES = {
    "audio_codec": key_getter("audio_codec"),
    "ext": lambda _, file: file.get("basename", "").split(".")[-1],
    "format": key_getter("format"),
    "height": key_getter("height"),
    "index": key_getter("index"),
    "video_codec": key_getter("video_codec"),
    "width": key_getter("width"),
}

SCENE_VARIABLES = {
    "scene_id": key_getter("id"),
    "title": key_getter("title"),
    "date": key_getter("date"),
    "director": key_getter("director"),
    "month": lambda _, scene: scene.get("date", "").split("-")[1] if scene.get("date") else "",
    "parent_studio_chain": get_parent_studio_chain,
    "studio_code": key_getter("code"),
    "stashdb_id": lambda _, scene: next(
        (s["stash_id"] for s in scene.get("stash_ids", [])
         if s.get("endpoint") == "https://stashdb.org/graphql"),
        ""
    ),
    "studio_name": lambda _, scene: scene.get("studio", {}).get("name", ""),
    "year": lambda _, scene: scene.get("date", "").split("-")[0] if scene.get("date") else "",
}

def find_variables(format_template) -> list[str]:
    variables = []

    for variable in FILE_VARIABLES.keys():
        if f"${variable}$" in format_template:
            variables.append(variable)

    for variable in SCENE_VARIABLES.keys():
        if f"${variable}$" in format_template:
            variables.append(variable)

    return variables


def clean_optional_from_format(formatted_string: str) -> str:
    # Erase entire optional section if there is an unused variable.
    # [$] is used instead of \$ because \$ in Python re is a zero-width
    # end-of-line anchor, not a literal dollar sign match.
    # [^{}]* prevents greedy matching from consuming across multiple blocks.
    formatted_string = re.sub(r'\{[^{}]*[$]\w+[$][^{}]*\}', '', formatted_string)

    # Remove any remaining curly braces
    formatted_string = formatted_string.replace("{", "").replace("}", "")

    return formatted_string


def apply_format(format_template: str, stash: StashInterface, scene_data, file_data)-> str:
    variables = find_variables(format_template)

    formatted_template = format_template

    for variable in variables:
        if variable in FILE_VARIABLES:
            value = FILE_VARIABLES[variable](stash, file_data)
        elif variable in SCENE_VARIABLES:
            value = SCENE_VARIABLES[variable](stash, scene_data)

        if not value:
            continue

        formatted_template = formatted_template.replace(f"${variable}$", str(value))

    formatted_template = clean_optional_from_format(formatted_template)

    return formatted_template


class StashFile:
    def __init__(self, stash: StashInterface, config, scene_data, file_data):
        self.stash = stash
        self.config = config
        self.scene_data = scene_data
        self.file_data = file_data
        self.duplicate_index = 0

    def get_old_file_path(self) -> pathlib.Path:
        path = pathlib.Path(self.file_data["path"])

        return path.absolute()

    def get_new_file_folder(self) -> pathlib.Path:
        if self.config.default_directory_path_format:
            directory_path = apply_format(self.config.default_directory_path_format, self.stash, self.scene_data, self.file_data)
            directory_path = pathlib.Path(directory_path).absolute()
        else:
            path = pathlib.Path(self.file_data["path"])
            directory_path = path.parent.absolute()

        return directory_path
    
    def get_new_file_name(self) -> str:
        if not self.config.default_file_name_format:
            return self.file_data["basename"]

        file_data = {**self.file_data, "index": self.duplicate_index}
        file_name = apply_format(self.config.default_file_name_format, self.stash, self.scene_data, file_data)

        if self.duplicate_index:
            duplicate_suffix = apply_format(self.config.duplicate_file_suffix, self.stash, self.scene_data, file_data)
            parts = file_name.rsplit(".", 1)
            base_name = parts[0]
            extension = parts[1] if len(parts) > 1 else ""
            suffix_dot = f".{extension}" if extension else ""

            file_name = f"{base_name}{duplicate_suffix}{suffix_dot}"

        if not self.config.allow_unsafe_characters:
            file_name = re.sub(r"[<>:\"/\\|?*]", "", file_name)

        if self.config.remove_extra_spaces_from_file_name:
            file_name = re.sub(r"\s+", " ", file_name)

        file_name = self._truncate_filename(file_name)

        return file_name

    @staticmethod
    def _truncate_filename(file_name: str, max_bytes: int = 255) -> str:
        """
        Truncate a filename to fit within the OS byte limit (255 bytes on Linux).
        Preserves the file extension and any trailing bracketed suffix (e.g. a
        StashDB UUID like '[f53175c6-...deb]') by trimming characters from the
        portion of the stem that precedes the suffix.
        """
        if len(file_name.encode("utf-8")) <= max_bytes:
            return file_name

        parts = file_name.rsplit(".", 1)
        stem = parts[0]
        extension = f".{parts[1]}" if len(parts) > 1 else ""

        # Detect a trailing bracketed suffix, e.g. ' [uuid]'
        bracket_match = re.search(r"(\s*\[[^\]]+\])$", stem)
        if bracket_match:
            suffix = bracket_match.group(1)
            prefix = stem[: bracket_match.start()]
        else:
            suffix = ""
            prefix = stem

        # Calculate how many bytes are available for the prefix
        extension_bytes = len(extension.encode("utf-8"))
        suffix_bytes = len(suffix.encode("utf-8"))
        max_prefix_bytes = max_bytes - extension_bytes - suffix_bytes

        if max_prefix_bytes <= 0:
            # Edge case: suffix + extension alone exceed the limit — just hard-truncate
            combined = (suffix + extension).encode("utf-8")[:max_bytes]
            result = combined.decode("utf-8", errors="ignore")
            log.warning(f"Filename too long even after dropping title, hard-truncated: {result}")
            return result

        prefix_encoded = prefix.encode("utf-8")
        if len(prefix_encoded) > max_prefix_bytes:
            truncated = prefix_encoded[:max_prefix_bytes]
            prefix = truncated.decode("utf-8", errors="ignore").rstrip()
            log.warning(f"Filename too long, truncated title portion to fit OS 255-byte limit: {prefix}{suffix}{extension}")

        return f"{prefix}{suffix}{extension}"

    def get_new_file_path(self) -> pathlib.Path:
        return self.get_new_file_folder() / self.get_new_file_name()

    def is_in_ignored_folder(self, path: pathlib.Path) -> bool:
        for folder in self.config.ignored_folders:
            ignored = pathlib.Path(folder).absolute()
            try:
                path.relative_to(ignored)
                return True
            except ValueError:
                continue
        return False

    def is_in_stashdb_folder(self, path: pathlib.Path) -> bool:
        """
        Returns True if the file's immediate parent folder name contains
        [stashdb_uuid] matching this scene's StashDB ID. This indicates
        the file has already been imported by Whisparr into its managed
        folder structure — the plugin should leave it alone.
        """
        stashdb_id = next(
            (s["stash_id"] for s in self.scene_data.get("stash_ids", [])
             if s.get("endpoint") == "https://stashdb.org/graphql"),
            None
        )
        if not stashdb_id:
            return False

        folder_name = path.parent.name
        return f"[{stashdb_id}]" in folder_name

    def rename_related_files(self, old_path: pathlib.Path, new_path: pathlib.Path, dry_run: bool):
        if not self.config.rename_related_files:
            return

        old_directory = old_path.parent
        new_directory = new_path.parent
        related_files = [
            path
            for path in old_directory.glob(f"{old_path.stem}.*")
            if path != old_path
        ]

        if not related_files:
            return

        for related_file in related_files:
            related_name = self._truncate_filename(f"{new_path.stem}{related_file.suffix}")
            target_path = new_directory / related_name

            if related_file == target_path:
                continue

            try:
                path_exists = target_path.exists()
            except OSError as e:
                log.error(f"Could not check if related file target exists (path too long?): {target_path}: {e}")
                continue

            if path_exists:
                log.warning(f"Related file already exists at {target_path}, skipping rename for {related_file}")
                continue

            log.info(f"Renaming related file from {related_file} to {target_path}")

            if dry_run:
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                related_file.rename(target_path)
            except OSError as error:
                log.error(f"Failed to rename related file from {related_file} to {target_path}: {error}")

    def rename_file(self):
        old_path = self.get_old_file_path()

        if not old_path.exists():
            log.warning(f"File for scene does not exist on disk: {old_path}")
            return

        # Never touch files inside ignored folders
        if self.is_in_ignored_folder(old_path):
            log.info(f"File is in an ignored folder, skipping: {old_path}")
            return

        # File is already in a Whisparr-managed folder containing the StashDB UUID —
        # Whisparr owns this file, do not rename or move it
        if self.is_in_stashdb_folder(old_path):
            log.info(f"File is already in a StashDB-matched folder, skipping: {old_path}")
            return

        # Only proceed if the basename is actually changing — no rename means no move
        new_file_name = self.get_new_file_name()
        if new_file_name == self.file_data["basename"]:
            log.info(f"File name is already correct, skipping: {old_path}")
            return

        new_path = self.get_new_file_path()

        if old_path == new_path:
            log.info("File paths are the same, no renaming needed.")
            return

        log.debug(f"Checking if a file exists at {new_path}")
        while new_path.exists():
            self.duplicate_index += 1
            log.warning(f"File already exists at {new_path}, adding duplicate suffix: {self.duplicate_index}")
            new_path = self.get_new_file_path()

            if old_path == new_path:
                log.info("File paths are the same after adding duplicate suffix, no renaming needed.")
                return

        log.info(f"Renaming file from {old_path} to {new_path}")
        if self.config.dry_run:
            log.info("Dry run enabled, not actually renaming the file.")
            self.rename_related_files(old_path, new_path, dry_run=True)
            return

        moved_file = self.stash.call_GQL(
            MOVE_FILE_MUTATION,
            {"input": {
                    "ids": [self.file_data["id"]],
                    "destination_folder": str(self.get_new_file_folder()),
                    "destination_basename": self.get_new_file_name(),
                }
            }
        )

        log.info(f"File renamed successfully: {moved_file}")
        self.rename_related_files(old_path, new_path, dry_run=False)
