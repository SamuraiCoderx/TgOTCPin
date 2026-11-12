"""
Database layer for managing chat settings, Star pricing, armed pins,
pending posts, pin payment history, and flexible duration (m for min, h for hour, s for day) tracking.
Uses standard library sqlite3 for lightweight, zero-dependency persistence.
"""

import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from config import DATABASE_PATH, DEFAULT_STAR_PRICE

logger = logging.getLogger(__name__)


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    target = db_path if db_path is not None else DATABASE_PATH
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DATABASE_PATH) -> None:
    """Initialize database tables and run schema migrations."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT,
                    star_price INTEGER NOT NULL DEFAULT 10,
                    paid_only_mode INTEGER NOT NULL DEFAULT 0,
                    last_activity_at TIMESTAMP,
                    last_reminder_at TIMESTAMP,
                    last_bot_message_id INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pin_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    payer_user_id INTEGER NOT NULL,
                    payer_username TEXT,
                    target_author_id INTEGER,
                    target_author_username TEXT,
                    stars_paid INTEGER NOT NULL,
                    telegram_payment_charge_id TEXT,
                    duration_hours INTEGER DEFAULT 1,
                    duration_seconds INTEGER DEFAULT 3600,
                    duration_label TEXT DEFAULT 'an hour',
                    unpin_at TIMESTAMP,
                    is_active INTEGER DEFAULT 1,
                    pinned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS armed_pins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    armed_by_user_id INTEGER NOT NULL,
                    armed_by_username TEXT,
                    duration_hours INTEGER NOT NULL DEFAULT 1,
                    duration_seconds INTEGER NOT NULL DEFAULT 3600,
                    duration_label TEXT NOT NULL DEFAULT 'an hour',
                    stars_paid INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'armed',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    user_fullname TEXT,
                    message_type TEXT NOT NULL,
                    file_id TEXT,
                    content_text TEXT,
                    duration_hours INTEGER DEFAULT 1,
                    duration_seconds INTEGER DEFAULT 3600,
                    duration_label TEXT DEFAULT 'an hour',
                    invoice_message_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Migrations
            cursor.execute("PRAGMA table_info(chat_settings)")
            settings_cols = {col[1] for col in cursor.fetchall()}
            if "title" not in settings_cols:
                cursor.execute("ALTER TABLE chat_settings ADD COLUMN title TEXT")
            if "paid_only_mode" not in settings_cols:
                cursor.execute("ALTER TABLE chat_settings ADD COLUMN paid_only_mode INTEGER NOT NULL DEFAULT 0")
            if "last_activity_at" not in settings_cols:
                cursor.execute("ALTER TABLE chat_settings ADD COLUMN last_activity_at TIMESTAMP")
            if "last_reminder_at" not in settings_cols:
                cursor.execute("ALTER TABLE chat_settings ADD COLUMN last_reminder_at TIMESTAMP")
            if "last_bot_message_id" not in settings_cols:
                cursor.execute("ALTER TABLE chat_settings ADD COLUMN last_bot_message_id INTEGER")

            cursor.execute("PRAGMA table_info(pin_records)")
            pin_cols = {col[1] for col in cursor.fetchall()}
            if "duration_hours" not in pin_cols:
                cursor.execute("ALTER TABLE pin_records ADD COLUMN duration_hours INTEGER DEFAULT 1")
            if "duration_seconds" not in pin_cols:
                cursor.execute("ALTER TABLE pin_records ADD COLUMN duration_seconds INTEGER DEFAULT 3600")
            if "duration_label" not in pin_cols:
                cursor.execute("ALTER TABLE pin_records ADD COLUMN duration_label TEXT DEFAULT 'an hour'")
            if "unpin_at" not in pin_cols:
                cursor.execute("ALTER TABLE pin_records ADD COLUMN unpin_at TIMESTAMP")
            if "is_active" not in pin_cols:
                cursor.execute("ALTER TABLE pin_records ADD COLUMN is_active INTEGER DEFAULT 1")

            cursor.execute("PRAGMA table_info(armed_pins)")
            armed_cols = {col[1] for col in cursor.fetchall()}
            if "duration_seconds" not in armed_cols:
                cursor.execute("ALTER TABLE armed_pins ADD COLUMN duration_seconds INTEGER DEFAULT 3600")
            if "duration_label" not in armed_cols:
                cursor.execute("ALTER TABLE armed_pins ADD COLUMN duration_label TEXT DEFAULT 'an hour'")

        logger.info("Database initialized successfully at %s", db_path)
    finally:
        conn.close()


def has_active_pin(chat_id: int, db_path: str = DATABASE_PATH) -> bool:
    """Returns True if there is currently an unexpired, active pin in the group."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            SELECT id FROM pin_records
            WHERE chat_id = ? AND is_active = 1 AND unpin_at IS NOT NULL AND unpin_at > ?
            LIMIT 1
            """,
            (chat_id, now_iso),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def touch_chat_activity(chat_id: int, title: Optional[str] = None, db_path: str = DATABASE_PATH) -> None:
    """Updates the last_activity_at timestamp for a chat to now."""
    clean_title = str(title) if (title is not None and isinstance(title, str)) else None
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                """
                INSERT INTO chat_settings (chat_id, title, last_activity_at, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = coalesce(excluded.title, chat_settings.title),
                    last_activity_at = excluded.last_activity_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, clean_title, now_iso),
            )
    finally:
        conn.close()


