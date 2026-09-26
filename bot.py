"""Юзербот: в заданном чате отвечает на ссылки TikTok скачанным видео или слайдшоу."""
import asyncio
import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from pyrogram.types import InputMediaPhoto, Message

import downloader

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

UPDATE_INTERVAL = 24 * 3600
ALBUM_LIMIT = 10


def _chat_id(value: str) -> int | str:
    return int(value) if value.lstrip("-").isdigit() else value


CHAT = _chat_id(os.environ["CHAT_ID"])
app = Client(
    os.getenv("SESSION_NAME", "userbot"),
    api_id=int(os.environ["API_ID"]),
    api_hash=os.environ["API_HASH"],
    workdir=os.getenv("DATA_DIR", "."),
)
# Не качаем слишком много одновременно, если в чат накидали пачку ссылок
semaphore = asyncio.Semaphore(2)


async def send(message: Message, media: downloader.Video | downloader.Slideshow) -> None:
    if isinstance(media, downloader.Video):
        await message.reply_video(
            str(media.path),
            width=media.width,
            height=media.height,
            duration=media.duration,
            thumb=str(media.thumb) if media.thumb else None,
            supports_streaming=True,
        )
        return

    for i in range(0, len(media.images), ALBUM_LIMIT):
        chunk = media.images[i:i + ALBUM_LIMIT]
        if len(chunk) == 1:
            await message.reply_photo(str(chunk[0]))
        else:
            await message.reply_media_group([InputMediaPhoto(str(p)) for p in chunk])
    if media.audio:
        await message.reply_audio(str(media.audio))


@app.on_message(filters.chat(CHAT) & filters.text & filters.regex(downloader.TIKTOK_URL_RE))
async def on_tiktok(_: Client, message: Message) -> None:
    for url in downloader.find_urls(message.text):
        async with semaphore:
            log.info("скачиваю %s", url)
            try:
                with tempfile.TemporaryDirectory(prefix="tiktok_") as tmp:
                    await send(message, await downloader.download(url, Path(tmp)))
            except Exception:
                log.exception("не удалось обработать %s", url)


async def update_forever() -> None:
    while True:
        try:
            await downloader.update_tools()
        except Exception:
            log.exception("не удалось обновить yt-dlp/gallery-dl")
        await asyncio.sleep(UPDATE_INTERVAL)


async def main() -> None:
    # Вход по QR: коды входа Telegram доставляет сторонним клиентам ненадёжно.
    # При уже сохранённой сессии флаг ни на что не влияет.
    await app.start(use_qr=True)
    try:
        updater = asyncio.create_task(update_forever())
        log.info("запущен, слушаю чат %s", CHAT)
        await idle()
        updater.cancel()
    finally:
        await app.stop()


if __name__ == "__main__":
    # Kurigram создаёт свой event loop при импорте, asyncio.run() с ним несовместим
    app.loop.run_until_complete(main())
