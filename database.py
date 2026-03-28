import sqlite3
import os
from datetime import datetime
import pytz

SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')
DB_FILE = os.getenv('DB_FILE', 'guessthebot.db')

# Garante que o diretório pai existe (necessário para /data no Railway)
os.makedirs(os.path.dirname(DB_FILE), exist_ok=True) if os.path.dirname(DB_FILE) else None


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT,
                last_ranking_date TEXT,
                last_reset_date TEXT
            );

            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                game_number INTEGER NOT NULL,
                score INTEGER NOT NULL,
                submitted_at TEXT NOT NULL,
                UNIQUE(guild_id, user_id, game_number)
            );
        """)


def set_channel(guild_id: str, channel_id: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO config (guild_id, channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id",
            (guild_id, channel_id)
        )


def get_channel(guild_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT channel_id FROM config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return row['channel_id'] if row else None


def has_submission(guild_id: str, user_id: str, game_number: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM scores WHERE guild_id = ? AND user_id = ? AND game_number = ?",
            (guild_id, user_id, game_number)
        ).fetchone()
        return row is not None


def add_score(guild_id: str, user_id: str, user_name: str, game_number: int, score: int):
    now = datetime.now(SAO_PAULO_TZ).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scores (guild_id, user_id, user_name, game_number, score, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, user_name, game_number, score, now)
        )


def get_weekly_total(guild_id: str, user_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(score), 0) as total FROM scores WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        ).fetchone()
        return row['total']


def get_ranking(guild_id: str) -> list[tuple[str, int]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT user_name, SUM(score) as total
            FROM scores
            WHERE guild_id = ?
            GROUP BY user_id
            HAVING total > 0
            ORDER BY total DESC
            """,
            (guild_id,)
        ).fetchall()
        return [(row['user_name'], row['total']) for row in rows]


def reset_scores(guild_id: str):
    now = datetime.now(SAO_PAULO_TZ).isoformat()
    with get_conn() as conn:
        conn.execute("DELETE FROM scores WHERE guild_id = ?", (guild_id,))
        conn.execute(
            "INSERT INTO config (guild_id, last_reset_date) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET last_reset_date = excluded.last_reset_date",
            (guild_id, now)
        )


def get_last_reset_date(guild_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_reset_date FROM config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return row['last_reset_date'] if row else None


def set_last_ranking_date(guild_id: str, date_str: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO config (guild_id, last_ranking_date) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET last_ranking_date = excluded.last_ranking_date",
            (guild_id, date_str)
        )


def get_last_ranking_date(guild_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_ranking_date FROM config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return row['last_ranking_date'] if row else None
