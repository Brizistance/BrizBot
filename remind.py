"""Reminder backend: duration parsing and SQLite persistence.

No discord imports here; cogs/reminders.py handles commands and delivery.
All timestamps are Unix seconds, UTC.
"""

import re
import time
from dataclasses import dataclass

import aiosqlite

DURATION_RE = re.compile(r"(\d+)\s*([smhdw])", re.ASCII)
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
MIN_SECONDS = 30
MAX_SECONDS = 365 * 86400
MAX_PER_USER = 25
MAX_MESSAGE_LEN = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message    TEXT    NOT NULL,
    due_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (due_at);
CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_id);
"""


@dataclass(frozen=True)
class Reminder:
    id: int
    user_id: int
    channel_id: int
    message: str
    due_at: int


def parse_duration(text: str) -> int | None:
    """'2h30m' -> 9000. Returns None if anything in the string isn't a duration."""
    text = text.strip().lower()
    matches = DURATION_RE.findall(text)
    if not matches or DURATION_RE.sub("", text).strip():
        return None
    return sum(int(n) * UNITS[u] for n, u in matches)


def validate_seconds(seconds: int | None) -> bool:
    return seconds is not None and MIN_SECONDS <= seconds <= MAX_SECONDS


class ReminderStore:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    @classmethod
    async def open(cls, path: str = "bot.db") -> "ReminderStore":
        db = await aiosqlite.connect(path)
        await db.executescript(SCHEMA)
        await db.commit()
        return cls(db)

    async def close(self) -> None:
        await self.db.close()

    async def add(
        self, user_id: int, channel_id: int, message: str, seconds: int
    ) -> int | None:
        """Insert a reminder. Returns due_at, or None if the user is at the cap.

        The cap check and insert are one statement so concurrent /remind calls
        can't both slip past the limit.
        """
        due_at = int(time.time()) + seconds
        cur = await self.db.execute(
            """
            INSERT INTO reminders (user_id, channel_id, message, due_at)
            SELECT ?, ?, ?, ?
            WHERE (SELECT COUNT(*) FROM reminders WHERE user_id = ?) < ?
            """,
            (user_id, channel_id, message, due_at, user_id, MAX_PER_USER),
        )
        await self.db.commit()
        return due_at if cur.rowcount else None

    async def due(self, now: int | None = None) -> list[Reminder]:
        """All reminders due at or before `now`, including ones missed while offline."""
        now = int(time.time()) if now is None else now
        async with self.db.execute(
            "SELECT id, user_id, channel_id, message, due_at FROM reminders "
            "WHERE due_at <= ? ORDER BY due_at",
            (now,),
        ) as cur:
            return [Reminder(*row) for row in await cur.fetchall()]

    async def delete(self, reminder_id: int) -> None:
        await self.db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await self.db.commit()

    async def list_for_user(self, user_id: int) -> list[Reminder]:
        async with self.db.execute(
            "SELECT id, user_id, channel_id, message, due_at FROM reminders "
            "WHERE user_id = ? ORDER BY due_at",
            (user_id,),
        ) as cur:
            return [Reminder(*row) for row in await cur.fetchall()]

    async def delete_for_user(self, user_id: int, reminder_id: int) -> bool:
        """Delete a reminder only if it belongs to `user_id`. Returns whether one was removed."""
        cur = await self.db.execute(
            "DELETE FROM reminders WHERE id = ? AND user_id = ?",
            (reminder_id, user_id),
        )
        await self.db.commit()
        return cur.rowcount > 0
