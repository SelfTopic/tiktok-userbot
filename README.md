# tiktok-userbot

Юзербот для Telegram на [Kurigram](https://github.com/KurimuzonAkuma/kurigram) (форк Pyrogram). В одном заданном чате он находит сообщения со ссылками на TikTok и отвечает на них скачанным видео или слайдшоу.

## Как скачивается

1. **yt-dlp**: видео в лучшем доступном разрешении, при равном разрешении выбирается H.264. Региональные блокировки обходятся через `--xff US`, при блокировке делается повторная попытка.
2. **gallery-dl**: слайдшоу (фото с музыкой). Картинки уходят альбомами по 10, звук отдельным сообщением.
3. **tikwm.com**: запасной вариант, если первые два не сработали.

Короткие ссылки (`vm.tiktok.com`, `vt.tiktok.com`) раскрываются автоматически. yt-dlp и gallery-dl обновляются раз в сутки прямо внутри контейнера, чтобы успевать за изменениями TikTok.

## Запуск на сервере

Нужны только Docker и Docker Compose.

```sh
git clone https://github.com/SelfTopic/tiktok-userbot.git
cd tiktok-userbot
cp .env.example .env   # заполнить API_ID, API_HASH, CHAT_ID
docker compose pull
```

Первый вход делается по QR-коду: коды входа Telegram доставляет сторонним клиентам ненадёжно ([подробнее](https://github.com/LonamiWebs/Telethon/issues/4730)). Сессия сохраняется в volume `data`.

```sh
docker compose run --rm bot
# в терминале появится QR: в Telegram на телефоне открыть
# Настройки → Устройства → Подключить устройство и отсканировать его.
# Если включён облачный пароль, бот его спросит.
# После строки «запущен, слушаю чат ...» нажать Ctrl+C
docker compose up -d
```

QR живёт около 30 секунд, потом бот сам покажет новый. Если QR не помещается, уменьшите шрифт в терминале.

Логи и обновление:

```sh
docker compose logs -f
docker compose pull && docker compose up -d
```

Образ `ghcr.io/selftopic/tiktok-userbot:latest` собирается GitHub Actions при каждом пуше в `main`.

## Настройки (`.env`)

| Переменная | Описание |
|---|---|
| `API_ID`, `API_HASH` | берутся на https://my.telegram.org в разделе API development tools |
| `CHAT_ID` | id чата (например `-1001234567890`) или `@username` |
| `SESSION_NAME` | имя файла сессии, по умолчанию `userbot` |

## Проверка скачивания без Telegram

```sh
docker compose run --rm --entrypoint python bot downloader.py https://vt.tiktok.com/ZSbYQSbFS/
```
