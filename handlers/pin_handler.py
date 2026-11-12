"""
Handler for the /pin command (with duration: m for min, h for hour, s for day), /stoppin command,
Telegram Star invoice creation, pre-checkout validation, and successful payment pinning.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List

from telegram import Update, LabeledPrice, Message
from telegram.constants import ChatType, ChatMemberStatus, ParseMode
from telegram.ext import ContextTypes

from config import DEFAULT_STAR_PRICE
from database import (
    get_star_price,
    record_pin,
    get_active_pin,
    mark_pin_inactive,
    arm_next_pin,
    cancel_armed_pin,
    get_pending_post,
    delete_pending_post,
    touch_chat_activity,
)

logger = logging.getLogger(__name__)


def parse_duration(args: List[str]) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    Parses duration formats:
    - 'm' for minutes (e.g. 15m, 30m)
    - 'h' for hours   (e.g. 1h, 2h)
    - 's' for days    (e.g. 1s = 1 day, 2s = 2 days)
    Also supports 'd' as alias for day.
    Defaults to 1 hour (3600 seconds) if no argument given.
    Returns: (total_seconds, duration_label, error_message)
    """
    if not args:
        return 3600, "an hour", None

    raw = " ".join(args).strip().lower()

    unit = None
    value_str = raw

    # Check for minutes: 'minutes', 'minute', 'mins', 'min', 'm'
    for suffix in ("minutes", "minute", "mins", "min", "m"):
        if raw.endswith(suffix):
            unit = "m"
            value_str = raw[: -len(suffix)].strip()
            break

    # Check for days: 'days', 'day', 's', 'd'
    if not unit:
        for suffix in ("days", "day", "s", "d"):
            if raw.endswith(suffix):
                unit = "s"
                value_str = raw[: -len(suffix)].strip()
                break

    # Check for hours: 'hours', 'hour', 'hrs', 'hr', 'h'
    if not unit:
        for suffix in ("hours", "hour", "hrs", "hr", "h"):
            if raw.endswith(suffix):
                unit = "h"
                value_str = raw[: -len(suffix)].strip()
                break

    if not unit:
        unit = "h"

    try:
        val = int(value_str)
    except ValueError:
        return (
            None,
            None,
            "❌ <b>Invalid duration format:</b>\n"
            "• Use <code>m</code> for minutes (e.g. <code>/pin 30m</code>)\n"
            "• Use <code>h</code> for hours (e.g. <code>/pin 1h</code>)\n"
            "• Use <code>s</code> for days (e.g. <code>/pin 1s</code> for 1 day)",
        )

    if val <= 0:
        return None, None, "❌ Duration must be greater than 0."

    if unit == "m":
        if val > 1440:
            return None, None, "❌ Maximum duration in minutes is 1440m (24 hours)."
        total_seconds = val * 60
        label = f"{val} minute" if val == 1 else f"{val} minutes"
    elif unit == "s":
        if val > 30:
            return None, None, "❌ Maximum duration in days is 30s (30 days)."
        total_seconds = val * 86400
        label = "a day" if val == 1 else f"{val} days"
    else:  # 'h'
        if val > 720:
            return None, None, "❌ Maximum duration in hours is 720h."
        total_seconds = val * 3600
        label = "an hour" if val == 1 else f"{val} hours"

    return total_seconds, label, None


def get_pinned_text(duration_label: str = "an hour") -> str:
    """Format the pinned confirmation message with custom emoji."""
    return f'Pinned for {duration_label} <tg-emoji emoji-id="6275785734511141427">📌</tg-emoji>.'


