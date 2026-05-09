import unittest

import aiosqlite

from app.models import Todo
from app.services.sync_engine import (
    _dedupe_dida_linked_todos,
    _find_existing_source_todo,
    _is_updated,
    _remote_field_changed,
)


CREATE_TODOS = """
CREATE TABLE todos (
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
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT '',
    dida_task_id TEXT,
    dida_project_id TEXT,
    reminders TEXT DEFAULT '',
    repeat_flag TEXT DEFAULT ''
);
"""


class FakeForwarder:
    def __init__(self):
        self.deleted = []

    async def delete_todo(self, remote_id):
        self.deleted.append(remote_id)


class SyncEngineTests(unittest.IsolatedAsyncioTestCase):
    async def make_db(self):
        db = await aiosqlite.connect(":memory:")
        db.row_factory = aiosqlite.Row
        await db.executescript(CREATE_TODOS)
        return db

    async def insert_todo(self, db, **overrides):
        data = {
            "uid": "uid-1",
            "title": "Title",
            "description": "",
            "due_date": "2026-05-07",
            "due_time": "16:00",
            "priority": 0,
            "completed": 0,
            "completed_at": None,
            "ical_raw": "",
            "last_modified": None,
            "synced": 1,
            "synced_at": None,
            "remote_id": None,
            "remote_updated_at": None,
            "source": "dida",
            "created_at": "2026-05-09 05:40:00",
            "updated_at": "2026-05-09 05:40:00",
            "dida_task_id": None,
            "dida_project_id": None,
            "reminders": "",
            "repeat_flag": "",
        }
        data.update(overrides)
        await db.execute(
            """INSERT INTO todos (
                uid, title, description, due_date, due_time, priority,
                completed, completed_at, ical_raw, last_modified, synced,
                synced_at, remote_id, remote_updated_at, source, created_at,
                updated_at, dida_task_id, dida_project_id, reminders, repeat_flag
            ) VALUES (
                :uid, :title, :description, :due_date, :due_time, :priority,
                :completed, :completed_at, :ical_raw, :last_modified, :synced,
                :synced_at, :remote_id, :remote_updated_at, :source, :created_at,
                :updated_at, :dida_task_id, :dida_project_id, :reminders, :repeat_flag
            )""",
            data,
        )
        await db.commit()

    async def test_find_existing_source_todo_prefers_linked_zectrix_row(self):
        db = await self.make_db()
        try:
            await self.insert_todo(
                db,
                uid="zectrix-33613",
                source="zectrix",
                remote_id="33613",
                dida_task_id="dida-task-1",
                created_at="2026-05-09 05:39:39",
            )
            await self.insert_todo(
                db,
                uid="dida-dida-task-1",
                source="dida",
                remote_id="33659",
                dida_task_id="dida-task-1",
                created_at="2026-05-09 05:40:39",
            )

            row = await _find_existing_source_todo(
                db,
                Todo(uid="dida-dida-task-1", title="Title"),
                "dida-task-1",
            )

            self.assertEqual(row["uid"], "zectrix-33613")
        finally:
            await db.close()

    async def test_is_updated_detects_field_change_without_last_modified(self):
        db = await self.make_db()
        try:
            await self.insert_todo(db, uid="dida-1", due_date="2026-05-07")
            row = await (await db.execute("SELECT * FROM todos WHERE uid = ?", ("dida-1",))).fetchone()

            changed = _is_updated(
                row,
                Todo(uid="dida-1", title="Title", due_date="2026-05-09", due_time="16:00"),
            )

            self.assertTrue(changed)
        finally:
            await db.close()

    async def test_dedupe_removes_duplicate_local_and_remote_copy(self):
        db = await self.make_db()
        try:
            await self.insert_todo(
                db,
                uid="zectrix-33613",
                source="zectrix",
                remote_id="33613",
                dida_task_id="dida-task-1",
                created_at="2026-05-09 05:39:39",
            )
            await self.insert_todo(
                db,
                uid="dida-dida-task-1",
                source="dida",
                remote_id="33659",
                dida_task_id="dida-task-1",
                created_at="2026-05-09 05:40:39",
            )
            forwarder = FakeForwarder()

            removed = await _dedupe_dida_linked_todos(db, forwarder=forwarder)
            rows = await (await db.execute("SELECT uid FROM todos ORDER BY uid")).fetchall()

            self.assertEqual(removed, 1)
            self.assertEqual(forwarder.deleted, ["33659"])
            self.assertEqual([row["uid"] for row in rows], ["zectrix-33613"])
        finally:
            await db.close()

    async def test_remote_field_changed_detects_zectrix_change_without_update_date(self):
        db = await self.make_db()
        try:
            await self.insert_todo(db, uid="dida-1", remote_id="33613", due_date="2026-05-07")
            row = await (await db.execute("SELECT * FROM todos WHERE uid = ?", ("dida-1",))).fetchone()

            changed = _remote_field_changed(
                row,
                {
                    "id": "33613",
                    "title": "Title",
                    "description": "",
                    "dueDate": "2026-05-09",
                    "dueTime": "16:00",
                    "priority": 0,
                    "status": 0,
                    "repeatType": "none",
                },
            )

            self.assertTrue(changed)
        finally:
            await db.close()


if __name__ == "__main__":
    unittest.main()
