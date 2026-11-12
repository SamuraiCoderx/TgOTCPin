# ⭐️ Telegram Star Pin & Paid-Only OTC Bot (@GramsGc_PinRobot)

A professional Telegram bot designed for **paid-only groups** (such as OTC trading, ads, and announcement groups) where **only paid messages are allowed to be posted and pinned**, strictly taking **Telegram Stars (XTR)**.

---

## 🚀 How It Works: "Paid Messages Only" Architecture

```
Member sends a message in group
             │
             ▼
   Is sender an Admin?
    ├── [YES] ──► Message is kept as-is (Admin messages are never pinned and never deleted)
    │
    └── [NO]  ──► 1. Bot immediately DELETES the unpaid message
                  2. Bot generates a Telegram Star (XTR) Invoice
                  3. If unpaid after 3 min: Invoice is cleaned up (group stays clean)
                  4. When PAID with Stars:
                     • Bot posts the message to the group
                     • Bot pins the message to the top
                     • Bot schedules auto-unpinning after 1h-24h (or early with /stoppin)
```

---

## 💎 Core Features

1. **Paid-Only Group Gatekeeper**:
   - Every non-admin message posted directly in the group is intercepted.
   - Unpaid messages are **immediately deleted** from the chat so free chat/spam is impossible.
   - A Telegram Star invoice is issued to post and pin the message.
   - If the invoice is left unpaid, it auto-deletes after 3 minutes so the chat remains 100% clean.

2. **Custom Pin Durations (1h to 24h)**:
   - Users can choose durations from **1 hour up to 24 hours** (`/pin 1h`, `/pin 2h`, `/pin 24h`).
   - The Star cost scales dynamically:
     $$\text{Total Stars} = \text{Hourly Rate} \times \text{Hours}$$

3. **Background Auto-Unpin Worker**:
   - A background loop checks SQLite every 15 seconds.
   - Once a message's paid duration expires, it is **automatically unpinned** from the group.

4. **Early Pin Cancellation (`/stoppin`)**:
   - Active pins can be stopped at any time using `/stoppin`.
   - **Permission Guard:** Only the person who paid for the pin OR a group administrator has permission to stop it.

5. **Strict Admin Protection**:
   - Admin messages are **never pinned** by the bot.
   - Admin messages are **never deleted** by the gatekeeper.
   - Admins can pin their own messages for free using Telegram's built-in pin menu.

---

## 📋 Commands Reference

| Command | Permission | Description |
| :--- | :--- | :--- |
| `/pin [1h-24h]` | All Members | Reply to a regular member's message to pay Stars and pin it for 1h-24h. |
| `/stoppin` | Payer or Admins | Stop an active Star pin early and unpin the message immediately. |
| `/paidonly [on/off]` | Group Admins | Toggle Paid-Only Gatekeeper mode (default: ON). |
| `/price` | All Members | Check the current hourly pin price in Stars. |
| `/setprice <amount>` | Group Admins | Update the hourly Star rate (e.g. `/setprice 15`). |
| `/stats` | All Members | View total messages pinned and Stars collected. |
| `/unpin` | Group Admins | Unpin the top pinned message, or reply to a message to unpin it. |
| `/help` | All Members | Display instructions, rules, and command list. |
| `/start` | All Members | Start the bot and get group addition link. |

---

## 🛠️ Required Bot Permissions in Group

To enable both paid-only gatekeeping and message pinning, promote `@GramsGc_PinRobot` to **Administrator** in your group with:
- ✅ **Pin messages** (`can_pin_messages`)
- ✅ **Delete messages** (`can_delete_messages`)

---

## 🏃 Running the Bot

Run directly using Python:
```bash
python bot.py
```

### Running Tests
To run the automated test suite verifying database functions, gatekeeper deletion, admin protection, duration parsing, and payment handlers:
```bash
python test_bot.py
```
