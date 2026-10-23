"""Уменьшенные копии картинок для холста редактора.

На плитке «Сообщения» картинка занимает ~240 пикселей в ширину, а грузилась
целиком — фото из Telegram весят 1–3 МБ. Большая воронка со 150 картинками
тянула сотни мегабайт при каждом открытии, и всё остальное (сохранение,
статистика, AI) стояло в очереди за ними. Здесь отдаём копию шириной до
w пикселей; делается она один раз и кладётся рядом в media/.thumbs.
Подписчикам по-прежнему уходит оригинал — это только для админки.
"""
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from .config import settings

log = logging.getLogger("sendbot.thumbs")
router = APIRouter()

SIZES = (240, 480, 960)
_RASTER = {".jpg", ".jpeg", ".png", ".webp"}
_CACHE = {"Cache-Control": "public, max-age=604800"}


def _source(rel: str) -> Path | None:
    media = Path(settings.media_dir).resolve()
    rel = rel[len("media/"):] if rel.startswith("media/") else rel
    p = (media / rel).resolve()
    if media not in p.parents or not p.is_file() or ".thumbs" in p.parts:
        return None
    return p


def _make(src: Path, dst: Path, w: int) -> bool:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((w, w * 4))
            if im.mode not in ("RGB", "L"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.convert("RGBA").split()[-1])
                im = bg
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=80, optimize=True)
            tmp.replace(dst)
        return True
    except Exception as e:  # noqa: BLE001 — битая картинка: отдадим оригинал
        log.info("Превью для %s не сделано: %s", src.name, e)
        return False


@router.get("/thumb/{w}/{path:path}", include_in_schema=False)
async def thumb(w: int, path: str):
    src = _source(path)
    if src is None:
        return Response(status_code=404)
    w = min(SIZES, key=lambda s: abs(s - w))
    if src.suffix.lower() not in _RASTER:          # gif и прочее — как есть
        return FileResponse(src, headers=_CACHE)
    media = Path(settings.media_dir).resolve()
    dst = media / ".thumbs" / str(w) / (str(src.relative_to(media)) + ".jpg")
    fresh = dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime
    if not fresh and not await run_in_threadpool(_make, src, dst, w):
        return FileResponse(src, headers=_CACHE)
    return FileResponse(dst, media_type="image/jpeg", headers=_CACHE)
