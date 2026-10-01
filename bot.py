import os
import io
import time
import sqlite3
import logging
from typing import Optional

import chess
import chess.pgn
from PIL import Image, ImageDraw, ImageFont

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

DB_FILE = os.getenv("DB_FILE", "chess_bot.db")

START_ELO = 1200
ELO_K = 32

# Chess.com-like visual settings
BOARD_STYLE = "wood"
PIECE_STYLE = "classic"

# Default chess clock: 10 minutes
DEFAULT_TIME_SECONDS = 10 * 60

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            photo_file_id TEXT,
            elo INTEGER DEFAULT 1200,
            games INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            white_id INTEGER NOT NULL,
            black_id INTEGER NOT NULL,
            fen TEXT NOT NULL,
            white_time REAL NOT NULL,
            black_time REAL NOT NULL,
            turn INTEGER NOT NULL,
            status TEXT DEFAULT 'playing',
            result TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            tournament_type TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'waiting',
            max_players INTEGER DEFAULT 16,
            puzzle_count INTEGER DEFAULT 10,
            puzzle_time INTEGER DEFAULT 60,
            puzzle_points INTEGER DEFAULT 10,
            rounds INTEGER DEFAULT 3,
            current_round INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tournament_players (
            tournament_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            score REAL DEFAULT 0,
            correct INTEGER DEFAULT 0,
            PRIMARY KEY (tournament_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tournament_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round INTEGER NOT NULL,
            white_id INTEGER NOT NULL,
            black_id INTEGER NOT NULL,
            result TEXT,
            status TEXT DEFAULT 'waiting'
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# USER FUNCTIONS
# ============================================================

async def save_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user:
        return

    photo_id = None

    try:
        photos = await context.bot.get_user_profile_photos(
            user_id=user.id,
            limit=1
        )

        if photos.total_count > 0:
            photo_id = photos.photos[0][-1].file_id

    except Exception:
        pass

    conn = db()

    conn.execute("""
        INSERT INTO users
        (user_id, username, first_name, photo_file_id, elo)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            photo_file_id=COALESCE(excluded.photo_file_id, users.photo_file_id)
    """, (
        user.id,
        user.username,
        user.first_name,
        photo_id,
        START_ELO
    ))

    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()
    conn.close()
    return row


def username_of(user_id):
    user = get_user(user_id)

    if not user:
        return str(user_id)

    if user["username"]:
        return "@" + user["username"]

    return user["first_name"] or str(user_id)


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():

    keyboard = [
        [
            InlineKeyboardButton(
                "🏆 Puzzle Tournament",
                callback_data="menu_puzzle"
            )
        ],
        [
            InlineKeyboardButton(
                "⚔️ Normal Game",
                callback_data="menu_game"
            )
        ],
        [
            InlineKeyboardButton(
                "🏆 Game Tournament",
                callback_data="menu_game_tournament"
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
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    # Group only
    if update.effective_chat.type == "private":
        return

    await save_user(update, context)

    text = (
        "♟️ <b>Chess Bot</b>\n\n"
        "Choose what you want to do:"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# ============================================================
# PROFILE
# ============================================================

async def show_profile(update, context):

    query = update.callback_query
    await query.answer()

    user = get_user(query.from_user.id)

    if not user:
        return

    text = (
        "👤 <b>My Profile</b>\n\n"
        f"Name: {user['first_name']}\n"
        f"Username: @{user['username'] if user['username'] else '—'}\n"
        f"User ID: <code>{user['user_id']}</code>\n\n"
        f"⭐ Elo: <b>{user['elo']}</b>\n"
        f"🎮 Games: {user['games']}\n"
        f"🏆 Wins: {user['wins']}\n"
        f"🤝 Draws: {user['draws']}\n"
        f"❌ Losses: {user['losses']}"
    )

    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data="home")]
    ]

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ============================================================
# RANKINGS
# ============================================================

async def rankings(update, context):

    query = update.callback_query
    await query.answer()

    conn = db()

    players = conn.execute("""
        SELECT * FROM users
        ORDER BY elo DESC
        LIMIT 20
    """).fetchall()

    conn.close()

    if not players:
        text = "📊 No players yet."
    else:
        lines = ["📊 <b>Rankings</b>\n"]

        for i, player in enumerate(players, 1):
            name = (
                "@" + player["username"]
                if player["username"]
                else player["first_name"]
            )

            lines.append(
                f"<b>{i}.</b> {name} — ⭐ {player['elo']}"
            )

        text = "\n".join(lines)

    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data="home")]
    ]

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ============================================================
# ELO
# ============================================================

