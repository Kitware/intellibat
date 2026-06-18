import api
from fastapi import FastAPI

app = FastAPI(title="Intellibat Config")

app.include_router(api.router, prefix="/config")
