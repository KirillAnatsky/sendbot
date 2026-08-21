import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope


class NoCacheStatic(StaticFiles):
    """Отдаёт статику с no-cache, чтобы браузер не показывал старую версию
    админки после обновления (иначе кэшированный app.js ломает страницу)."""

    async def get_response(self, path: str, scope: Scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

from .api import router
from .bot.runner import manager, start_bot_and_workers
from .db import init_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from .auth import ensure_owner_exists

    await ensure_owner_exists()
    await start_bot_and_workers()
    yield
    await manager.shutdown()


app = FastAPI(title="SendBot", lifespan=lifespan)
app.include_router(router)

static_dir = Path(__file__).parent / "static"
app.mount("/admin", NoCacheStatic(directory=static_dir, html=True), name="admin")

from .config import settings  # noqa: E402

media_dir = Path(settings.media_dir)
media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")


@app.get("/")
async def root():
    return RedirectResponse("/admin/")