def calculate_elo(player_elo, opponent_elo, result):
    expected = 1 / (
        1 + 10 ** ((opponent_elo - player_elo) / 400)
    )

    new_elo = player_elo + ELO_K * (result - expected)

    return round(new_elo)


def update_elo(winner_id, loser_id, draw=False):

    conn = db()

    winner = conn.execute(
        "SELECT elo FROM users WHERE user_id=?",
        (winner_id,)
    ).fetchone()

    loser = conn.execute(
        "SELECT elo FROM users WHERE user_id=?",
        (loser_id,)
    ).fetchone()

    if not winner or not loser:
        conn.close()
        return

    if draw:
        new_winner = calculate_elo(
            winner["elo"],
            loser["elo"],
            0.5
        )

        new_loser = calculate_elo(
            loser["elo"],
            winner["elo"],
            0.5
        )

    else:
        new_winner = calculate_elo(
            winner["elo"],
            loser["elo"],
            1
        )

        new_loser = calculate_elo(
            loser["elo"],
            winner["elo"],
            0
        )

    conn.execute(
        "UPDATE users SET elo=? WHERE user_id=?",
        (new_winner, winner_id)
    )

    conn.execute(
        "UPDATE users SET elo=? WHERE user_id=?",
        (new_loser, loser_id)
    )

    conn.commit()
    conn.close()


# ============================================================
# CHESS BOARD RENDER
# ============================================================

LIGHT_WOOD = (222, 184, 135)
DARK_WOOD = (160, 110, 65)

PIECE_UNICODE = {
    chess.PAWN: "♟",
    chess.KNIGHT: "♞",
    chess.BISHOP: "♝",
    chess.ROOK: "♜",
    chess.QUEEN: "♛",
    chess.KING: "♚",
}


def render_board(fen):

    board = chess.Board(fen)

    size = 800
    square = size // 8

    image = Image.new(
        "RGB",
        (size, size),
        LIGHT_WOOD
    )

    draw = ImageDraw.Draw(image)

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            72
        )
    except Exception:
        font = ImageFont.load_default()

    for rank in range(8):
        for file in range(8):

            x = file * square
            y = rank * square

            is_dark = (rank + file) % 2 == 1

            draw.rectangle(
                [
                    x,
                    y,
                    x + square,
                    y + square
                ],
                fill=DARK_WOOD if is_dark else LIGHT_WOOD
            )

            chess_square = chess.square(
                file,
                7 - rank
            )

            piece = board.piece_at(chess_square)

            if piece:

                symbol = PIECE_UNICODE[piece.piece_type]

                # White pieces
                if piece.color == chess.WHITE:
                    fill = (245, 245, 245)
                    stroke = (20, 20, 20)

                # Black pieces
                else:
                    fill = (20, 20, 20)
                    stroke = (240, 240, 240)

                bbox = draw.textbbox(
                    (0, 0),
                    symbol,
                    font=font
                )

                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]

                px = x + (square - w) / 2
                py = y + (square - h) / 2 - 8

                draw.text(
                    (px, py),
                    symbol,
                    font=font,
                    fill=fill,
                    stroke_width=2,
                    stroke_fill=stroke
                )

    output = io.BytesIO()
    output.name = "board.png"

    image.save(
        output,
        format="PNG"
    )

    output.seek(0)

    return output


