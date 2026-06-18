from pathlib import Path

from config_manager import ConfigManager
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from settings import settings

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIR)

config_manager = ConfigManager.from_file(Path(settings.intellibat_config_path))

@router.get("/health")
def get_health():
    return {"status": "ok"}


@router.get("/")
def get_config(request: Request):
    config = config_manager.config

    config_raw = {
        k: (v.value if hasattr(v, "value") else v)
        for k, v in config.model_dump().items()
    }

    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={
            "config": config_raw,
        },
    )


@router.post("/")
async def update_config(request: Request):
    form = await request.form()

    try:
        config_update = {
            "recording_format": form["recording_format"],
            "sample_rate": form["sample_rate"],
            "triggered_recording": form["triggered_recording"],
            "minimum_trigger_frequency": form["minimum_trigger_frequency"],
            "maximum_recording_length": form["maximum_recording_length"],
            "trigger_window": form["trigger_window"],
            "save_noise_files": form["save_noise_files"],
            "latitude": form["latitude"],
            "longitude": form["longitude"],
            "schedule_mode": form["schedule_mode"],
        }
        config_manager.update(config_update)
    except ValidationError as e:
        errors = {}
        for error in e.errors():
            field = error["loc"][0]
            errors.setdefault(field, []).append(error["msg"])

        return templates.TemplateResponse(
            request=request,
            name="config.html",
            context={
                "config": config_update,
                "errors": errors
            },
            status_code=400,
        )
    return RedirectResponse("/", status_code=300)
