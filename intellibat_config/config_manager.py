import json
from pathlib import Path

from schema_intellibat import IntelliBatConfig


class ConfigManager:

    def __init__(self, config: IntelliBatConfig, path: Path):
        self._config = config
        self._path = path

    @classmethod
    def from_file(cls, file: Path):
        if file.exists():
            with open(file, 'r') as f:
                config_data = json.load(f)
            config = IntelliBatConfig.model_validate(config_data)
        else:
            config = IntelliBatConfig()
        return cls(config, file)

    @property
    def config(self):
        return self._config

    def update(self, data: dict) -> IntelliBatConfig:
        updated = self._config.model_copy(update=data)
        validated = IntelliBatConfig.model_validate(updated)

        self._config = validated
        self._write()
        return self._config

    def _write(self):
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(self._config.model_dump_json(indent=2))
        tmp.replace(self._path)
