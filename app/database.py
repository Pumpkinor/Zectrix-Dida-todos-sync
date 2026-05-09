import aiosqlite
from app.config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    uid TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    due_date TEXT,
    due_time TEXT,
    priority INTEGER DEFAULT 0,
    completed INTEGER DEFAULT 0,
    completed_at TEXT,
    ical_raw TEXT DEFAULT '',
    last_modified TEXT,
    synced INTEGER DEFAULT 0,
    synced_at TEXT,
    remote_id TEXT,
    remote_updated_at TEXT,
    source TEXT DEFAULT 'dida',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS sync_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT DEFAULT '',
    count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    db = await get_db()
    try:
        await db.executescript(_SCHEMA)

        from app.config import DEFAULTS
        for key, value in DEFAULTS.items():
            await db.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                (key, value),
            )
        await db.commit()

        # Migration: add columns for existing databases
        try:
            await db.execute("ALTER TABLE todos ADD COLUMN remote_updated_at TEXT")
            await db.commit()
        except Exception:
            pass  # Column already exists

        try:
            await db.execute("ALTER TABLE todos ADD COLUMN source TEXT DEFAULT 'dida'")
            await db.commit()
            # Set source for existing rows: zectrix- prefix means zectrix origin
            await db.execute("UPDATE todos SET source = 'zectrix' WHERE uid LIKE 'zectrix-%' AND source = 'dida'")
            await db.commit()
        except Exception:
            pass  # Column already exists

        try:
            await db.execute("ALTER TABLE todos ADD COLUMN dida_task_id TEXT")
            await db.commit()
        except Exception:
            pass

        try:
            await db.execute("ALTER TABLE todos ADD COLUMN dida_project_id TEXT")
            await db.commit()
        except Exception:
            pass

        try:
            await db.execute("ALTER TABLE todos ADD COLUMN reminders TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass

        try:
            await db.execute("ALTER TABLE todos ADD COLUMN repeat_flag TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass
    finally:
        await db.close()


async def get_config(key: str) -> str:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else ""
    finally:
        await db.close()


async def set_config(key: str, value: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
        await db.commit()
    finally:
        await db.close()


async def get_all_config() -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT key, value FROM config")
        rows = await cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        await db.close()


async def add_sync_log(action: str, status: str, detail: str = "", count: int = 0):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO sync_logs (action, status, detail, count) VALUES (?, ?, ?, ?)",
            (action, status, detail, count),
        )
        await db.commit()
    finally:
        await db.close()


async def clear_sync_logs():
    db = await get_db()
    try:
        await db.execute("DELETE FROM sync_logs")
        await db.commit()
    finally:
        await db.close()
