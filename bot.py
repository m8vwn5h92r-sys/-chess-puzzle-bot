import os
import logging
import sqlite3
import random
import time

import chess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "puzzle_bot.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =========================
# DATABASE
# =========================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            name TEXT,
            points INTEGER DEFAULT 0,
            puzzles_solved INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


def save_user(user):
    conn = db()

    conn.execute("""
        INSERT INTO users (user_id, username, name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            name=excluded.name
    """, (
        user.id,
        user.username,
        user.first_name
    ))

    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db()

    user = conn.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    conn.close()

    return user


# =========================
# MAIN MENU
# =========================

def main_menu():

    keyboard = [
        [
            InlineKeyboardButton(
                "🧩 Puzzle Tournament",
                callback_data="puzzle_menu"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 Rankings",
                callback_data="rankings"
            ),
            InlineKeyboardButton(
                "👤 My Profile",
                callback_data="profile"
            )
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type == "private":
        return

    user = update.effective_user
    save_user(user)

    await update.message.reply_text(
        "♟️ <b>Chess Puzzle Bot</b>\n\n"
        "Choose an option:",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================
# PROFILE
# =========================

async def profile(update, context):

    query = update.callback_query
    await query.answer()

    user = get_user(query.from_user.id)

    if not user:
        save_user(query.from_user)
        user = get_user(query.from_user.id)

    username = (
        f"@{user['username']}"
        if user["username"]
        else "—"
    )

    text = (
        "👤 <b>My Profile</b>\n\n"
        f"Name: {user['name']}\n"
        f"Username: {username}\n"
        f"User ID: <code>{user['user_id']}</code>\n\n"
        f"⭐ Points: <b>{user['points']}</b>\n"
        f"🧩 Puzzles solved: <b>{user['puzzles_solved']}</b>"
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="home"
                )
            ]
        ])
    )


# =========================
# RANKINGS
# =========================

async def rankings(update, context):

    query = update.callback_query
    await query.answer()

    conn = db()

    players = conn.execute("""
        SELECT *
        FROM users
        ORDER BY points DESC
        LIMIT 20
    """).fetchall()

    conn.close()

    if not players:
        text = "📊 No players yet."
    else:

        lines = ["📊 <b>Rankings</b>\n"]

        for i, player in enumerate(players, 1):

            name = (
                f"@{player['username']}"
                if player["username"]
                else player["name"]
            )

            lines.append(
                f"<b>{i}.</b> {name} — ⭐ {player['points']}"
            )

        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="home"
                )
            ]
        ])
    )


# =========================
# PUZZLE MENU
# =========================

async def puzzle_menu(update, context):

    query = update.callback_query
    await query.answer()

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Create Tournament",
                callback_data="create_tournament"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="home"
            )
        ]
    ]

    await query.edit_message_text(
        "🧩 <b>Puzzle Tournament</b>\n\n"
        "Create a tournament and choose:\n\n"
        "🔢 Number of puzzles\n"
        "⏱️ Time per puzzle\n"
        "⭐ Points per puzzle",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# ADMIN CHECK
# =========================

async def is_admin(update, user_id):

    try:

        member = await update.effective_chat.get_member(user_id)

        return member.status in (
            "administrator",
            "creator"
        )

    except Exception:

        return False


# =========================
# CREATE TOURNAMENT
# =========================

async def create_tournament(update, context):

    query = update.callback_query

    if not await is_admin(
        update,
        query.from_user.id
    ):
        await query.answer(
            "Only group admins can create tournaments.",
            show_alert=True
        )
        return

    await query.answer()

    context.user_data["creating_tournament"] = True

    keyboard = [
        [
            InlineKeyboardButton(
                "5",
                callback_data="count:5"
            ),
            InlineKeyboardButton(
                "10",
                callback_data="count:10"
            ),
            InlineKeyboardButton(
                "20",
                callback_data="count:20"
            )
        ]
    ]

    await query.edit_message_text(
        "🔢 <b>Choose number of puzzles:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# CALLBACKS
# =========================

async def callback_handler(update, context):

    query = update.callback_query
    data = query.data

    if data == "home":

        await query.answer()

        await query.edit_message_text(
            "♟️ <b>Chess Puzzle Bot</b>\n\n"
            "Choose an option:",
            parse_mode="HTML",
            reply_markup=main_menu()
        )

        return

    if data == "profile":
        await profile(update, context)
        return

    if data == "rankings":
        await rankings(update, context)
        return

    if data == "puzzle_menu":
        await puzzle_menu(update, context)
        return

    if data == "create_tournament":
        await create_tournament(update, context)
        return

    if data.startswith("count:"):

        await query.answer()

        count = int(data.split(":")[1])

        context.user_data["puzzle_count"] = count

        keyboard = [
            [
                InlineKeyboardButton(
                    "30 sec",
                    callback_data="time:30"
                ),
                InlineKeyboardButton(
                    "60 sec",
                    callback_data="time:60"
                )
            ],
            [
                InlineKeyboardButton(
                    "120 sec",
                    callback_data="time:120"
                )
            ]
        ]

        await query.edit_message_text(
            f"🧩 Puzzles: <b>{count}</b>\n\n"
            "⏱️ Choose time for each puzzle:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    if data.startswith("time:"):

        await query.answer()

        seconds = int(data.split(":")[1])

        context.user_data["puzzle_time"] = seconds

        keyboard = [
            [
                InlineKeyboardButton(
                    "5 Points",
                    callback_data="points:5"
                ),
                InlineKeyboardButton(
                    "10 Points",
                    callback_data="points:10"
                )
            ],
            [
                InlineKeyboardButton(
                    "20 Points",
                    callback_data="points:20"
                )
            ]
        ]

        await query.edit_message_text(
            f"⏱️ Time: <b>{seconds} seconds</b>\n\n"
            "⭐ Choose points:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    if data.startswith("points:"):

        await query.answer()

        points = int(data.split(":")[1])

        count = context.user_data.get(
            "puzzle_count",
            10
        )

        seconds = context.user_data.get(
            "puzzle_time",
            60
        )

        context.user_data["puzzle_points"] = points

        await query.edit_message_text(
            "🧩 <b>Tournament Ready!</b>\n\n"
            f"🔢 Puzzles: <b>{count}</b>\n"
            f"⏱️ Time: <b>{seconds} sec</b>\n"
            f"⭐ Points: <b>{points}</b>\n\n"
            "The actual puzzle tournament system will be added next.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="puzzle_menu"
                    )
                ]
            ])
        )

        return


# =========================
# MAIN
# =========================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    logger.info("Bot started.")

    port = int(
        os.getenv("PORT", "10000")
    )

    hostname = os.getenv(
        "RENDER_EXTERNAL_HOSTNAME"
    )

    if not hostname:
        raise RuntimeError(
            "RENDER_EXTERNAL_HOSTNAME is missing."
        )

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="telegram-webhook",
        webhook_url=f"https://{hostname}/telegram-webhook",
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
