from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    intellibat_config_path: str = f"{Path.cwd()}/config.json"


settings = Settings()
