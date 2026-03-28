class Config:
    DEFAULT_CONFIG = {
        "defaultDirectoryPathFormat": "",
        "defaultFileNameFormat": "",
        "dryRun": False,
        "renameUnorganized": False,
        "renameRelatedFiles": True,
        "removeExtraSpacesFromFileName": False,
        "duplicateFileSuffix": " ($index$)",
        "ignoredFolders": "",
    }

    def __init__(self, config):
        self.config = config

    @property
    def ignored_folders(self) -> list[str]:
        raw = self.config.get("ignoredFolders") or Config.DEFAULT_CONFIG["ignoredFolders"]
        if not raw:
            return []
        return [folder.strip() for folder in raw.split(",") if folder.strip()]

    def __getattr__(self, name):
        config_name = self.__to_camel_case(name)

        stash_config = self.config.get(config_name)

        if stash_config is not None:
            return stash_config

        return Config.DEFAULT_CONFIG.get(config_name)

    @staticmethod
    def __to_camel_case(snake_str):
        pascal_case = "".join(x.capitalize() for x in snake_str.lower().split("_"))
        return pascal_case[0].lower() + pascal_case[1:]
