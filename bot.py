import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import (
    BufferedInputFile,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"]  # https-адрес сервиса на Railway
ADMIN_IDS = {int(x) for x in re.findall(r"\d+", os.getenv("ADMIN_IDS", ""))}
PORT = int(os.getenv("PORT", "8080"))
_ch = os.getenv("CHANNEL_ID", "").strip()  # @username канала или числовой id вида -100...
CHANNEL_ID = int(_ch) if re.fullmatch(r"-?\d+", _ch) else _ch
MB = 1024 * 1024
MAX_UPLOAD = 55 * MB

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "webapp", "index.html"), encoding="utf-8") as f:
    PAGE = f.read()

bot = Bot(TOKEN)
dp = Dispatcher()


def allowed(uid: int) -> bool:
    return not ADMIN_IDS or uid in ADMIN_IDS


def check_init_data(init_data: str):
    """Проверяет подпись Telegram и возвращает id пользователя или None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash")
        dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got):
            return None
        if time.time() - int(pairs["auth_date"]) > 86400:
            return None
        return int(json.loads(pairs["user"])["id"])
    except (KeyError, ValueError, TypeError):
        return None


@dp.message(CommandStart())
async def start(m: Message):
    if not allowed(m.from_user.id):
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Войти", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
    await m.answer(
        "Собери пост в миниаппе: медиа, текст и кнопки. "
        "Я пришлю готовое сообщение сюда, а ты перешлёшь его в канал.",
        reply_markup=kb,
    )


TT = re.compile(r"https?://(?:[\w-]+\.)?tiktok\.com/\S+", re.I)
FMT = "bv*+ba/b" if shutil.which("ffmpeg") else "b"
TG_LIMIT = 49 * MB  # бот может отправить файл до 50 МБ


def download(url: str, out_dir: str, fmt: str) -> str:
    import yt_dlp  # импорт здесь, чтобы подхватить версию после автообновления

    for name in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, name))
    opts = {
        "format": fmt,
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = [os.path.join(out_dir, n) for n in os.listdir(out_dir)]
    if not files:
        raise RuntimeError("yt-dlp ничего не скачал")
    return max(files, key=os.path.getsize)


def upgrade_ytdlp():
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "-q", "yt-dlp"],
            timeout=120, capture_output=True, text=True,
        )
        logging.info("Обновление yt-dlp: код %s", r.returncode)
    except Exception:
        logging.warning("Не удалось обновить yt-dlp", exc_info=True)


@dp.message(lambda m: bool(m.text and TT.search(m.text)))
async def tiktok(m: Message):
    if not ADMIN_IDS or m.from_user.id not in ADMIN_IDS:
        return
    url = TT.search(m.text).group(0)
    status = await m.answer("Скачиваю…")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path = await asyncio.to_thread(download, url, tmp, FMT)
            if os.path.getsize(path) > TG_LIMIT:  # слишком тяжёлое, пробуем качество поменьше
                path = await asyncio.to_thread(download, url, tmp, "b[height<=720]/w")
            if os.path.getsize(path) > TG_LIMIT:
                await status.edit_text("Видео больше 50 МБ, Telegram не даёт боту его отправить.")
                return
            await m.answer_video(FSInputFile(path), supports_streaming=True)
            await status.delete()
        except Exception:
            logging.exception("tiktok download failed")
            await status.edit_text(
                "Не получилось скачать. Проверь, что ссылка рабочая, а видео открытое. "
                "Если повторяется, TikTok мог заблокировать сервер или нужно обновить yt-dlp (перезапусти сервис)."
            )


def err(msg: str, status: int = 400):
    return web.json_response({"error": msg}, status=status)


async def index(_):
    return web.Response(text=PAGE, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def health(_):
    return web.Response(text="ok")


async def api_post(request: web.Request):
    try:
        form = await request.post()
    except web.HTTPRequestEntityTooLarge:
        return err("Файл слишком большой, максимум 50 МБ.", 413)

    uid = check_init_data(str(form.get("initData", "")))
    if uid is None:
        return err("Открой миниапп из Telegram, кнопкой «Войти» у бота.", 403)
    if not allowed(uid):
        return err("У тебя нет доступа к этому боту.", 403)
    to_channel = str(form.get("target", "")) == "channel"
    if to_channel and not (CHANNEL_ID and ADMIN_IDS):
        return err("Для публикации в канал задай переменные CHANNEL_ID и ADMIN_IDS.")
    chat = CHANNEL_ID if to_channel else uid

    text = str(form.get("text", "")).strip()
    try:
        raw = json.loads(str(form.get("buttons", "[]")))
        rows = [
            [InlineKeyboardButton(text=str(b["t"]).strip()[:64], url=str(b["u"]).strip())]
            for b in raw[:3]
            if str(b.get("t", "")).strip() and str(b.get("u", "")).startswith(("http://", "https://", "tg://"))
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return err("Не удалось прочитать кнопки.")
    markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

    kind = data = name = None
    field = form.get("media")
    if getattr(field, "file", None):
        data = field.file.read()
        name = field.filename or "media"
        ctype = field.content_type or ""
        if not data:
            data = None
        elif ctype == "image/gif":
            kind = "gif"
        elif ctype.startswith("video/"):
            kind = "video"
        elif ctype.startswith("image/"):
            kind = "photo"
        else:
            return err("Поддерживаются фото, видео и GIF.")
        if kind and len(data) > (10 if kind == "photo" else 50) * MB:
            return err("Фото больше 10 МБ." if kind == "photo" else "Файл больше 50 МБ.")

    if not text and not kind:
        return err("Добавь текст или медиа.")
    limit = 1024 if kind else 4096
    if len(re.sub(r"<[^>]+>", "", text)) > limit:
        return err(f"Текст длиннее {limit} символов.")

    async def send(parse_mode):
        kw = {"reply_markup": markup, "parse_mode": parse_mode}
        cap = text or None
        if kind:
            media = BufferedInputFile(data, filename=name)
            fn = {"photo": bot.send_photo, "video": bot.send_video, "gif": bot.send_animation}[kind]
            return await fn(chat, media, caption=cap, **kw)
        return await bot.send_message(chat, text, link_preview_options=LinkPreviewOptions(is_disabled=True), **kw)

    note = None
    try:
        try:
            await send(ParseMode.HTML)
        except TelegramBadRequest as e:
            if "parse entities" not in str(e):
                raise
            await send(None)
            note = "В тексте есть лишние символы < или >, поэтому отправил без жирного и курсива."
    except TelegramForbiddenError:
        return err("Бот не может писать в канал: сделай его админом с правом публикации." if to_channel
                   else "Нажми /start у бота и попробуй снова.", 403)
    except TelegramBadRequest as e:
        return err(f"Telegram не принял пост: {e.message}")
    except Exception:
        logging.exception("send failed")
        return err("Не удалось отправить пост, попробуй ещё раз.", 500)
    return web.json_response({"ok": True, "note": note})


def make_app() -> web.Application:
    app = web.Application(client_max_size=MAX_UPLOAD)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/api/post", api_post)
    return app


async def main():
    logging.basicConfig(level=logging.INFO)
    runner = web.AppRunner(make_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logging.info("Web server listening on port %s", PORT)
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Войти", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except Exception:
        logging.warning("Не удалось поставить кнопку меню", exc_info=True)
    await asyncio.to_thread(upgrade_ytdlp)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
