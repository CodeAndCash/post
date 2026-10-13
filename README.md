# Бот для постов с кнопками

1. В @BotFather создай бота и скопируй токен.
2. Выложи `webapp/index.html` на любой хостинг с HTTPS (GitHub Pages, Netlify, Vercel). Нужна ссылка на этот файл.
3. Скопируй `.env.example` в `.env` и заполни `BOT_TOKEN`, `WEBAPP_URL`. Свой ID можно узнать у @userinfobot и вписать в `ADMIN_IDS`.
4. `pip install -r requirements.txt`, затем `python bot.py`.

Как пользоваться: отправь боту фото, видео или гифку (необязательно), нажми «Создать пост», заполни текст и кнопки, нажми «Создать пост» внизу. Бот пришлёт готовое сообщение, перешли его в канал. Медиа хранится, пока не нажмёшь «Убрать медиа» или не перезапустишь бота.

## Railway
1. Залей папку проекта в GitHub (файл `.env` не заливай, он уже в `.gitignore`).
2. Railway → New Project → Deploy from GitHub repo → выбери репозиторий. Если проект лежит в подпапке, укажи её в Root Directory.
3. Вкладка Variables: добавь `BOT_TOKEN` и `ADMIN_IDS`.
4. Settings → Networking → Generate Domain, скопируй адрес.
5. Добавь переменную `WEBAPP_URL` = `https://твой-домен.up.railway.app/` (с закрывающим `/`), Railway перезапустит сервис.
Бот сам отдаёт страницу миниаппа по этому адресу, отдельный хостинг не нужен.
