class Config:
    DEFAULT_CONFIG = {
        "defaultDirectoryPathFormat": "",
        "defaultFileNameFormat": "",
        "dryRun": False,
        "renameUnorganized": False,
        "renameRelatedFiles": True,
        "removeExtraSpacesFromFileName": False,
        "allowUnsafeCharacters": False,
        "duplicateFileSuffix": " ($index$)",
        "excludedFolders": "",
    }

    def __init__(self, config):
        self.config = config

    def __getattr__(self, name):
        config_name = self.__to_camel_case(name)

        stash_config = self.config.get(config_name)

        if stash_config is not None:
            return stash_config

        return Config.DEFAULT_CONFIG.get(config_name)

    @property
    def excluded_folders(self) -> list[str]:
        raw = self.config.get("excludedFolders", "") or ""
        return [f.strip().rstrip("/") for f in raw.split(",") if f.strip()]

    @staticmethod
    def __to_camel_case(snake_str):
        pascal_case = "".join(x.capitalize() for x in snake_str.lower().split("_"))
        return pascal_case[0].lower() + pascal_case[1:]