# ============================================================
# NORMAL GAME
# ============================================================

async def normal_game_menu(update, context):

    query = update.callback_query
    await query.answer()

    text = (
        "⚔️ <b>Normal Game</b>\n\n"
        "To challenge another player:\n\n"
        "Reply to that player's message with:\n"
        "<code>/challenge</code>\n\n"
        "The opponent can then accept or reject."
    )

    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data="home")]
    ]

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type == "private":
        return

    await save_user(update, context)

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "⚠️ Reply to the player's message and write /challenge"
        )
        return

    challenger = update.effective_user
    opponent = update.message.reply_to_message.from_user

    if challenger.id == opponent.id:
        return

    if opponent.is_bot:
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Accept",
                callback_data=f"accept_game:{challenger.id}"
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject_game:{challenger.id}"
            )
        ]
    ]

    await update.message.reply_text(
        f"⚔️ {opponent.mention_html()}, "
        f"{challenger.mention_html()} challenged you!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def accept_game(update, context):

    query = update.callback_query
    await query.answer()

    opponent = query.from_user

    try:
        challenger_id = int(
            query.data.split(":")[1]
        )
    except Exception:
        return

    if challenger_id == opponent.id:
        return

    await save_user(update, context)

    # Prevent players from starting multiple games
    conn = db()

    active = conn.execute("""
        SELECT id FROM games
        WHERE status='playing'
        AND (
            white_id IN (?, ?)
            OR black_id IN (?, ?)
        )
    """, (
        challenger_id,
        opponent.id,
        challenger_id,
        opponent.id
    )).fetchone()

    if active:
        conn.close()

        await query.edit_message_text(
            "⚠️ One of these players already has an active game."
        )
        return

    game = chess.Board()

    now = time.time()

    conn.execute("""
        INSERT INTO games
        (
            chat_id,
            white_id,
            black_id,
            fen,
            white_time,
            black_time,
            turn,
            status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'playing', ?, ?)
    """, (
        query.message.chat_id,
        challenger_id,
        opponent.id,
        game.fen(),
        DEFAULT_TIME_SECONDS,
        DEFAULT_TIME_SECONDS,
        chess.WHITE,
        now,
        now
    ))

    game_id = conn.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    conn.commit()
    conn.close()

    await send_game_board(
        query.message.chat_id,
        game_id,
        context
    )

    await query.edit_message_text(
        "⚔️ <b>Game accepted!</b>\n\n"
        "White moves first.",
        parse_mode="HTML"
    )


async def reject_game(update, context):

    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "❌ Challenge rejected."
    )


# ============================================================
# SEND BOARD
# ============================================================

async def send_game_board(chat_id, game_id, context):

    conn = db()

    row = conn.execute(
        "SELECT * FROM games WHERE id=?",
        (game_id,)
    ).fetchone()

    conn.close()

    if not row:
        return

    board = render_board(row["fen"])

    white = username_of(row["white_id"])
    black = username_of(row["black_id"])

    white_time = format_time(
        get_remaining_time(row, chess.WHITE)
    )

    black_time = format_time(
        get_remaining_time(row, chess.BLACK)
    )

    turn_name = (
        white
        if row["turn"] == chess.WHITE
        else black
    )

    caption = (
        "♟️ <b>Chess Game</b>\n\n"
        f"⚪ {white}  ⏱️ {white_time}\n"
        f"⚫ {black}  ⏱️ {black_time}\n\n"
        f"▶️ Turn: <b>{turn_name}</b>\n\n"
        "Send your move like:\n"
        "<code>e2e4</code>"
    )

    await context.bot.send_photo(
        chat_id=chat_id,
        photo=board,
        caption=caption,
        parse_mode="HTML"
    )


