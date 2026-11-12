"""
Comprehensive test suite for Telegram Star Pin Bot.
Verifies:
1. First message is pinned for 1 hour with "Pinned for an hour 📌."
2. Messages during the 1 hour are NOT pinned and NOT deleted.
3. After 1 hour, the next message IS pinned for 1 hour.
4. Admin messages are NEVER pinned.
5. Inactivity reminder every 15 minutes.
6. /stoppin command.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.constants import ChatMemberStatus, ChatType

import database
from handlers.pin_handler import (
    is_user_admin,
    is_admin_message,
    parse_duration,
    get_pinned_text,
    pin_command,
    stoppin_command,
)
from handlers.gatekeeper import (
    group_gatekeeper_handler,
    PINNED_CONFIRMATION_HTML,
    INACTIVITY_REMINDER_HTML,
)

TEST_DB_PATH = "test_bot_data.db"
ORIGINAL_DB_PATH = database.DATABASE_PATH


def setUpModule():
    database.init_db()  # Ensure default bot_data.db is migrated
    database.DATABASE_PATH = TEST_DB_PATH
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass
    database.init_db(TEST_DB_PATH)


def tearDownModule():
    database.DATABASE_PATH = ORIGINAL_DB_PATH
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass


class TestDatabase(unittest.TestCase):
    def test_default_star_price(self):
        price = database.get_star_price(chat_id=-100123456, default_price=10, db_path=TEST_DB_PATH)
        self.assertEqual(price, 10)

    def test_set_and_get_star_price(self):
        chat_id = -100987654
        database.set_star_price(chat_id=chat_id, price=25, db_path=TEST_DB_PATH)
        price = database.get_star_price(chat_id=chat_id, default_price=10, db_path=TEST_DB_PATH)
        self.assertEqual(price, 25)

    def test_has_active_pin(self):
        chat_id = -100888999
        self.assertFalse(database.has_active_pin(chat_id, db_path=TEST_DB_PATH))

        future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        database.record_pin(
            chat_id=chat_id,
            message_id=501,
            payer_user_id=1,
            payer_username="u1",
            stars_paid=0,
            charge_id="test",
            duration_hours=1,
            unpin_at=future_time,
            db_path=TEST_DB_PATH,
        )
        self.assertTrue(database.has_active_pin(chat_id, db_path=TEST_DB_PATH))

        # After unpinning (marking inactive)
        database.mark_pin_inactive(chat_id=chat_id, db_path=TEST_DB_PATH)
        self.assertFalse(database.has_active_pin(chat_id, db_path=TEST_DB_PATH))


class TestParseDuration(unittest.TestCase):
    def test_default_duration(self):
        secs, label, err = parse_duration([])
        self.assertIsNone(err)
        self.assertEqual(secs, 3600)
        self.assertEqual(label, "an hour")

    def test_minutes_duration(self):
        secs, label, err = parse_duration(["15m"])
        self.assertIsNone(err)
        self.assertEqual(secs, 900)
        self.assertEqual(label, "15 minutes")

        secs, label, err = parse_duration(["1m"])
        self.assertIsNone(err)
        self.assertEqual(secs, 60)
        self.assertEqual(label, "1 minute")

    def test_hours_duration(self):
        secs, label, err = parse_duration(["1h"])
        self.assertIsNone(err)
        self.assertEqual(secs, 3600)
        self.assertEqual(label, "an hour")

        secs, label, err = parse_duration(["2h"])
        self.assertIsNone(err)
        self.assertEqual(secs, 7200)
        self.assertEqual(label, "2 hours")

    def test_days_duration(self):
        # 's' is day as requested by user
        secs, label, err = parse_duration(["1s"])
        self.assertIsNone(err)
        self.assertEqual(secs, 86400)
        self.assertEqual(label, "a day")

        secs, label, err = parse_duration(["2s"])
        self.assertIsNone(err)
        self.assertEqual(secs, 172800)
        self.assertEqual(label, "2 days")

    def test_invalid_duration(self):
        secs, label, err = parse_duration(["abc"])
        self.assertIsNotNone(err)
        self.assertIsNone(secs)

    def test_pinned_text_formatting(self):
        self.assertIn("Pinned for an hour", get_pinned_text("an hour"))
        self.assertIn("Pinned for 15 minutes", get_pinned_text("15 minutes"))
        self.assertIn("Pinned for a day", get_pinned_text("a day"))
        self.assertIn('emoji-id="6275785734511141427"', get_pinned_text("a day"))


class TestOneHourPinCycle(unittest.IsolatedAsyncioTestCase):
    async def test_first_message_pinned_for_an_hour_no_deletion(self):
        """First non-admin message when no pin is active is pinned for 1 hour without deletion."""
        chat_id = -100111222
        update = MagicMock()
        chat = MagicMock()
        chat.type = ChatType.SUPERGROUP
        chat.id = chat_id
        chat.title = "OTC Test Group"
        update.effective_chat = chat

        msg = MagicMock()
        msg.message_id = 1001
        msg.text = "First member ad"
        msg.delete = AsyncMock()
        msg.reply_text = AsyncMock()
        update.effective_message = msg

        user = MagicMock()
        user.is_bot = False
        user.id = 1234
        user.username = "member1"
        update.effective_user = user

        context = MagicMock()
        context.bot = AsyncMock()

        # Non-admin mock
        with patch("handlers.gatekeeper.is_admin_message", return_value=False), \
             patch("handlers.gatekeeper.has_active_pin", return_value=False):
            await group_gatekeeper_handler(update, context)

        # MUST NOT be deleted
        msg.delete.assert_not_called()
        # MUST be pinned
        context.bot.pin_chat_message.assert_called_once_with(
            chat_id=chat_id,
            message_id=1001,
            disable_notification=False,
        )
        # Reply MUST be 'Pinned for an hour 📌.'
        msg.reply_text.assert_called_once()
        self.assertIn("Pinned for an hour", msg.reply_text.call_args[0][0])
        self.assertIn('emoji-id="6275785734511141427"', msg.reply_text.call_args[0][0])

    async def test_subsequent_message_during_1_hour_is_NOT_pinned_and_NOT_deleted(self):
        """While a pin is active during the 1 hour, subsequent messages are neither pinned nor deleted."""
        chat_id = -100111222
        update = MagicMock()
        chat = MagicMock()
        chat.type = ChatType.SUPERGROUP
        chat.id = chat_id
        chat.title = "OTC Test Group"
        update.effective_chat = chat

        msg = MagicMock()
        msg.message_id = 1002
        msg.text = "Second message within 1 hour"
        msg.delete = AsyncMock()
        msg.reply_text = AsyncMock()
        update.effective_message = msg

        user = MagicMock()
        user.is_bot = False
        user.id = 5678
        user.username = "member2"
        update.effective_user = user

        context = MagicMock()
        context.bot = AsyncMock()

        # Active pin running mock
        with patch("handlers.gatekeeper.is_admin_message", return_value=False), \
             patch("handlers.gatekeeper.has_active_pin", return_value=True):
            await group_gatekeeper_handler(update, context)

        # MUST NOT be deleted
        msg.delete.assert_not_called()
        # MUST NOT be pinned
        context.bot.pin_chat_message.assert_not_called()
        msg.reply_text.assert_not_called()

    async def test_admin_message_is_never_pinned_or_deleted(self):
        """Admin messages are never pinned and never deleted. Bot deletes previous reminder and posts fresh reminder."""
        chat_id = -100111222
        update = MagicMock()
        chat = MagicMock()
        chat.type = ChatType.SUPERGROUP
        chat.id = chat_id
        chat.title = "OTC Test Group"
        update.effective_chat = chat

        msg = MagicMock()
        msg.message_id = 1003
        msg.text = "Admin announcement"
        msg.delete = AsyncMock()
        msg.reply_text = AsyncMock()
        update.effective_message = msg

        user = MagicMock()
        user.is_bot = False
        user.id = 9999
        user.username = "admin"
        update.effective_user = user

        context = MagicMock()
        context.bot = AsyncMock()
        sent_bot_msg = MagicMock()
        sent_bot_msg.message_id = 8888
        context.bot.send_message = AsyncMock(return_value=sent_bot_msg)
        context.bot.delete_message = AsyncMock()

        # Mock previous bot message ID 7777 exists in chat
        with patch("handlers.gatekeeper.is_admin_message", return_value=True), \
             patch("handlers.gatekeeper.get_last_bot_message_id", return_value=7777), \
             patch("handlers.gatekeeper.set_last_bot_message_id") as mock_set_last:
            await group_gatekeeper_handler(update, context)

        # Admin message itself MUST NOT be deleted
        msg.delete.assert_not_called()
        # Admin message MUST NOT be pinned
        context.bot.pin_chat_message.assert_not_called()

        # Bot MUST delete its previous message (7777)
        context.bot.delete_message.assert_called_once_with(chat_id=chat_id, message_id=7777)

        # Bot MUST send the requested reminder
        context.bot.send_message.assert_called_once()
        sent_text = context.bot.send_message.call_args[1]["text"]
        self.assertIn("Next ad will be pinned for an hour", sent_text)
        self.assertIn("Except Admins", sent_text)
        self.assertIn('emoji-id="6275785734511141427"', sent_text)
        self.assertIn('emoji-id="5893192487324880883"', sent_text)

        # Bot MUST record its new message ID (8888)
        mock_set_last.assert_called_once_with(chat_id, 8888)

    async def test_armed_pin_with_custom_duration(self):
        """When /pin 15m or /pin 1s was set, the next message is pinned with that duration label."""
        chat_id = -100111333
        update = MagicMock()
        chat = MagicMock()
        chat.type = ChatType.SUPERGROUP
        chat.id = chat_id
        chat.title = "OTC Test Group"
        update.effective_chat = chat

        msg = MagicMock()
        msg.message_id = 2001
        msg.text = "Second member ad"
        msg.delete = AsyncMock()
        msg.reply_text = AsyncMock()
        update.effective_message = msg

        user = MagicMock()
        user.is_bot = False
        user.id = 4321
        user.username = "member2"
        update.effective_user = user

        context = MagicMock()
        context.bot = AsyncMock()

        armed_mock = {
            "id": 1,
            "chat_id": chat_id,
            "duration_seconds": 900,
            "duration_label": "15 minutes",
            "duration_hours": 1,
        }

        with patch("handlers.gatekeeper.is_admin_message", return_value=False), \
             patch("handlers.gatekeeper.has_active_pin", return_value=False), \
             patch("handlers.gatekeeper.get_armed_pin", return_value=armed_mock), \
             patch("handlers.gatekeeper.consume_armed_pin") as mock_consume, \
             patch("handlers.gatekeeper.record_pin") as mock_record:
            await group_gatekeeper_handler(update, context)

        mock_consume.assert_called_once_with(1)
        mock_record.assert_called_once()
        self.assertEqual(mock_record.call_args[1]["duration_seconds"], 900)
        self.assertEqual(mock_record.call_args[1]["duration_label"], "15 minutes")

        msg.reply_text.assert_called_once()
        self.assertIn("Pinned for 15 minutes", msg.reply_text.call_args[0][0])
        self.assertIn('emoji-id="6275785734511141427"', msg.reply_text.call_args[0][0])


class TestInactivityReminder(unittest.TestCase):
    def test_inactivity_texts(self):
        self.assertIn("Next ad will be pinned for an hour", INACTIVITY_REMINDER_HTML)
        self.assertIn("Except Admins", INACTIVITY_REMINDER_HTML)
        self.assertIn('emoji-id="6275785734511141427"', INACTIVITY_REMINDER_HTML)
        self.assertIn('emoji-id="5893192487324880883"', INACTIVITY_REMINDER_HTML)

    def test_get_inactive_groups_5_minute_cycle(self):
        chat_id_inactive = -100999001
        chat_id_active = -100999002

        database.save_chat_info(chat_id=chat_id_inactive, title="Inactive Group", db_path=TEST_DB_PATH)
        database.save_chat_info(chat_id=chat_id_active, title="Active Group", db_path=TEST_DB_PATH)

        # Set last reminder 6 minutes ago for inactive group
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        conn = database.get_connection(TEST_DB_PATH)
        try:
            with conn:
                conn.execute(
                    "UPDATE chat_settings SET last_reminder_at = ? WHERE chat_id = ?",
                    (old_time, chat_id_inactive),
                )
        finally:
            conn.close()

        # Set active pin on active group
        future_time = (datetime.now(timezone.utc) + timedelta(minutes=50)).isoformat()
        database.record_pin(
            chat_id=chat_id_active,
            message_id=999,
            payer_user_id=111,
            payer_username="u",
            stars_paid=0,
            charge_id="test",
            unpin_at=future_time,
            db_path=TEST_DB_PATH,
        )

        inactive = database.get_inactive_groups(threshold_minutes=5, db_path=TEST_DB_PATH)
        inactive_ids = [g["chat_id"] for g in inactive]

        # Inactive group must receive 5-min cycle reminder
        self.assertIn(chat_id_inactive, inactive_ids)
        # Active pinned group must NOT receive reminder
        self.assertNotIn(chat_id_active, inactive_ids)


class TestStopPin(unittest.IsolatedAsyncioTestCase):
    async def test_stoppin_unpins_and_clears_active_pin(self):
        """Calling /stoppin unpins the message and allows the next message to be pinned."""
        chat_id = -100555666
        update = MagicMock()
        chat = MagicMock()
        chat.type = ChatType.SUPERGROUP
        chat.id = chat_id
        update.effective_chat = chat

        msg = MagicMock()
        msg.reply_to_message = None
        msg.reply_text = AsyncMock()
        update.effective_message = msg

        user = MagicMock()
        user.id = 777
        update.effective_user = user

        context = MagicMock()
        context.bot = AsyncMock()

        # Mock admin
        with patch("handlers.pin_handler.is_user_admin", return_value=True), \
             patch("handlers.pin_handler.get_active_pin", return_value={
                 "id": 99,
                 "chat_id": chat_id,
                 "message_id": 555,
                 "payer_user_id": 777,
                 "payer_username": "payer",
             }), \
             patch("handlers.pin_handler.mark_pin_inactive") as mock_inactive, \
             patch("handlers.gatekeeper.send_or_replace_reminder") as mock_reminder:
            await stoppin_command(update, context)
            context.bot.unpin_chat_message.assert_called_once_with(chat_id=chat_id, message_id=555)
            mock_inactive.assert_called_once_with(record_id=99)
            mock_reminder.assert_called_once_with(context.bot, chat_id)
            msg.reply_text.assert_called_once()
            self.assertIn("Pin Stopped Successfully", msg.reply_text.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
