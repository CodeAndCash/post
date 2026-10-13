import asyncio
import json
import logging
import os
import re
import time

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"]  # https-адрес страницы webapp/index.html
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

CLEAR_TEXT = "🗑 Убрать медиа"
media: dict[int, tuple[str, str]] = {}  # user_id -> (photo|video|gif, file_id)
dp = Dispatcher()


def allowed(m: Message) -> bool:
    return not ADMIN_IDS or (m.from_user and m.from_user.id in ADMIN_IDS)


def keyboard(uid: int) -> ReplyKeyboardMarkup:
    kind = media.get(uid, ("none", ""))[0]
    sep = "&" if "?" in WEBAPP_URL else "?"
    url = f"{WEBAPP_URL}{sep}media={kind}&v={int(time.time())}"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✍️ Создать пост", web_app=WebAppInfo(url=url))],
            [KeyboardButton(text=CLEAR_TEXT)],
        ],
        resize_keyboard=True,
    )


@dp.message(CommandStart())
async def start(m: Message):
    if not allowed(m):
        return
    await m.answer(
        "Пришли фото, видео или гифку (по желанию) и нажми «Создать пост».\n"
        "Я соберу сообщение с кнопками, а ты перешлёшь его в канал.",
        reply_markup=keyboard(m.from_user.id),
    )


@dp.message(F.photo | F.video | F.animation)
async def got_media(m: Message):
    if not allowed(m):
        return
    if m.animation:  # у гифки заполнены и animation, и document, поэтому она первая
        media[m.from_user.id] = ("gif", m.animation.file_id)
    elif m.video:
        media[m.from_user.id] = ("video", m.video.file_id)
    else:
        media[m.from_user.id] = ("photo", m.photo[-1].file_id)
    await m.answer("Медиа сохранено ✅ Теперь нажми «Создать пост».", reply_markup=keyboard(m.from_user.id))


@dp.message(F.text == CLEAR_TEXT)
async def clear(m: Message):
    if not allowed(m):
        return
    media.pop(m.from_user.id, None)
    await m.answer("Медиа убрано.", reply_markup=keyboard(m.from_user.id))


@dp.message(F.web_app_data)
async def got_form(m: Message, bot: Bot):
    if not allowed(m):
        return
    uid = m.from_user.id
    try:
        data = json.loads(m.web_app_data.data)
        text = str(data.get("text", "")).strip()
        rows = [
            [InlineKeyboardButton(text=str(b["t"]).strip()[:64], url=str(b["u"]).strip())]
            for b in data.get("buttons", [])[:3]
            if str(b.get("t", "")).strip() and str(b.get("u", "")).startswith(("http://", "https://", "tg://"))
        ]
    except (ValueError, KeyError, AttributeError, TypeError):
        await m.answer("Не удалось прочитать данные формы, попробуй ещё раз.")
        return

    markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    kind, file_id = media.get(uid, (None, None))
    if not text and not kind:
        await m.answer("Пост пустой: добавь текст или пришли медиа.")
        return
    limit = 1024 if kind else 4096
    if len(re.sub(r"<[^>]+>", "", text)) > limit:
        await m.answer(f"Текст длиннее {limit} символов, сократи его.")
        return

    async def send(parse_mode):
        kw = {"reply_markup": markup, "parse_mode": parse_mode}
        cap = text or None
        if kind == "photo":
            return await bot.send_photo(m.chat.id, file_id, caption=cap, **kw)
        if kind == "video":
            return await bot.send_video(m.chat.id, file_id, caption=cap, **kw)
        if kind == "gif":
            return await bot.send_animation(m.chat.id, file_id, caption=cap, **kw)
        return await bot.send_message(
            m.chat.id, text, link_preview_options=LinkPreviewOptions(is_disabled=True), **kw
        )

    try:
        try:
            await send(ParseMode.HTML)
        except TelegramBadRequest as e:
            if "parse entities" not in str(e):
                raise
            await send(None)
            await m.answer("В тексте есть лишние символы < или >, поэтому отправил без жирного и курсива.")
        await m.answer("Готово ✅ Перешли сообщение выше в канал.", reply_markup=keyboard(uid))
    except TelegramBadRequest as e:
        await m.answer(f"Telegram не принял пост: {e.message}")


async def serve_webapp():
    port = int(os.getenv("PORT", "8080"))  # Railway обычно задаёт PORT сам
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp", "index.html")

    async def index(_):
        return web.FileResponse(page, headers={"Cache-Control": "no-cache"})

    app = web.Application()
    app.router.add_get("/", index)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logging.info("Web server listening on port %s", port)


async def main():
    logging.basicConfig(level=logging.INFO)
    await serve_webapp()
    await dp.start_polling(Bot(TOKEN))


if __name__ == "__main__":
    asyncio.run(main())
