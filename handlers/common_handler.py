"""
Common handlers for /start, /help, /dashboard, and interactive dashboard callback buttons.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from telegram.ext import ContextTypes

DASHBOARD_TEXT = (
    '<tg-emoji emoji-id="6275785734511141427">📌</tg-emoji> Automatic Pin Service.\n\n'
    '✪ To start pin mechanism in your otc:\n\n'
    '• Command : /pin 1h\n'
    'Give this command inside your otc.\n\n'
    '→ /pin 1h - Next post will be pinned for an hour.\n'
    'You can specify the hour(s) in command like /pin 1h for 1 hour and /pin 2h for 2 hours and so on.\n\n'
    '✪ To stop pin mechanism in your otc:\n\n'
    '• Command : /stoppin\n'
    'Give this command inside your otc to stop the pin mechanism\n\n'
    '<tg-emoji emoji-id="5893203503915996356">⚠️</tg-emoji> Note: The bot can only function in your OTC group '
    'if it has permission to pin and delete messages.'
)


def get_welcome_markup() -> InlineKeyboardMarkup:
    """Builds inline keyboard with green 'see dash board' button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 see dash board", callback_data="see_dashboard")]
    ])


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command with the requested welcome message and green button."""
    message = update.effective_message
    if not message:
        return

    await message.reply_text(
        "Welcome To @gramsGc Pin Bot",
        reply_markup=get_welcome_markup(),
    )


async def dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered when the user taps 'see dash board' button."""
    query = update.callback_query
    if not query:
        return

    await query.answer()

    bot_info = await context.bot.get_me()
    add_group_url = f"https://t.me/{bot_info.username}?startgroup=true"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Bot to OTC Group", url=add_group_url)],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_welcome")],
    ])

    await query.edit_message_text(
        text=DASHBOARD_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def back_to_welcome_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Returns to the welcome message."""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    await query.edit_message_text(
        text="Welcome To @gramsGc Pin Bot",
        reply_markup=get_welcome_markup(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help or /dashboard command by displaying the official OTC service guide."""
    message = update.effective_message
    if not message:
        return

    bot_info = await context.bot.get_me()
    add_group_url = f"https://t.me/{bot_info.username}?startgroup=true"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Bot to OTC Group", url=add_group_url)]
    ])

    await message.reply_text(
        text=DASHBOARD_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
