"""
Main entry point for Telegram Star Pin Bot.
Handles application startup, 1-hour pin cycle listener, handler registration,
interactive callbacks, background auto-unpinning, 15-minute inactivity reminders, and polling.
"""

import asyncio
import logging
from datetime import datetime, timezone
from telegram import BotCommand
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    Application,
)

from config import BOT_TOKEN
from database import (
    init_db,
    get_expired_pins,
    mark_pin_inactive,
    get_inactive_groups,
    update_last_reminder,
)
from handlers.common_handler import (
    start_command,
    help_command,
    dashboard_callback,
    back_to_welcome_callback,
)
from handlers.pin_handler import (
    pin_command,
    stoppin_command,
    precheckout_handler,
    successful_payment_handler,
)
from handlers.admin_handler import (
    setprice_command,
    price_command,
    stats_command,
    unpin_command,
)
from handlers.gatekeeper import (
    group_gatekeeper_handler,
    send_or_replace_reminder,
)

import sys
import atexit
import traceback

import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from flask import Flask
from threading import Thread

app = Flask(name)


@app.route("/")
def home():
    return "Bot is alive"
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is online and polling!")

    def log_message(self, format, *args):
        pass  # Suppress HTTP logging noise

def start_health_server():
    """Starts a lightweight web server if PORT is provided (e.g. on Render.com free tier)."""
    if "PORT" in os.environ:
        try:
            port = int(os.environ["PORT"])
            server = HTTPServer(("0.0.0.0", port), HealthHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            logger.info("Cloud health check server started on port %s", port)
        except Exception as e:
            logger.warning("Could not start cloud health check server: %s", e)

# Configure logging: always log to bot.log, and to console if stdout is available
log_handlers = [logging.FileHandler("bot.log", mode="a", encoding="utf-8")]
if sys.stdout is not None:
    log_handlers.append(logging.StreamHandler())

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=log_handlers,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

def on_exit():
    logger.info("=== PYTHON PROCESS ATEXIT TRIGGERED ===")
    logger.info("Stack:\n%s", "".join(traceback.format_stack()))

atexit.register(on_exit)

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        return
    logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

INACTIVITY_REMINDER_HTML = (
    'Next ad will be pinned for an hour\n'
    '(<tg-emoji emoji-id="5893192487324880883">ℹ️</tg-emoji> Except Admins) '
    '<tg-emoji emoji-id="6275785734511141427">📌</tg-emoji>.'
)


async def auto_unpin_worker(bot) -> None:
    """Background task running every 15s to unpin messages whose duration has expired."""
    logger.info("Auto-unpin background worker started.")
    while True:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            expired_pins = get_expired_pins(now_iso)
            for pin in expired_pins:
                chat_id = pin["chat_id"]
                message_id = pin["message_id"]
                pin_id = pin["id"]
                try:
                    await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
                    logger.info("Auto-unpinned expired message %s in chat %s", message_id, chat_id)
                except Exception as e:
                    logger.warning("Could not auto-unpin message %s in chat %s: %s", message_id, chat_id, e)
                mark_pin_inactive(record_id=pin_id)

                # Send reminder immediately after unpinning
                try:
                    await send_or_replace_reminder(bot, chat_id)
                    logger.info("Sent post-unpin reminder in %s", chat_id)
                except Exception as e:
                    logger.warning("Could not send post-unpin reminder in %s: %s", chat_id, e)
        except Exception as e:
            logger.error("Error in auto_unpin_worker: %s", e)

        await asyncio.sleep(15)


async def inactivity_reminder_worker(bot) -> None:
    """Background task that posts reminder every 5-minute cycle when no message is pinned."""
    logger.info("5-minute cycle reminder worker started.")
    while True:
        try:
            inactive_groups = get_inactive_groups(threshold_minutes=5)
            for group in inactive_groups:
                chat_id = group["chat_id"]
                try:
                    await send_or_replace_reminder(bot, chat_id)
                    logger.info("Sent 5-min cycle reminder in %s", chat_id)
                except Exception as e:
                    logger.warning("Could not send reminder to %s: %s", chat_id, e)
        except Exception as e:
            logger.error("Error in inactivity_reminder_worker: %s", e)

        await asyncio.sleep(20)


async def post_init(application: Application) -> None:
    """Setup bot commands menu in Telegram upon startup and spawn background workers."""
    commands = [
        BotCommand("pin", "Reply to pay Stars & pin (or /pin 1h)"),
        BotCommand("stoppin", "Stop active pin early"),
        BotCommand("dashboard", "View automatic pin service dashboard"),
        BotCommand("price", "Check hourly Star pin price"),
        BotCommand("setprice", "(Admin) Change Star price for this group"),
        BotCommand("stats", "View group pin statistics"),
        BotCommand("unpin", "(Admin) Unpin message"),
        BotCommand("help", "Bot guide & rules"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        bot_info = await application.bot.get_me()
        logger.info("Bot commands menu registered for @%s", bot_info.username)
    except Exception as e:
        logger.warning("Could not set bot commands menu: %s", e)

    # Spawn background workers
    asyncio.create_task(auto_unpin_worker(application.bot))
    asyncio.create_task(inactivity_reminder_worker(application.bot))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error causing updates."""
    logger.error("Exception while handling update: %s", context.error, exc_info=context.error)


def main() -> None:
    """Start the bot with resilient auto-reconnect."""
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured! Please check your .env file.")

    # Initialize SQLite database (includes schema migrations)
    init_db()

    # Start health check server if running on Render / Cloud Web Service
    start_health_server()

    while True:
        try:
            # Build Telegram Bot Application
            app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

            # Common & Dashboard handlers
            app.add_handler(CommandHandler("start", start_command))
            app.add_handler(CommandHandler(["help", "dashboard"], help_command))
            app.add_handler(CallbackQueryHandler(dashboard_callback, pattern="^see_dashboard$"))
            app.add_handler(CallbackQueryHandler(back_to_welcome_callback, pattern="^back_to_welcome$"))

            # Pin & Duration commands
            app.add_handler(CommandHandler(["pin", "paytopin", "starpin"], pin_command))
            app.add_handler(CommandHandler("stoppin", stoppin_command))
            app.add_handler(CommandHandler(["price", "pinprice"], price_command))
            app.add_handler(CommandHandler("setprice", setprice_command))
            app.add_handler(CommandHandler("stats", stats_command))
            app.add_handler(CommandHandler("unpin", unpin_command))

            # Telegram Stars Payments
            app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
            app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

            # Message Listener (enforces 1-hour pin cycle, zero deletion, skip admins)
            app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, group_gatekeeper_handler))

            # Error handling
            app.add_error_handler(error_handler)

            logger.info("Starting Telegram Star Pin Bot polling...")
            app.run_polling(drop_pending_updates=True)
            logger.warning("run_polling returned. Restarting polling loop in 2s...")
            import time
            time.sleep(2)
        except (KeyboardInterrupt, SystemExit) as sig:
            logger.info("Bot execution received %s. Reconnecting in 3s...", sig)
            import time
            time.sleep(3)
        except Exception as e:
            logger.error("Polling stopped due to error: %s. Reconnecting in 3 seconds...", e)
            import time
            time.sleep(3)


if __name__ == "__main__":
    main()
