"""Telegram service: runs the trading bot in a background thread and exposes control + notifications.

Run: python telegram_service.py      (needs TELEGRAM_BOT_TOKEN in .env / environment)
"""
import asyncio
import logging
import os
import queue
import threading
from pathlib import Path

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from bot import Bot, setup_logging
from core.config import load_config, load_dotenv
from core.telegram_control import Controller

log = logging.getLogger("telegram")
BASE = Path(__file__).resolve().parent
DEFAULT_PASSWORD = "679902"
CHUNK = 3900


def chunks(text: str):
    while text:
        yield text[:CHUNK]
        text = text[CHUNK:]


def main() -> None:
    load_dotenv(BASE / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (put it in .env)")
    config_path = BASE / "config" / "config.yaml"
    cfg = load_config(config_path, require_token=False)   # may start without a Groww token; use /settoken
    setup_logging(BASE / cfg["paths"]["log_dir"])
    bot = Bot(cfg, base_dir=BASE)
    ctl = Controller(bot, config_path, BASE / ".env", os.environ.get("TELEGRAM_PASSWORD", DEFAULT_PASSWORD),
                     bot.log_dir / "telegram_auth.json")
    outbox: "asyncio.Queue[str]" = None  # created in post_init (needs the running loop)
    holder: dict = {}

    def notify(text: str) -> None:
        loop = holder.get("loop")
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(outbox.put_nowait, text)

    async def sender(app: Application) -> None:
        while True:
            text = await outbox.get()
            for chat_id in list(ctl.chats):
                for part in chunks(text):
                    try:
                        await app.bot.send_message(chat_id, part)
                    except Exception as e:
                        log.warning("send to %s failed: %s", chat_id, e)

    async def post_init(app: Application) -> None:
        nonlocal outbox
        outbox = asyncio.Queue()
        holder["loop"] = asyncio.get_running_loop()
        holder["task"] = asyncio.create_task(sender(app))
        bot.notify = notify
        t = threading.Thread(target=bot.run, name="trading-bot", daemon=True)
        t.start()
        holder["thread"] = t
        notify(f"Bot service started. Trading: {'PAUSED' if bot.paused else 'ACTIVE'}. /status for details.")

    async def post_shutdown(app: Application) -> None:
        bot.stop_event.set()
        t = holder.get("thread")
        if t:
            await asyncio.to_thread(t.join, 40)

    async def on_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg, chat = update.message, update.effective_chat
        if not msg or not msg.text or chat.type != "private":
            return
        parts = msg.text.split()
        cmd = parts[0][1:].split("@")[0].lower()
        reply, delete = await asyncio.to_thread(ctl.handle, chat.id, cmd, parts[1:])
        if delete:   # hide passwords / tokens from chat history
            try:
                await msg.delete()
            except Exception:
                pass
        for part in chunks(reply):
            await ctx.bot.send_message(chat.id, part)

    app = Application.builder().token(token).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(MessageHandler(filters.COMMAND, on_command))
    log.info("Telegram service starting")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
