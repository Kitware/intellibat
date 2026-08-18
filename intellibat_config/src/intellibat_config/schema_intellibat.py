from datetime import time
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RecordingFormat(StrEnum):
    FULL_SPECTRUM = 'full_spectrum'
    ZERO_CROSSING = 'zero_crossing'


class ScheduleMode(StrEnum):
    SUNSET_TO_SUNRISE = 'sunset_to_sunrise'
    SUNSET_MINUS_30_TO_SUNRISE_PLUS_30 = 'sunset_minus_30_to_sunrise_plus_30'
    CUSTOM = 'custom'


class IntellibatConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    recording_format: RecordingFormat = RecordingFormat.FULL_SPECTRUM

    sample_rate: Literal[256000, 384000, 500000]

    triggered_recording: bool

    minimum_trigger_frequency: int = Field(
        ge=6,
        le=60,
        description='Trigger frequency in kHz',
    )

    maximum_recording_length: int = Field(
        ge=3,
        le=60,
        description='Maximum recording length in seconds',
    )

    trigger_window: int = Field(
        ge=1,
        le=15,
        description='Trigger window in seconds',
    )

    save_noise_files: bool

    latitude: float = Field(
        ge=-90,
        le=90,
    )

    longitude: float = Field(
        ge=-180,
        le=180,
    )

    schedule_mode: ScheduleMode = ScheduleMode.CUSTOM

    start_time: time

    end_time: time
