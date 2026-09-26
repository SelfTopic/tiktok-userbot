"""Скачивание медиа из TikTok.

Цепочка: yt-dlp (видео) -> gallery-dl (слайдшоу) -> tikwm.com (запасной вариант).
yt-dlp и gallery-dl вызываются подпроцессами, чтобы их автообновление
применялось без перезапуска бота.
"""
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

TIKTOK_URL_RE = re.compile(r"https?://(?:[\w-]+\.)*tiktok\.com/[^\s<>\"']+", re.IGNORECASE)

TOOL_TIMEOUT = 180
TIKWM_API = "https://www.tikwm.com/api/"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".ogg", ".opus"}

# --xff US обходит региональные блокировки постов (проверено с IP в NL)
YTDLP = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist", "--xff", "US"]
GALLERYDL = [sys.executable, "-m", "gallery_dl", "--quiet"]
# Лучшее разрешение; при равном разрешении предпочитаем H.264 как более совместимый.
# Формат "download" — версия с водяным знаком.
YTDLP_FORMAT = ["-f", "b[format_id!=download]/b", "-S", "res,vcodec:h264"]
# Обход через --xff срабатывает не каждый раз, поэтому при блокировке по IP повторяем
GEO_BLOCK_ATTEMPTS = 3


class DownloadError(Exception):
    pass


@dataclass
class Video:
    path: Path
    width: int = 0
    height: int = 0
    duration: int = 0
    thumb: Path | None = None


@dataclass
class Slideshow:
    images: list[Path]
    audio: Path | None = None


def find_urls(text: str) -> list[str]:
    urls = (m.rstrip(".,!?)]}»") for m in TIKTOK_URL_RE.findall(text))
    return list(dict.fromkeys(urls))


async def _run(*args: str, timeout: float = TOOL_TIMEOUT) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise DownloadError(f"timeout: {' '.join(args[:4])}")
    if proc.returncode != 0:
        lines = err.decode(errors="replace").strip().splitlines()
        raise DownloadError(lines[-1] if lines else f"exit code {proc.returncode}")
    return out.decode(errors="replace")


async def _resolve_short_link(url: str, http: httpx.AsyncClient) -> str:
    """vm.tiktok.com / vt.tiktok.com / tiktok.com/t/... -> полная ссылка на пост."""
    parts = urlsplit(url)
    if not (parts.hostname.startswith(("vm.", "vt.")) or parts.path.startswith("/t/")):
        return url
    try:
        r = await http.get(url)
        return str(r.url)
    except httpx.HTTPError as e:
        log.warning("не удалось раскрыть короткую ссылку %s: %s", url, e)
        return url


async def _probe(path: Path, workdir: Path) -> Video:
    out = await _run(
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path),
    )
    meta = json.loads(out)
    stream = (meta.get("streams") or [{}])[0]
    duration = float(meta.get("format", {}).get("duration") or 0)

    thumb = workdir / "thumb.jpg"
    try:
        await _run(
            "ffmpeg", "-v", "error", "-y", "-i", str(path), "-frames:v", "1",
            "-vf", "scale=320:320:force_original_aspect_ratio=decrease", str(thumb),
        )
    except DownloadError as e:
        log.warning("не удалось сделать превью: %s", e)
        thumb = None

    return Video(path, stream.get("width", 0), stream.get("height", 0), round(duration), thumb)


async def _ytdlp_info(url: str) -> dict:
    for attempt in range(1, GEO_BLOCK_ATTEMPTS + 1):
        try:
            return json.loads(await _run(*YTDLP, "--dump-single-json", url))
        except DownloadError as e:
            if "IP address is blocked" not in str(e) or attempt == GEO_BLOCK_ATTEMPTS:
                raise
            log.info("гео-блок для %s, попытка %d", url, attempt)


async def _ytdlp(url: str, workdir: Path) -> Video | Slideshow:
    info = await _ytdlp_info(url)
    has_video = any(f.get("vcodec") not in (None, "none") for f in info.get("formats", []))
    if not has_video:
        # Слайдшоу: yt-dlp отдаёт только звук, картинки берём через gallery-dl
        return await _gallerydl(info.get("webpage_url") or url, workdir)

    info_path = workdir / "info.json"
    info_path.write_text(json.dumps(info))
    out = await _run(
        *YTDLP, *YTDLP_FORMAT, "--load-info-json", str(info_path),
        "-o", str(workdir / "video.%(ext)s"), "--print", "after_move:filepath",
    )
    return await _probe(Path(out.strip().splitlines()[-1]), workdir)


async def _gallerydl(url: str, workdir: Path) -> Slideshow:
    target = workdir / "gallery"
    await _run(*GALLERYDL, "-D", str(target), url)
    files = sorted(target.iterdir()) if target.exists() else []
    images = [p for p in files if p.suffix.lower() in IMAGE_EXTS]
    audio = next((p for p in files if p.suffix.lower() in AUDIO_EXTS), None)
    if not images:
        raise DownloadError("gallery-dl не вернул картинок")
    return Slideshow(images, audio)


async def _fetch(http: httpx.AsyncClient, url: str, path: Path) -> Path:
    if url.startswith("/"):
        url = "https://www.tikwm.com" + url
    async with http.stream("GET", url) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            async for chunk in r.aiter_bytes(1 << 16):
                f.write(chunk)
    return path


async def _tikwm(url: str, workdir: Path, http: httpx.AsyncClient) -> Video | Slideshow:
    r = await http.post(TIKWM_API, data={"url": url, "hd": 1})
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise DownloadError(f"tikwm: {body.get('msg')}")
    data = body["data"]

    if data.get("images"):
        images = [
            await _fetch(http, img, workdir / f"tikwm_{i:02}.jpg")
            for i, img in enumerate(data["images"])
        ]
        audio = await _fetch(http, data["music"], workdir / "tikwm.mp3") if data.get("music") else None
        return Slideshow(images, audio)

    src = data.get("hdplay") or data.get("play")
    if not src:
        raise DownloadError("tikwm: нет ссылки на видео")
    return await _probe(await _fetch(http, src, workdir / "tikwm.mp4"), workdir)


async def download(url: str, workdir: Path) -> Video | Slideshow:
    async with httpx.AsyncClient(
        headers={"User-Agent": BROWSER_UA}, follow_redirects=True, timeout=60
    ) as http:
        url = await _resolve_short_link(url, http)
        try:
            if "/photo/" in url:
                return await _gallerydl(url, workdir)
            return await _ytdlp(url, workdir)
        except DownloadError as e:
            log.warning("основной способ не сработал для %s: %s; пробую tikwm", url, e)

        try:
            return await _tikwm(url, workdir, http)
        except (DownloadError, httpx.HTTPError, ValueError, KeyError) as e:
            raise DownloadError(f"все способы не сработали для {url}: {e}") from e


async def update_tools() -> None:
    await _run(
        sys.executable, "-m", "pip", "install", "-q", "-U",
        "yt-dlp[default,curl-cffi]", "gallery-dl", timeout=600,
    )
    version = (await _run(*YTDLP[:3], "--version")).strip()
    log.info("yt-dlp и gallery-dl обновлены (yt-dlp %s)", version)


async def _cli(urls: list[str]) -> None:
    """python downloader.py URL... — проверка скачивания без Telegram, файлы в ./out/"""
    for i, url in enumerate(urls):
        workdir = Path("out") / str(i)
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            print(url, "->", await download(url, workdir))
        except DownloadError as e:
            print(url, "-> ОШИБКА:", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(_cli(sys.argv[1:]))
