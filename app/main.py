import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import FRONTEND_DIR
from app.database import init_db
from app.scheduler import start_scheduler
from app.routers import todos, config, logs, sync, feed, dida

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await start_scheduler()
    yield


app = FastAPI(title="Todo List Sync Service", lifespan=lifespan)

app.include_router(todos.router)
app.include_router(config.router)
app.include_router(logs.router)
app.include_router(sync.router)
app.include_router(feed.router)
app.include_router(dida.router)

app.mount("/assets", StaticFiles(directory=f"{FRONTEND_DIR}/assets"), name="assets")


@app.get("/")
async def serve_frontend():
    return FileResponse(f"{FRONTEND_DIR}/index.html")