def get_message_link(chat, message_id: int) -> Optional[str]:
    """Generate a direct clickable link to a message in the chat."""
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    chat_str = str(chat.id)
    if chat_str.startswith("-100"):
        internal_id = chat_str[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"
    return None


async def is_user_admin(chat_id: int, user_id: int, bot) -> bool:
    """Check if a specific user_id is an administrator or creator in the chat."""
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception as e:
        logger.warning("Failed to check admin status for user %s in chat %s: %s", user_id, chat_id, e)
        return False


async def is_admin_message(chat_id: int, message: Message, bot) -> bool:
    """
    Check if a message was authored by an admin, creator, or anonymous channel/group.
    Returns True if the message is from an admin, False otherwise.
    """
    if not message:
        return False

    if message.sender_chat is not None:
        return True

    if message.from_user and message.from_user.id == 1087968824:
        return True

    if message.from_user:
        return await is_user_admin(chat_id, message.from_user.id, bot)

    return False


async def pin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles /pin [m for min, h for hour, s for day].
    1. If typed standalone without reply:
       Arms the pin mechanism so the NEXT post is pinned for the duration.
    2. If typed as a reply:
       Pins that specific replied message directly (admin messages are rejected).
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user

    if not chat or not message or not user:
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text(
            "👋 <b>Welcome To @gramsGc Pin Bot</b>\n\n"
            "Use <code>/pin</code> inside your OTC group to set pin duration:\n"
            "• <code>m</code> for minutes (e.g. <code>/pin 30m</code>)\n"
            "• <code>h</code> for hours (e.g. <code>/pin 1h</code>)\n"
            "• <code>s</code> for days (e.g. <code>/pin 1s</code> for 1 day)\n\n"
            "⚠️ <i>Note: Admin messages are never pinned via this bot.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    touch_chat_activity(chat.id, chat.title)

    duration_seconds, duration_label, error_msg = parse_duration(context.args)
    if error_msg:
        await message.reply_text(error_msg, parse_mode=ParseMode.HTML)
        return

    # Check bot permissions
    try:
        bot_member = await context.bot.get_chat_member(chat_id=chat.id, user_id=context.bot.id)
        if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
            await message.reply_text(
                "⚠️ <b>Permission Required:</b> Please make me an Administrator in this group so I can pin messages.",
                parse_mode=ParseMode.HTML,
            )
            return
        if not getattr(bot_member, "can_pin_messages", False):
            await message.reply_text(
                "⚠️ <b>Missing Right:</b> I need the <b>Pin Messages</b> permission to function.\n"
                "Please enable 'Pin Messages' in my administrator permissions.",
                parse_mode=ParseMode.HTML,
            )
            return
    except Exception as e:
        logger.error("Error checking bot admin permissions: %s", e)

    target_message = message.reply_to_message

    # =========================================================================
    # CASE 1: Standalone /pin (ARM NEXT POST PIN)
    # =========================================================================
    if not target_message:
        user_is_admin = await is_user_admin(chat.id, user.id, context.bot)

        if user_is_admin:
            arm_next_pin(
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                duration_seconds=duration_seconds,
                duration_label=duration_label,
                stars_paid=0,
            )
            await message.reply_text(
                f"📌 <b>Next post will be pinned for {duration_label}.</b>\n\n"
                f"• Waiting for the next post in this OTC.\n"
                f"• Admin messages will be skipped.\n"
                f"• Will automatically unpin after {duration_label}.\n\n"
                f"💡 <i>Use <code>/stoppin</code> to cancel.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        # Non-admin requires Stars to arm
        hourly_rate = get_star_price(chat.id, default_price=DEFAULT_STAR_PRICE)
        hours_equiv = max(1, round(duration_seconds / 3600))
        total_stars = max(1, hourly_rate * hours_equiv)
        payload = f"arm_pin:{chat.id}:{user.id}:{duration_seconds}:{duration_label}"

        title = f"📌 Pin Next Post ({duration_label})"
        description = (
            f"Pay {total_stars} ⭐️ Stars to pin the next post in {chat.title} for {duration_label}."
        )

        try:
            await context.bot.send_invoice(
                chat_id=chat.id,
                title=title,
                description=description,
                payload=payload,
                currency="XTR",
                prices=[LabeledPrice(label=f"Pin for {duration_label}", amount=total_stars)],
                provider_token="",
            )
        except Exception as e:
            logger.error("Failed to send Star invoice for arm_pin: %s", e)
            await message.reply_text(f"❌ Failed to generate Star invoice: {e}")
        return

    # =========================================================================
    # CASE 2: Replying /pin to an existing message
    # =========================================================================
    if target_message.from_user and target_message.from_user.is_bot:
        await message.reply_text("❌ Bot messages cannot be pinned.")
        return

    if (
        target_message.pinned_message
        or target_message.new_chat_members
        or target_message.left_chat_member
        or target_message.group_chat_created
        or target_message.supergroup_chat_created
    ):
        await message.reply_text("❌ Service messages cannot be pinned.")
        return

    if await is_admin_message(chat.id, target_message, context.bot):
        await message.reply_text(
            "🚫 <b>Cannot Pin Admin Message</b>\n\n"
            "Admin messages cannot be pinned through paid Star payments.\n"
            "<i>(Admins can pin their own messages directly using Telegram's built-in pin menu).</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    hourly_rate = get_star_price(chat.id, default_price=DEFAULT_STAR_PRICE)
    hours_equiv = max(1, round(duration_seconds / 3600))
    total_star_price = max(1, hourly_rate * hours_equiv)
    target_author_id = target_message.from_user.id if target_message.from_user else 0
    target_author_name = target_message.from_user.first_name if target_message.from_user else "Member"

    content_snippet = target_message.text or target_message.caption or "Media/Attachment"
    if len(content_snippet) > 35:
        content_snippet = content_snippet[:32] + "..."

    payload = f"pin:{chat.id}:{target_message.message_id}:{target_author_id}:{duration_seconds}:{duration_label}"
    title = f"📌 Pin Message ({duration_label})"
    description = (
        f"Pay {total_star_price} ⭐️ Stars to pin {target_author_name}'s message for {duration_label}: \"{content_snippet}\""
    )

    try:
        await context.bot.send_invoice(
            chat_id=chat.id,
            title=title,
            description=description,
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(label=f"Pin for {duration_label} ({total_star_price} Stars)", amount=total_star_price)],
            provider_token="",
            reply_to_message_id=target_message.message_id,
        )
    except Exception as e:
        logger.error("Failed to send Star invoice: %s", e)
        await message.reply_text(f"❌ Failed to generate Star invoice: {e}")


async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles pre-checkout query before charging Telegram Stars."""
    query = update.pre_checkout_query
    payload = query.invoice_payload

    if payload.startswith("arm_pin:"):
        await query.answer(ok=True)
        return

    if payload.startswith("post_pin:"):
        await query.answer(ok=True)
        return

    if not payload.startswith("pin:"):
        await query.answer(ok=False, error_message="Unrecognized invoice payload.")
        return

    parts = payload.split(":")
    if len(parts) < 4:
        await query.answer(ok=False, error_message="Invalid invoice format.")
        return

    target_chat_id = int(parts[1])
    target_author_id = int(parts[3])

    if target_author_id != 0 and await is_user_admin(target_chat_id, target_author_id, context.bot):
        await query.answer(
            ok=False,
            error_message="Action rejected: Admin messages cannot be pinned.",
        )
        return

    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Triggered when Telegram confirms successful payment in Telegram Stars.
    Pins the message and sends: 'Pinned for {duration_label} 📌.'
    """
    message = update.effective_message
    if not message or not message.successful_payment:
        return

    payment = message.successful_payment
    payload = payment.invoice_payload

    payer = message.from_user
    payer_id = payer.id if payer else 0
    payer_username = payer.username if payer else None
    stars_paid = payment.total_amount
    charge_id = payment.telegram_payment_charge_id

    if update.effective_chat:
        touch_chat_activity(update.effective_chat.id, update.effective_chat.title)

    # CASE A: arm_pin: user paid to pin the NEXT post
    if payload.startswith("arm_pin:"):
        parts = payload.split(":")
        target_chat_id = int(parts[1])
        duration_seconds = int(parts[3]) if len(parts) > 3 else 3600
        duration_label = parts[4] if len(parts) > 4 else "an hour"

        arm_next_pin(
            chat_id=target_chat_id,
            user_id=payer_id,
            username=payer_username,
            duration_seconds=duration_seconds,
            duration_label=duration_label,
            stars_paid=stars_paid,
        )

        await message.reply_text(
            f"⭐️ <b>Payment of {stars_paid} Stars Received!</b>\n\n"
            f"📌 <b>Next post will be pinned for {duration_label}.</b>\n\n"
            f"• Send your post now.\n"
            f"• Admin messages will be skipped.\n"
            f"• Will automatically unpin after {duration_label}.\n\n"
            f"💡 <i>Use <code>/stoppin</code> to cancel.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # CASE B: pin: replied to specific message
    if not payload.startswith("pin:"):
        return

    parts = payload.split(":")
    target_chat_id = int(parts[1])
    target_message_id = int(parts[2])
    target_author_id = int(parts[3]) if len(parts) > 3 else 0
    duration_seconds = int(parts[4]) if len(parts) > 4 else 3600
    duration_label = parts[5] if len(parts) > 5 else "an hour"

    if target_author_id != 0 and await is_user_admin(target_chat_id, target_author_id, context.bot):
        await message.reply_text("⚠️ <b>Warning:</b> Admin messages cannot be pinned.", parse_mode=ParseMode.HTML)
        return

    now_utc = datetime.now(timezone.utc)
    unpin_at = now_utc + timedelta(seconds=duration_seconds)

    try:
        await context.bot.pin_chat_message(
            chat_id=target_chat_id,
            message_id=target_message_id,
            disable_notification=False,
        )

        record_pin(
            chat_id=target_chat_id,
            message_id=target_message_id,
            payer_user_id=payer_id,
            payer_username=payer_username,
            stars_paid=stars_paid,
            charge_id=charge_id,
            target_author_id=target_author_id,
            duration_hours=max(1, round(duration_seconds / 3600)),
            duration_seconds=duration_seconds,
            duration_label=duration_label,
            unpin_at=unpin_at.isoformat(),
        )

        # Exact requested confirmation message
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=get_pinned_text(duration_label),
            reply_to_message_id=target_message_id,
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        logger.error("Failed to pin chat message %s in chat %s: %s", target_message_id, target_chat_id, e)
        await message.reply_text(f"⚠️ Failed to pin message: {e}")


async def stoppin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /stoppin
    Stops the pin mechanism:
    1. Cancels any armed pin waiting for the next post.
    2. Unpins active pinned message in the group.
    """
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not chat or not user or not message:
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("This command can only be used inside groups.")
        return

    touch_chat_activity(chat.id, chat.title)
    user_is_admin = await is_user_admin(chat.id, user.id, context.bot)

    # 1. Cancel waiting armed pin
    if cancel_armed_pin(chat.id):
        await message.reply_text(
            "🛑 <b>Pin Mechanism Stopped.</b>\n\n"
            "The waiting pin has been cancelled. Next posts will not be pinned.",
            parse_mode=ParseMode.HTML,
        )
        return

    # 2. Unpin active pinned message
    target_msg_id = None
    if message.reply_to_message:
        target_msg_id = message.reply_to_message.message_id

    active_pin = get_active_pin(chat.id, message_id=target_msg_id)

    if not active_pin:
        if user_is_admin:
            try:
                if target_msg_id:
                    await context.bot.unpin_chat_message(chat_id=chat.id, message_id=target_msg_id)
                else:
                    await context.bot.unpin_chat_message(chat_id=chat.id)
                await message.reply_text("✅ Message has been unpinned.", parse_mode=ParseMode.HTML)
                return
            except Exception as e:
                logger.warning("Admin unpin failed: %s", e)

        await message.reply_text(
            "ℹ️ There is no active Star pin or armed pin mechanism in this group.",
            parse_mode=ParseMode.HTML,
        )
        return

    is_payer = active_pin["payer_user_id"] == user.id
    if not (user_is_admin or is_payer):
        payer_disp = f"@{active_pin['payer_username']}" if active_pin["payer_username"] else "the payer"
        await message.reply_text(
            f"🚫 <b>Access Denied:</b> Only group administrators or {payer_disp} can stop this pin.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        await context.bot.unpin_chat_message(
            chat_id=chat.id,
            message_id=active_pin["message_id"],
        )
    except Exception as e:
        logger.warning("Could not unpin message %s in chat %s: %s", active_pin["message_id"], chat.id, e)

    mark_pin_inactive(record_id=active_pin["id"])

    await message.reply_text(
        "🛑 <b>Pin Stopped Successfully.</b>\n\n"
        "The message has been unpinned from the group.",
        parse_mode=ParseMode.HTML,
    )

    try:
        from handlers.gatekeeper import send_or_replace_reminder
        await send_or_replace_reminder(context.bot, chat.id)
    except Exception as e:
        logger.warning("Could not send reminder after stoppin: %s", e)
