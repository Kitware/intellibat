from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    intellibat_config_path: str = f'{Path.cwd()}/config.json'
    intellibat_recordings_path: str = f'{Path.cwd()}/test_recordings'
    intellibat_spectrograms_path: str = f'{Path.cwd()}/output'
    intellibat_telemetry_path: str = f'{Path.cwd()}/runtime/status.json'
    intellibat_timezone: str = 'America/Los_Angeles'


settings = Settings()