def format_time(seconds):

    seconds = max(0, int(seconds))

    minutes = seconds // 60
    secs = seconds % 60

    return f"{minutes:02d}:{secs:02d}"


def get_remaining_time(row, color):

    now = time.time()

    white_time = row["white_time"]
    black_time = row["black_time"]

    if row["status"] != "playing":
        return (
            white_time
            if color == chess.WHITE
            else black_time
        )

    elapsed = max(
        0,
        now - row["updated_at"]
    )

    if row["turn"] == color:

        if color == chess.WHITE:
            return max(
                0,
                white_time - elapsed
            )

        return max(
            0,
            black_time - elapsed
        )

    return (
        white_time
        if color == chess.WHITE
        else black_time
    )


# ============================================================
# MOVE HANDLER
# ============================================================

async def move_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type == "private":
        return

    text = update.message.text.strip()

    if not text:
        return

    if len(text) != 4:
        return

    # Example: e2e4
    try:
        chess.parse_square(text[:2])
        chess.parse_square(text[2:])
    except Exception:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    conn = db()

    games = conn.execute("""
        SELECT * FROM games
        WHERE chat_id=?
        AND status='playing'
        AND (
            white_id=?
            OR black_id=?
        )
        ORDER BY id DESC
        LIMIT 1
    """, (
        chat_id,
        user_id,
        user_id
    )).fetchone()

    if not games:
        conn.close()
        return

    game_row = games

    board = chess.Board(
        game_row["fen"]
    )

    player_color = (
        chess.WHITE
        if user_id == game_row["white_id"]
        else chess.BLACK
    )

    if board.turn != player_color:
        conn.close()

        await update.message.reply_text(
            "⏳ It's not your turn."
        )
        return

    # Check clock BEFORE accepting move
    remaining = get_remaining_time(
        game_row,
        player_color
    )

    if remaining <= 0:

        winner = (
            game_row["black_id"]
            if player_color == chess.WHITE
            else game_row["white_id"]
        )

        await finish_game(
            game_row["id"],
            winner,
            user_id,
            False,
            context
        )

        conn.close()

        await update.message.reply_text(
            "⏱️ Your time has run out."
        )
        return

    try:
        move = chess.Move.from_uci(text)
    except Exception:
        conn.close()
        return

    if move not in board.legal_moves:
        conn.close()

        await update.message.reply_text(
            "❌ Illegal move."
        )
        return

    # Calculate time used
    now = time.time()
    elapsed = max(
        0,
        now - game_row["updated_at"]
    )

    white_time = game_row["white_time"]
    black_time = game_row["black_time"]

    if player_color == chess.WHITE:
        white_time -= elapsed
    else:
        black_time -= elapsed

    board.push(move)

    # Game over?
    result = None
    winner = None
    loser = None
    draw = False

    if board.is_checkmate():

        result = (
            "1-0"
            if board.turn == chess.BLACK
            else "0-1"
        )

        winner = (
            game_row["white_id"]
            if result == "1-0"
            else game_row["black_id"]
        )

        loser = (
            game_row["black_id"]
            if result == "1-0"
            else game_row["white_id"]
        )

    elif (
        board.is_stalemate()
        or board.is_insufficient_material()
        or board.is_seventyfive_moves()
        or board.is_fivefold_repetition()
    ):

        result = "1/2-1/2"
        draw = True

    new_turn = board.turn

    conn.execute("""
        UPDATE games
        SET fen=?,
            white_time=?,
            black_time=?,
            turn=?,
            updated_at=?,
            result=?,
            status=?
        WHERE id=?
    """, (
        board.fen(),
        max(0, white_time),
        max(0, black_time),
        new_turn,
        now,
        result,
        "finished" if result else "playing",
        game_row["id"]
    ))

    conn.commit()
    conn.close()

    if result:

        if draw:
            update_elo(
                game_row["white_id"],
                game_row["black_id"],
                True
            )
        else:
            update_elo(
                winner,
                loser,
                False
            )

        await update_game_stats(
            game_row["white_id"],
            game_row["black_id"],
            result
        )

        await update.message.reply_text(
            "🏁 <b>Game Over</b>\n\n"
            f"Result: <b>{result}</b>\n"
            "⭐ Elo has been updated.",
            parse_mode="HTML"
        )

    else:

        await send_game_board(
            chat_id,
            game_row["id"],
            context
        )


