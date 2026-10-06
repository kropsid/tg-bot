import asyncio
import os
import logging

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    MessageReactionHandler,
    ContextTypes,
    filters,
)

# ==== Настройки из переменных окружения ====
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
PORT = int(os.environ.get("PORT", 8080))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ==== Обработчики ====
async def notify_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not user or user.is_bot:
        return

    header = (
        f"📩 Новое сообщение\n"
        f"👥 Чат: {chat.title} (id: {chat.id})\n"
        f"👤 От: {user.full_name} (@{user.username or '—'}, id: {user.id})\n"
        f"🕒 {msg.date}\n"
        f"— — —"
    )

    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=header)
        await msg.forward(chat_id=ADMIN_ID)
    except Exception as e:
        logger.error(f"[message] Ошибка: {e}")


async def notify_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction = update.message_reaction
    if not reaction:
        return

    chat = update.effective_chat
    user = update.effective_user
    if not user:
        return

    new_emojis = [
        getattr(r, "emoji", None) or f"custom:{getattr(r, 'custom_emoji_id', '?')}"
        for r in reaction.new_reaction
    ]
    old_emojis = [
        getattr(r, "emoji", None) or f"custom:{getattr(r, 'custom_emoji_id', '?')}"
        for r in reaction.old_reaction
    ]

    if new_emojis:
        action = f"поставил(а): {' '.join(new_emojis)}"
    else:
        action = f"снял(а): {' '.join(old_emojis)}"

    text = (
        f"⚡ Реакция\n"
        f"👥 Чат: {chat.title} (id: {chat.id})\n"
        f"👤 {user.full_name} (@{user.username or '—'}, id: {user.id})\n"
        f"➡️ {action}\n"
        f"📎 id сообщения: {reaction.message_id}\n"
        f"— — —"
    )

    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text)
        await context.bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=chat.id,
            message_id=reaction.message_id,
        )
    except Exception as e:
        logger.error(f"[reaction] Не удалось переслать исходное: {e}")


# ==== Веб-сервер: вебхук + health-check ====
async def health_check(request: web.Request) -> web.Response:
    """UptimeRobot пингует этот адрес, чтобы бот не засыпал."""
    return web.Response(text="OK")


async def webhook_handler(request: web.Request) -> web.Response:
    """Telegram шлёт сюда апдейты."""
    ptb_app: Application = request.app["ptb_app"]
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return web.Response(text="OK")


# ==== Основной цикл ====
async def main():
    # 1. Создаём приложение Telegram
    ptb_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    ptb_app.add_handler(
        MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, notify_message)
    )
    ptb_app.add_handler(MessageReactionHandler(notify_reaction))

    # 2. Инициализируем
    await ptb_app.initialize()
    await ptb_app.start()

    # 3. Ставим вебхук (Telegram будет сам присылать апдейты)
    if RENDER_URL:
        webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
        await ptb_app.bot.set_webhook(
            url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        logger.info(f"Вебхук установлен: {webhook_url}")
    else:
        logger.warning("RENDER_EXTERNAL_URL не задан — вебхук не установлен!")

    # 4. Поднимаем веб-сервер
    web_app = web.Application()
    web_app["ptb_app"] = ptb_app
    web_app.router.add_get("/health", health_check)
    web_app.router.add_post(f"/{BOT_TOKEN}", webhook_handler)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Веб-сервер запущен на порту {PORT}")

    # 5. Держим процесс живым
    try:
        await asyncio.Event().wait()
    finally:
        await ptb_app.stop()
        await ptb_app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())