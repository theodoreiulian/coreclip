import os
import shutil
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import config
from database import init_db
from api import routes_jobs, routes_clips, routes_logs, routes_ws, routes_accounts, routes_posting, routes_settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()
    init_db()
    yield

app = FastAPI(title="CoreClip API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_jobs.router, prefix="/api")
app.include_router(routes_clips.router, prefix="/api")
app.include_router(routes_accounts.router, prefix="/api")
app.include_router(routes_posting.router, prefix="/api")
app.include_router(routes_settings.router, prefix="/api")
app.include_router(routes_logs.router, prefix="/api/logs")
app.include_router(routes_ws.router, prefix="/ws")

@app.get("/api/health")
def health_check():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    total, used, free = shutil.disk_usage(config.OUTPUT_DIR)
    free_gb = free / (1024 ** 3)
    
    return {
        "status": "ok",
        "disk_space_gb": round(free_gb, 2),
        "db": "connected"
    }
