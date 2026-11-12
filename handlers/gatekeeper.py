"""
Gatekeeper and Message Listener module.
Enforces:
1. NO message deletion ever.
2. Only 1 pinned message at a time for 1 hour.
3. Subsequent messages during the 1 hour are NOT pinned.
4. After 1 hour, when the next new message arrives, it is pinned for an hour.
5. Admin messages are NEVER pinned.
6. Exact reply: 'Pinned for an hour 📌.'
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from telegram import Update, Message
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from database import (
    touch_chat_activity,
    has_active_pin,
    get_armed_pin,
    consume_armed_pin,
    record_pin,
    get_last_bot_message_id,
    set_last_bot_message_id,
    update_last_reminder,
)
from handlers.pin_handler import is_admin_message, is_user_admin

logger = logging.getLogger(__name__)

PINNED_CONFIRMATION_HTML = 'Pinned for an hour <tg-emoji emoji-id="6275785734511141427">📌</tg-emoji>.'
INACTIVITY_REMINDER_HTML = (
    'Next ad will be pinned for an hour\n'
    '(<tg-emoji emoji-id="5893192487324880883">ℹ️</tg-emoji> Except Admins) '
    '<tg-emoji emoji-id="6275785734511141427">📌</tg-emoji>.'
)


def get_pinned_text(duration_label: str = "an hour") -> str:
    """Format the pinned confirmation message with custom emoji."""
    return f'Pinned for {duration_label} <tg-emoji emoji-id="6275785734511141427">📌</tg-emoji>.'


def get_reminder_text(duration_label: str = "an hour") -> str:
    """Format the reminder message with duration and custom emojis."""
    return (
        f'Next ad will be pinned for {duration_label}\n'
        f'(<tg-emoji emoji-id="5893192487324880883">ℹ️</tg-emoji> Except Admins) '
        f'<tg-emoji emoji-id="6275785734511141427">📌</tg-emoji>.'
    )


async def send_or_replace_reminder(bot, chat_id: int, text: Optional[str] = None) -> Optional[Message]:
    """
    Deletes the bot's previous reminder message in this chat (if any),
    sends the new reminder message, and saves the new message ID.
    """
    if not text:
        text = INACTIVITY_REMINDER_HTML

    prev_id = get_last_bot_message_id(chat_id)
    if prev_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=prev_id)
            logger.info("Deleted bot previous message %s in chat %s", prev_id, chat_id)
        except Exception as e:
            logger.debug("Could not delete bot previous message %s in chat %s: %s", prev_id, chat_id, e)

    try:
        new_msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
        set_last_bot_message_id(chat_id, new_msg.message_id)
        update_last_reminder(chat_id)
        logger.info("Sent reminder message %s in chat %s", new_msg.message_id, chat_id)
        return new_msg
    except Exception as e:
        err_msg = str(e).lower()
        if "forbidden" in err_msg or "kicked" in err_msg or "chat not found" in err_msg:
            logger.warning("Bot no longer has access to chat %s (%s). Deactivating reminders for this chat.", chat_id, e)
            from database import get_connection
            conn = get_connection()
            try:
                with conn:
                    conn.execute("DELETE FROM chat_settings WHERE chat_id = ?", (chat_id,))
            except Exception:
                pass
            finally:
                conn.close()
        else:
            logger.error("Failed to send reminder message in chat %s: %s", chat_id, e)
        return None


async def publish_post_to_group(bot, chat_id: int, draft: Dict[str, Any], duration_hours: int) -> Message:
    """Publishes a user draft into the Telegram group without deleting."""
    m_type = draft.get("message_type", "text")
    file_id = draft.get("file_id")
    text = draft.get("content_text") or ""
    username = draft.get("username")
    fullname = draft.get("user_fullname") or "Member"

    author_mention = f"@{username}" if username else fullname
    badge = f"\n\n⭐️ <b>Paid Pin ({duration_hours}h)</b> • <i>Posted by {author_mention}</i>"

    if m_type == "text":
        return await bot.send_message(
            chat_id=chat_id,
            text=f"{text}{badge}",
            parse_mode=ParseMode.HTML,
        )
    elif m_type == "photo":
        caption = f"{text}{badge}" if text else badge
        return await bot.send_photo(
            chat_id=chat_id,
            photo=file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
    elif m_type == "video":
        caption = f"{text}{badge}" if text else badge
        return await bot.send_video(
            chat_id=chat_id,
            video=file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
    elif m_type == "document":
        caption = f"{text}{badge}" if text else badge
        return await bot.send_document(
            chat_id=chat_id,
            document=file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
    elif m_type == "animation":
        caption = f"{text}{badge}" if text else badge
        return await bot.send_animation(
            chat_id=chat_id,
            animation=file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
    else:
        return await bot.send_message(
            chat_id=chat_id,
            text=f"{text}{badge}",
            parse_mode=ParseMode.HTML,
        )


async def group_gatekeeper_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Monitors all messages in groups.
    Rules:
    1. NEVER deletes any messages.
    2. Admin messages are NEVER pinned.
    3. If there is already an active pin running (within its 1h period), subsequent messages are NOT pinned.
    4. When there is NO active pin (e.g. after the 1 hour period expires), the next incoming non-admin message IS pinned for 1 hour.
    5. Replies: 'Pinned for an hour 📌.'
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not chat or not message or not user:
        return

    # Only inspect groups & supergroups
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    # Update chat activity timestamp for 15-min inactivity tracker
    touch_chat_activity(chat.id, chat.title)

    # Ignore bots and commands
    if user.is_bot:
        return
    if message.text and message.text.startswith("/"):
        return

    # STRICT REQUIREMENT: Admin messages are NEVER pinned (and never deleted).
    # When an admin posts a message, announce the next pin and delete bot's previous message.
    if await is_admin_message(chat.id, message, context.bot):
        armed = get_armed_pin(chat.id)
        duration_label = armed.get("duration_label", "an hour") if armed else "an hour"
        reminder_text = get_reminder_text(duration_label)
        await send_or_replace_reminder(context.bot, chat.id, text=reminder_text)
        return

    # =========================================================================
    # Check if a pin is ALREADY active in this group
    # =========================================================================
    if has_active_pin(chat.id):
        # A message is currently pinned! The next message will NOT be pinned.
        # It remains as a normal message in the chat (never deleted).
        return

    # =========================================================================
    # No pin is currently active (e.g. after 1 hour has passed)
    # PIN THE NEXT MESSAGE FOR 1 HOUR / SPECIFIED DURATION!
    # =========================================================================
    duration_hours = 1
    duration_seconds = 3600
    duration_label = "an hour"
    armed = get_armed_pin(chat.id)
    if armed:
        duration_seconds = armed.get("duration_seconds", 3600)
        duration_label = armed.get("duration_label", "an hour")
        duration_hours = armed.get("duration_hours", max(1, round(duration_seconds / 3600)))
        consume_armed_pin(armed["id"])

    try:
        # Clean up any previous bot reminder message so it doesn't clutter above newly pinned post
        prev_reminder = get_last_bot_message_id(chat.id)
        if prev_reminder:
            try:
                await context.bot.delete_message(chat_id=chat.id, message_id=prev_reminder)
            except Exception:
                pass
            set_last_bot_message_id(chat.id, None)

        await context.bot.pin_chat_message(
            chat_id=chat.id,
            message_id=message.message_id,
            disable_notification=False,
        )

        now_utc = datetime.now(timezone.utc)
        unpin_at = now_utc + timedelta(seconds=duration_seconds)

        record_pin(
            chat_id=chat.id,
            message_id=message.message_id,
            payer_user_id=user.id,
            payer_username=user.username,
            stars_paid=0,
            charge_id="auto_1h_cycle",
            target_author_id=user.id,
            target_author_username=user.username,
            duration_hours=duration_hours,
            duration_seconds=duration_seconds,
            duration_label=duration_label,
            unpin_at=unpin_at.isoformat(),
        )

        # Exact requested confirmation message
        await message.reply_text(
            get_pinned_text(duration_label),
            parse_mode=ParseMode.HTML,
        )
        logger.info(
            "Pinned message %s in chat %s for %s (%ss). Subsequent messages will not be pinned until %s",
            message.message_id,
            chat.id,
            duration_label,
            duration_seconds,
            unpin_at.isoformat(),
        )

    except Exception as e:
        logger.error("Failed to pin message %s in chat %s: %s", message.message_id, chat.id, e)
