import api
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

app = FastAPI(title="Intellibat Config")

@app.get("/")
def root():
    return RedirectResponse("/config/")

app.include_router(api.router, prefix="/config")
