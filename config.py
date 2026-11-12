"""
Configuration settings for the Telegram Star Pin Bot.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Bot API Token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9151651281:AAEKzJQdmCs_bLFIhHcr_jpVRPayTZtWc7g")

# Default price in Telegram Stars (XTR) to pin a message
DEFAULT_STAR_PRICE = int(os.getenv("DEFAULT_STAR_PRICE", "10"))

# Database path
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "bot_data.db"))

# Pricing constraints (in Telegram Stars)
MIN_STAR_PRICE = 1
MAX_STAR_PRICE = 10000