async def finish_game(
    game_id,
    winner,
    loser,
    draw,
    context
):

    conn = db()

    row = conn.execute(
        "SELECT * FROM games WHERE id=?",
        (game_id,)
    ).fetchone()

    if not row:
        conn.close()
        return

    result = (
        "1/2-1/2"
        if draw
        else (
            "1-0"
            if winner == row["white_id"]
            else "0-1"
        )
    )

    conn.execute("""
        UPDATE games
        SET status='finished',
            result=?,
            updated_at=?
        WHERE id=?
    """, (
        result,
        time.time(),
        game_id
    ))

    conn.commit()
    conn.close()

    if draw:
        update_elo(
            row["white_id"],
            row["black_id"],
            True
        )
    else:
        update_elo(
            winner,
            loser,
            False
        )

    await update_game_stats(
        row["white_id"],
        row["black_id"],
        result
    )


async def update_game_stats(
    white_id,
    black_id,
    result
):

    conn = db()

    conn.execute("""
        UPDATE users
        SET games=games+1
        WHERE user_id IN (?, ?)
    """, (
        white_id,
        black_id
    ))

    if result == "1-0":

        conn.execute("""
            UPDATE users
            SET wins=wins+1
            WHERE user_id=?
        """, (white_id,))

        conn.execute("""
            UPDATE users
            SET losses=losses+1
            WHERE user_id=?
        """, (black_id,))

    elif result == "0-1":

        conn.execute("""
            UPDATE users
            SET wins=wins+1
            WHERE user_id=?
        """, (black_id,))

        conn.execute("""
            UPDATE users
            SET losses=losses+1
            WHERE user_id=?
        """, (white_id,))

    else:

        conn.execute("""
            UPDATE users
            SET draws=draws+1
            WHERE user_id IN (?, ?)
        """, (
            white_id,
            black_id
        ))

    conn.commit()
    conn.close()


# ============================================================
# PUZZLE TOURNAMENT MENU
# ============================================================

