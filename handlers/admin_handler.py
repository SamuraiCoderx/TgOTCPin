"""
Handlers for admin group commands: /setprice, /price, /stats, and /unpin.
"""

import logging
from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from config import DEFAULT_STAR_PRICE, MIN_STAR_PRICE, MAX_STAR_PRICE
from database import get_star_price, set_star_price, get_chat_stats
from handlers.pin_handler import is_user_admin

logger = logging.getLogger(__name__)


async def setprice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /setprice <amount>
    Allows group administrators to configure the star cost to pin a message.
    """
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not chat or not user or not message:
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("This command can only be used inside groups.")
        return

    # Check if user is an admin
    if not await is_user_admin(chat.id, user.id, context.bot):
        await message.reply_text("🚫 **Access Denied:** Only group administrators can change the pin price.")
        return

    # Check arguments
    args = context.args
    if not args or len(args) != 1:
        current_price = get_star_price(chat.id, default_price=DEFAULT_STAR_PRICE)
        await message.reply_text(
            f"ℹ️ **Usage:** `/setprice <amount>`\n\n"
            f"Current pin price: **{current_price} ⭐️ Stars**\n"
            f"Allowed range: {MIN_STAR_PRICE} to {MAX_STAR_PRICE} Stars.\n\n"
            f"Example: `/setprice 15`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        new_price = int(args[0])
    except ValueError:
        await message.reply_text("❌ Please enter a valid whole number for the Stars price.")
        return

    if new_price < MIN_STAR_PRICE or new_price > MAX_STAR_PRICE:
        await message.reply_text(
            f"❌ Price must be between {MIN_STAR_PRICE} and {MAX_STAR_PRICE} ⭐️ Stars."
        )
        return

    set_star_price(chat.id, new_price)
    await message.reply_text(
        f"✅ **Pin price updated!**\n\n"
        f"Members will now pay **{new_price} ⭐️ Stars** to pin a message in this group.",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info("Admin %s updated star price in chat %s to %s", user.id, chat.id, new_price)


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /price
    Displays the current pin price in the chat.
    """
    chat = update.effective_chat
    message = update.effective_message

    if not chat or not message:
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text(
            f"⭐️ Default pin price is **{DEFAULT_STAR_PRICE} Stars** per message.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    current_price = get_star_price(chat.id, default_price=DEFAULT_STAR_PRICE)
    await message.reply_text(
        f"⭐️ Current pin price in **{chat.title}**: **{current_price} Stars**.\n\n"
        f"To pin a message, reply to it with `/pin`!",
        parse_mode=ParseMode.MARKDOWN,
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /stats
    Displays total pins and stars collected in the current group.
    """
    chat = update.effective_chat
    message = update.effective_message

    if not chat or not message:
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Statistics are only available inside groups.")
        return

    stats = get_chat_stats(chat.id)
    await message.reply_text(
        f"📊 **Pin Statistics for {chat.title}**\n\n"
        f"📌 **Total Paid Pins:** {stats['total_pins']}\n"
        f"⭐️ **Total Stars Paid:** {stats['total_stars']} XTR",
        parse_mode=ParseMode.MARKDOWN,
    )


async def unpin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /unpin
    Allows administrators to unpin messages.
    If replied to a message, unpins that message. Otherwise unpins the latest pinned message.
    """
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if not chat or not user or not message:
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("This command can only be used inside groups.")
        return

    if not await is_user_admin(chat.id, user.id, context.bot):
        await message.reply_text("🚫 Only administrators can unpin messages.")
        return

    try:
        if message.reply_to_message:
            target_id = message.reply_to_message.message_id
            await context.bot.unpin_chat_message(chat_id=chat.id, message_id=target_id)
            await message.reply_text("✅ Specified message has been unpinned.")
        else:
            await context.bot.unpin_chat_message(chat_id=chat.id)
            await message.reply_text("✅ The top pinned message has been unpinned.")
    except Exception as e:
        logger.error("Failed to unpin message: %s", e)
        await message.reply_text(f"❌ Failed to unpin: {e}")
