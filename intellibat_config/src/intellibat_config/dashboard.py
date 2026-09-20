from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel, Field

from intellibat_config.api import templates
from intellibat_config.device_clock import DeviceClock
from intellibat_config.recordings import RecordingIndex
from intellibat_config.settings import settings
from intellibat_config.storage import CopyManager, ExternalStorage
from intellibat_config.telemetry import SystemTelemetry

router = APIRouter()
index = RecordingIndex(
    settings.intellibat_recordings_path,
    settings.intellibat_spectrograms_path,
    settings.intellibat_timezone,
)
telemetry = SystemTelemetry(settings)
storage = ExternalStorage(index.roots.values())
copies = CopyManager(index, storage)
clock = DeviceClock()


class ManualClock(BaseModel):
    instant: AwareDatetime


class Destination(BaseModel):
    drive_id: str = Field(min_length=1, max_length=100)
    folder: str = Field(default='', max_length=200)


def same_origin(request):
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(
            status_code=403, detail='Use this device’s interface to make changes.'
        )
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(
            status_code=403, detail='Cross-site requests are not allowed.'
        )


@router.get('/status')
def status_page(request: Request):
    return templates.TemplateResponse(
        request=request, name='status.html', context={'active_tab': 'status'}
    )


@router.get('/data')
def data_page(request: Request):
    return templates.TemplateResponse(
        request=request, name='data.html', context={'active_tab': 'data'}
    )


@router.get('/api/status')
def system_status():
    return telemetry.snapshot()


@router.get('/api/clock')
def clock_status():
    return clock.snapshot()


@router.post('/api/clock/manual')
def set_device_clock(value: ManualClock, request: Request):
    same_origin(request)
    return clock.set_manual(value.instant)


@router.post('/api/clock/network')
def enable_network_clock(request: Request):
    same_origin(request)
    return clock.enable_network_time()


@router.get('/api/recordings')
def recordings():
    return index.summary()


@router.get('/api/recordings/image/{relative:path}')
def recording_image(relative: str):
    try:
        return FileResponse(
            index.media_path(relative), headers={'X-Content-Type-Options': 'nosniff'}
        )
    except (ValueError, OSError) as error:
        raise HTTPException(
            status_code=404, detail='Recording image not found.'
        ) from error


@router.get('/api/storage')
def external_storage():
    return storage.list()


@router.get('/api/storage/{drive_id}/folders')
def destination_folders(drive_id: str):
    return storage.folders(drive_id)


@router.post('/api/storage/{drive_id}/mount')
def mount_drive(drive_id: str, request: Request):
    same_origin(request)
    return storage.mount(drive_id)


@router.post('/api/storage/folders')
def create_folder(destination: Destination, request: Request):
    same_origin(request)
    return storage.create_folder(destination.drive_id, destination.folder)


@router.post('/api/copies/estimate', status_code=202)
def estimate_copy(destination: Destination, request: Request):
    same_origin(request)
    return copies.estimate(destination.drive_id, destination.folder)


@router.get('/api/copies/current')
def current_copy():
    return copies.snapshot()


@router.get('/api/copies/{job_id}')
def copy_status(job_id: str):
    return copies.snapshot(job_id)


@router.post('/api/copies/{job_id}/start', status_code=202)
def start_copy(job_id: str, request: Request):
    same_origin(request)
    return copies.start(job_id)


@router.post('/api/copies/{job_id}/cancel')
def cancel_copy(job_id: str, request: Request):
    same_origin(request)
    return copies.cancel(job_id)