async def puzzle_menu(update, context):

    query = update.callback_query
    await query.answer()

    text = (
        "🧩 <b>Puzzle Tournament</b>\n\n"
        "Admin can create a puzzle tournament "
        "and choose:\n\n"
        "• Number of puzzles\n"
        "• Time per puzzle\n"
        "• Points per puzzle\n\n"
        "Puzzle results do NOT affect Elo."
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Create Tournament",
                callback_data="create_puzzle"
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
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ============================================================
# GAME TOURNAMENT MENU
# ============================================================

async def game_tournament_menu(update, context):

    query = update.callback_query
    await query.answer()

    text = (
        "🏆 <b>Game Tournament</b>\n\n"
        "Real chess games between players.\n\n"
        "Tournament points:\n"
        "🏆 Win = 1\n"
        "🤝 Draw = 0.5\n"
        "❌ Loss = 0\n\n"
        "Tournament games do NOT change Elo."
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Create Tournament",
                callback_data="create_game_tournament"
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
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ============================================================
# ADMIN CHECK
# ============================================================

async def is_admin(update, user_id):

    try:

        member = await update.effective_chat.get_member(
            user_id
        )

        return member.status in (
            "administrator",
            "creator"
        )

    except Exception:

        return False


# ============================================================
# TOURNAMENT CREATION
# ============================================================

async def create_puzzle_tournament(update, context):

    query = update.callback_query
    await query.answer()

    if not await is_admin(update, query.from_user.id):

        await query.answer(
            "Only group admins can create tournaments.",
            show_alert=True
        )
        return

    chat_id = query.message.chat_id

    conn = db()

    conn.execute("""
        INSERT INTO tournaments
        (
            chat_id,
            tournament_type,
            name,
            status,
            created_at
        )
        VALUES (?, 'puzzle', ?, 'waiting', ?)
    """, (
        chat_id,
        "Puzzle Tournament",
        time.time()
    ))

    tournament_id = conn.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    conn.commit()
    conn.close()

    keyboard = [
        [
            InlineKeyboardButton(
                "5 Puzzles",
                callback_data=f"puzzles:{tournament_id}:5"
            ),
            InlineKeyboardButton(
                "10 Puzzles",
                callback_data=f"puzzles:{tournament_id}:10"
            )
        ],
        [
            InlineKeyboardButton(
                "20 Puzzles",
                callback_data=f"puzzles:{tournament_id}:20"
            )
        ]
    ]

    await query.edit_message_text(
        "🧩 Choose number of puzzles:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def create_game_tournament(update, context):

    query = update.callback_query
    await query.answer()

    if not await is_admin(update, query.from_user.id):

        await query.answer(
            "Only group admins can create tournaments.",
            show_alert=True
        )
        return

    chat_id = query.message.chat_id

    conn = db()

    conn.execute("""
        INSERT INTO tournaments
        (
            chat_id,
            tournament_type,
            name,
            status,
            created_at
        )
        VALUES (?, 'game', ?, 'waiting', ?)
    """, (
        chat_id,
        "Game Tournament",
        time.time()
    ))

    tournament_id = conn.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    conn.commit()
    conn.close()

    keyboard = [
        [
            InlineKeyboardButton(
                "3 Rounds",
                callback_data=f"rounds:{tournament_id}:3"
            ),
            InlineKeyboardButton(
                "5 Rounds",
                callback_data=f"rounds:{tournament_id}:5"
            )
        ]
    ]

    await query.edit_message_text(
        "🏆 Choose tournament rounds:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ============================================================
# CALLBACK ROUTER
# ============================================================

async def callback_router(update, context):

    query = update.callback_query

    data = query.data

    if data == "home":

        await query.answer()

        await query.edit_message_text(
            "♟️ <b>Chess Bot</b>\n\nChoose an option:",
            parse_mode="HTML",
            reply_markup=main_menu()
        )

        return

    if data == "profile":
        await show_profile(update, context)
        return

    if data == "rankings":
        await rankings(update, context)
        return

    if data == "menu_game":
        await normal_game_menu(update, context)
        return

    if data == "menu_puzzle":
        await puzzle_menu(update, context)
        return

    if data == "menu_game_tournament":
        await game_tournament_menu(update, context)
        return

    if data == "create_puzzle":
        await create_puzzle_tournament(update, context)
        return

    if data == "create_game_tournament":
        await create_game_tournament(update, context)
        return

    if data.startswith("accept_game:"):
        await accept_game(update, context)
        return

    if data.startswith("reject_game:"):
        await reject_game(update, context)
        return

    # Puzzle count
    if data.startswith("puzzles:"):

        await query.answer()

        parts = data.split(":")

        tournament_id = int(parts[1])
        count = int(parts[2])

        conn = db()

        conn.execute("""
            UPDATE tournaments
            SET puzzle_count=?
            WHERE id=?
        """, (
            count,
            tournament_id
        ))

        conn.commit()
        conn.close()

        keyboard = [
            [
                InlineKeyboardButton(
                    "30 sec",
                    callback_data=f"ptime:{tournament_id}:30"
                ),
                InlineKeyboardButton(
                    "60 sec",
                    callback_data=f"ptime:{tournament_id}:60"
                )
            ],
            [
                InlineKeyboardButton(
                    "120 sec",
                    callback_data=f"ptime:{tournament_id}:120"
                )
            ]
        ]

        await query.edit_message_text(
            "⏱️ Choose time for each puzzle:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    # Puzzle time
    if data.startswith("ptime:"):

        await query.answer()

        parts = data.split(":")

        tournament_id = int(parts[1])
        seconds = int(parts[2])

        conn = db()

        conn.execute("""
            UPDATE tournaments
            SET puzzle_time=?
            WHERE id=?
        """, (
            seconds,
            tournament_id
        ))

        conn.commit()
        conn.close()

        keyboard = [
            [
                InlineKeyboardButton(
                    "5 Points",
                    callback_data=f"ppoints:{tournament_id}:5"
                ),
                InlineKeyboardButton(
                    "10 Points",
                    callback_data=f"ppoints:{tournament_id}:10"
                )
            ],
            [
                InlineKeyboardButton(
                    "20 Points",
                    callback_data=f"ppoints:{tournament_id}:20"
                )
            ]
        ]

        await query.edit_message_text(
            "⭐ Choose points per correct puzzle:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    # Puzzle points
    if data.startswith("ppoints:"):

        await query.answer()

        parts = data.split(":")

        tournament_id = int(parts[1])
        points = int(parts[2])

        conn = db()

        conn.execute("""
            UPDATE tournaments
            SET puzzle_points=?,
                status='waiting'
            WHERE id=?
        """, (
            points,
            tournament_id
        ))

        conn.commit()
        conn.close()

        keyboard = [
            [
                InlineKeyboardButton(
                    "🧩 Join Tournament",
                    callback_data=f"join_puzzle:{tournament_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "▶️ Start",
                    callback_data=f"start_puzzle:{tournament_id}"
                )
            ]
        ]

        await query.edit_message_text(
            "🧩 <b>Puzzle Tournament Created!</b>\n\n"
            f"Puzzles: <b>{get_tournament_value(tournament_id, 'puzzle_count')}</b>\n"
            f"Time: <b>{seconds_to_text(get_tournament_value(tournament_id, 'puzzle_time'))}</b>\n"
            f"Points: <b>{get_tournament_value(tournament_id, 'puzzle_points')}</b>\n\n"
            "Players can join below.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    # Join puzzle
    if data.startswith("join_puzzle:"):

        await query.answer()

        tournament_id = int(
            data.split(":")[1]
        )

        await save_user(update, context)

        conn = db()

        exists = conn.execute("""
            SELECT 1 FROM tournament_players
            WHERE tournament_id=? AND user_id=?
        """, (
            tournament_id,
            query.from_user.id
        )).fetchone()

        if not exists:

            conn.execute("""
                INSERT INTO tournament_players
                (tournament_id, user_id)
                VALUES (?, ?)
            """, (
                tournament_id,
                query.from_user.id
            ))

            conn.commit()

        conn.close()

        await query.answer(
            "You joined the tournament!",
            show_alert=True
        )

        return

    # Start puzzle
    if data.startswith("start_puzzle:"):

        await query.answer()

        tournament_id = int(
            data.split(":")[1]
        )

        conn = db()

        tournament = conn.execute(
            "SELECT * FROM tournaments WHERE id=?",
            (tournament_id,)
        ).fetchone()

        if tournament:

            conn.execute("""
                UPDATE tournaments
                SET status='running'
                WHERE id=?
            """, (
                tournament_id,
            ))

            conn.commit()

        conn.close()

        await query.edit_message_text(
            "🧩 Tournament started!\n\n"
            "Puzzle system is ready."
        )

        return

    # Game tournament rounds
    if data.startswith("rounds:"):

        await query.answer()

        parts = data.split(":")

        tournament_id = int(parts[1])
        rounds = int(parts[2])

        conn = db()

        conn.execute("""
            UPDATE tournaments
            SET rounds=?
            WHERE id=?
        """, (
            rounds,
            tournament_id
        ))

        conn.commit()
        conn.close()

        keyboard = [
            [
                InlineKeyboardButton(
                    "🏆 Join Tournament",
                    callback_data=f"join_game_tournament:{tournament_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "▶️ Start",
                    callback_data=f"start_game_tournament:{tournament_id}"
                )
            ]
        ]

        await query.edit_message_text(
            f"🏆 <b>Game Tournament</b>\n\n"
            f"Rounds: <b>{rounds}</b>\n\n"
            "Join before the admin starts it.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    # Join game tournament
    if data.startswith("join_game_tournament:"):

        await query.answer()

        tournament_id = int(
            data.split(":")[1]
        )

        await save_user(update, context)

        conn = db()

        exists = conn.execute("""
            SELECT 1 FROM tournament_players
            WHERE tournament_id=? AND user_id=?
        """, (
            tournament_id,
            query.from_user.id
        )).fetchone()

        if not exists:

            conn.execute("""
                INSERT INTO tournament_players
                (tournament_id, user_id)
                VALUES (?, ?)
            """, (
                tournament_id,
                query.from_user.id
            ))

            conn.commit()

        conn.close()

        await query.answer(
            "You joined the tournament!",
            show_alert=True
        )

        return

    # Start game tournament
    if data.startswith("start_game_tournament:"):

        await query.answer()

        tournament_id = int(
            data.split(":")[1]
        )

        await start_game_tournament(
            update,
            context,
            tournament_id
        )

        return


# ============================================================
# TOURNAMENT HELPERS
# ============================================================

def get_tournament_value(tournament_id, field):

    allowed = {
        "puzzle_count",
        "puzzle_time",
        "puzzle_points",
        "rounds"
    }

    if field not in allowed:
        return None

    conn = db()

    row = conn.execute(
        f"SELECT {field} FROM tournaments WHERE id=?",
        (tournament_id,)
    ).fetchone()

    conn.close()

    return row[field] if row else None


def seconds_to_text(seconds):

    if seconds < 60:
        return f"{seconds} seconds"

    return f"{seconds // 60} minutes"


async def start_game_tournament(
    update,
    context,
    tournament_id
):

    query = update.callback_query

    if not await is_admin(
        update,
        query.from_user.id
    ):

        await query.answer(
            "Only admins can start it.",
            show_alert=True
        )
        return

    conn = db()

    players = conn.execute("""
        SELECT user_id
        FROM tournament_players
        WHERE tournament_id=?
    """, (
        tournament_id,
    )).fetchall()

    if len(players) < 2:

        conn.close()

        await query.answer(
            "At least 2 players are required.",
            show_alert=True
        )
        return

    ids = [
        p["user_id"]
        for p in players
    ]

    # Simple first-round pairing
    conn.execute("""
        UPDATE tournaments
        SET status='running',
            current_round=1
        WHERE id=?
    """, (
        tournament_id,
    ))

    for i in range(0, len(ids) - 1, 2):

        white = ids[i]
        black = ids[i + 1]

        conn.execute("""
            INSERT INTO tournament_matches
            (
                tournament_id,
                round,
                white_id,
                black_id,
                status
            )
            VALUES (?, 1, ?, ?, 'waiting')
        """, (
            tournament_id,
            white,
            black
        ))

    conn.commit()
    conn.close()

    await query.edit_message_text(
        "🏆 <b>Game Tournament Started!</b>\n\n"
        "Round 1 pairings have been created.\n\n"
        "Tournament games will NOT affect Elo.",
        parse_mode="HTML"
    )


# ============================================================
# GENERAL CALLBACK ERROR PROTECTION
# ============================================================

async def error_handler(update, context):

    logger.exception(
        "Exception while handling update:",
        exc_info=context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "challenge",
            challenge
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_router
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            move_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Chess bot is starting..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