def update_last_reminder(chat_id: int, db_path: str = DATABASE_PATH) -> None:
    """Records the timestamp of the last inactivity reminder."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "UPDATE chat_settings SET last_reminder_at = ? WHERE chat_id = ?",
                (now_iso, chat_id),
            )
    finally:
        conn.close()


def get_last_bot_message_id(chat_id: int, db_path: str = DATABASE_PATH) -> Optional[int]:
    """Get the message ID of the bot's last reminder message in this chat."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT last_bot_message_id FROM chat_settings WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if row and row["last_bot_message_id"] is not None:
            return int(row["last_bot_message_id"])
        return None
    finally:
        conn.close()


def set_last_bot_message_id(chat_id: int, message_id: Optional[int], db_path: str = DATABASE_PATH) -> None:
    """Save or clear the last bot message ID for a chat."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                """
                INSERT INTO chat_settings (chat_id, last_bot_message_id, last_reminder_at, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    last_bot_message_id = excluded.last_bot_message_id,
                    last_reminder_at = coalesce(excluded.last_reminder_at, chat_settings.last_reminder_at),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, message_id, now_iso),
            )
    finally:
        conn.close()


def get_inactive_groups(threshold_minutes: int = 5, db_path: str = DATABASE_PATH) -> List[Dict[str, Any]]:
    """
    Returns groups that need a reminder (every threshold_minutes):
    - No active pin is currently running.
    - Either no reminder has been sent yet, or the last reminder was sent >= threshold_minutes ago.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(minutes=threshold_minutes)).isoformat()
        now_iso = now.isoformat()

        cursor.execute(
            """
            SELECT c.chat_id, c.title, c.last_activity_at, c.last_reminder_at
            FROM chat_settings c
            WHERE (c.last_reminder_at IS NULL OR c.last_reminder_at <= ?)
              AND NOT EXISTS (
                  SELECT 1 FROM pin_records p
                  WHERE p.chat_id = c.chat_id AND p.is_active = 1 AND p.unpin_at > ?
              )
            """,
            (cutoff, now_iso),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def arm_next_pin(
    chat_id: int,
    user_id: int,
    username: Optional[str],
    duration_seconds: int = 3600,
    duration_label: str = "an hour",
    stars_paid: int = 0,
    db_path: str = DATABASE_PATH,
) -> int:
    """Arms the group so the next post is pinned for duration_seconds."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE armed_pins SET status = 'cancelled' WHERE chat_id = ? AND status = 'armed'", (chat_id,))
            duration_hours = max(1, round(duration_seconds / 3600))
            cursor.execute(
                """
                INSERT INTO armed_pins (chat_id, armed_by_user_id, armed_by_username, duration_hours, duration_seconds, duration_label, stars_paid, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'armed')
                """,
                (chat_id, user_id, username, duration_hours, duration_seconds, duration_label, stars_paid),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_armed_pin(chat_id: int, db_path: str = DATABASE_PATH) -> Optional[Dict[str, Any]]:
    """Fetch the currently active armed pin for a group."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM armed_pins
            WHERE chat_id = ? AND status = 'armed'
            ORDER BY id DESC LIMIT 1
            """,
            (chat_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def consume_armed_pin(armed_pin_id: int, db_path: str = DATABASE_PATH) -> None:
    """Mark an armed pin as consumed once the next post is pinned."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE armed_pins SET status = 'consumed' WHERE id = ?", (armed_pin_id,))
    finally:
        conn.close()


def cancel_armed_pin(chat_id: int, db_path: str = DATABASE_PATH) -> bool:
    """Cancel any waiting armed pin in a group. Returns True if any was cancelled."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE armed_pins SET status = 'cancelled' WHERE chat_id = ? AND status = 'armed'", (chat_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()


def save_chat_info(chat_id: int, title: Optional[str], db_path: str = DATABASE_PATH) -> None:
    """Save or update chat information and touch activity."""
    touch_chat_activity(chat_id, title, db_path=db_path)


def get_known_groups(db_path: str = DATABASE_PATH) -> List[Dict[str, Any]]:
    """Fetch all known groups where the bot is registered."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, title, star_price, paid_only_mode FROM chat_settings ORDER BY updated_at DESC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def is_paid_only_enabled(chat_id: int, default_val: bool = False, db_path: str = DATABASE_PATH) -> bool:
    """Check if Paid-Only mode is enabled for a chat (default: False)."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT paid_only_mode FROM chat_settings WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if row is not None and row["paid_only_mode"] is not None:
            return bool(row["paid_only_mode"])
        return default_val
    finally:
        conn.close()


def set_paid_only_mode(chat_id: int, enabled: bool, db_path: str = DATABASE_PATH) -> None:
    """Enable or disable paid-only mode for a chat."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            val = 1 if enabled else 0
            cursor.execute(
                """
                INSERT INTO chat_settings (chat_id, paid_only_mode, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    paid_only_mode = ?,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, val, val),
            )
    finally:
        conn.close()


def get_star_price(chat_id: int, default_price: int = DEFAULT_STAR_PRICE, db_path: str = DATABASE_PATH) -> int:
    """Get the current hourly star price configured for a chat."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT star_price FROM chat_settings WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if row and row["star_price"]:
            return int(row["star_price"])
        return default_price
    finally:
        conn.close()


def set_star_price(chat_id: int, price: int, db_path: str = DATABASE_PATH) -> None:
    """Set the hourly star price for a chat."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO chat_settings (chat_id, star_price, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    star_price = excluded.star_price,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, price),
            )
    finally:
        conn.close()


def save_pending_post(
    chat_id: int,
    user_id: int,
    username: Optional[str],
    user_fullname: Optional[str],
    message_type: str,
    file_id: Optional[str],
    content_text: Optional[str],
    duration_hours: int = 1,
    db_path: str = DATABASE_PATH,
) -> int:
    """Store an unpaid user post awaiting Star payment."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO pending_posts (
                    chat_id, user_id, username, user_fullname, message_type,
                    file_id, content_text, duration_hours, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    chat_id,
                    user_id,
                    username,
                    user_fullname,
                    message_type,
                    file_id,
                    content_text,
                    duration_hours,
                ),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_pending_post(draft_id: int, db_path: str = DATABASE_PATH) -> Optional[Dict[str, Any]]:
    """Retrieve pending post by its ID."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pending_posts WHERE id = ?", (draft_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_pending_post_invoice(draft_id: int, invoice_message_id: int, db_path: str = DATABASE_PATH) -> None:
    """Record the generated invoice message ID for clean deletion later."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE pending_posts SET invoice_message_id = ? WHERE id = ?",
                (invoice_message_id, draft_id),
            )
    finally:
        conn.close()


def update_pending_post_duration(draft_id: int, duration_hours: int, db_path: str = DATABASE_PATH) -> None:
    """Update duration hours on a pending post."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE pending_posts SET duration_hours = ? WHERE id = ?",
                (duration_hours, draft_id),
            )
    finally:
        conn.close()


def delete_pending_post(draft_id: int, db_path: str = DATABASE_PATH) -> None:
    """Delete a pending post draft."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM pending_posts WHERE id = ?", (draft_id,))
    finally:
        conn.close()


def record_pin(
    chat_id: int,
    message_id: int,
    payer_user_id: int,
    payer_username: Optional[str],
    stars_paid: int,
    charge_id: str,
    target_author_id: Optional[int] = None,
    target_author_username: Optional[str] = None,
    duration_hours: int = 1,
    duration_seconds: int = 3600,
    duration_label: str = "an hour",
    unpin_at: Optional[str] = None,
    db_path: str = DATABASE_PATH,
) -> int:
    """Record a successful pin transaction."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO pin_records (
                    chat_id, message_id, payer_user_id, payer_username,
                    target_author_id, target_author_username, stars_paid,
                    telegram_payment_charge_id, duration_hours, duration_seconds,
                    duration_label, unpin_at, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    chat_id,
                    message_id,
                    payer_user_id,
                    payer_username,
                    target_author_id,
                    target_author_username,
                    stars_paid,
                    charge_id,
                    duration_hours,
                    duration_seconds,
                    duration_label,
                    unpin_at,
                ),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_expired_pins(now_iso: str, db_path: str = DATABASE_PATH) -> List[Dict[str, Any]]:
    """Fetch all active pin records that have expired."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, chat_id, message_id, payer_user_id, payer_username,
                   target_author_id, stars_paid, duration_hours, duration_seconds,
                   duration_label, unpin_at
            FROM pin_records
            WHERE is_active = 1 AND unpin_at IS NOT NULL AND unpin_at <= ?
            """,
            (now_iso,),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def mark_pin_inactive(
    record_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    message_id: Optional[int] = None,
    db_path: str = DATABASE_PATH,
) -> None:
    """Mark one or more pin records as inactive."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            if record_id is not None:
                cursor.execute("UPDATE pin_records SET is_active = 0 WHERE id = ?", (record_id,))
            elif chat_id is not None and message_id is not None:
                cursor.execute(
                    "UPDATE pin_records SET is_active = 0 WHERE chat_id = ? AND message_id = ?",
                    (chat_id, message_id),
                )
            elif chat_id is not None:
                cursor.execute("UPDATE pin_records SET is_active = 0 WHERE chat_id = ?", (chat_id,))
    finally:
        conn.close()


def get_active_pin(
    chat_id: int,
    message_id: Optional[int] = None,
    db_path: str = DATABASE_PATH,
) -> Optional[Dict[str, Any]]:
    """Retrieve an active pin for a specific message or the latest unexpired pin in the chat."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        now_iso = datetime.now(timezone.utc).isoformat()
        if message_id is not None:
            cursor.execute(
                """
                SELECT id, chat_id, message_id, payer_user_id, payer_username,
                       target_author_id, stars_paid, duration_hours, duration_seconds,
                       duration_label, unpin_at, pinned_at
                FROM pin_records
                WHERE chat_id = ? AND message_id = ? AND is_active = 1 AND unpin_at > ?
                ORDER BY id DESC LIMIT 1
                """,
                (chat_id, message_id, now_iso),
            )
        else:
            cursor.execute(
                """
                SELECT id, chat_id, message_id, payer_user_id, payer_username,
                       target_author_id, stars_paid, duration_hours, duration_seconds,
                       duration_label, unpin_at, pinned_at
                FROM pin_records
                WHERE chat_id = ? AND is_active = 1 AND unpin_at > ?
                ORDER BY id DESC LIMIT 1
                """,
                (chat_id, now_iso),
            )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_chat_stats(chat_id: int, db_path: str = DATABASE_PATH) -> Dict[str, Any]:
    """Get pin statistics for a specific chat."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS total_pins, COALESCE(SUM(stars_paid), 0) AS total_stars
            FROM pin_records
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        row = cursor.fetchone()
        return {
            "total_pins": row["total_pins"] if row else 0,
            "total_stars": row["total_stars"] if row else 0,
        }
    finally:
        conn.close()
