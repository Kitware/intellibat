from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from intellibat_config import api, dashboard
from intellibat_config.device_clock import ClockError
from intellibat_config.storage import StorageError

app = FastAPI(title='IntelliBat')
app.mount(
    '/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static'
)


@app.exception_handler(ClockError)
async def clock_error(request: Request, error: ClockError):
    return JSONResponse(status_code=400, content={'detail': str(error)})


@app.exception_handler(StorageError)
async def storage_error(request: Request, error: StorageError):
    return JSONResponse(status_code=400, content={'detail': str(error)})


@app.exception_handler(PermissionError)
async def permission_error(request: Request, error: PermissionError):
    return JSONResponse(status_code=403, content={'detail': f'Access denied: {error}'})


@app.get('/')
def root():
    return RedirectResponse('/status')


@app.exception_handler(OSError)
async def filesystem_error(request: Request, error: OSError):
    return JSONResponse(
        status_code=400, content={'detail': f'Filesystem operation failed: {error}'}
    )


app.include_router(api.router, prefix='/config')
app.include_router(dashboard.router)
