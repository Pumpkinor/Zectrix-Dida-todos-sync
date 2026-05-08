from fastapi import APIRouter, Query
from app.database import get_db

router = APIRouter(prefix="/api/todos", tags=["todos"])


@router.get("")
async def list_todos(
    status: str = Query(None, description="completed / pending / all"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    db = await get_db()
    try:
        where = ""
        params = []
        if status == "completed":
            where = "WHERE completed = 1"
        elif status == "pending":
            where = "WHERE completed = 0"

        count_cursor = await db.execute(f"SELECT COUNT(*) as total FROM todos {where}", params)
        total = (await count_cursor.fetchone())["total"]

        offset = (page - 1) * size
        cursor = await db.execute(
            f"SELECT * FROM todos {where} ORDER BY due_date IS NULL, due_date ASC, updated_at DESC LIMIT ? OFFSET ?",
            params + [size, offset],
        )
        rows = await cursor.fetchall()
        return {
            "total": total,
            "page": page,
            "size": size,
            "data": [dict(r) for r in rows],
        }
    finally:
        await db.close()


@router.delete("")
async def clear_todos():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) as total FROM todos")
        total = (await cursor.fetchone())["total"]
        await db.execute("DELETE FROM todos")
        await db.commit()
        return {"ok": True, "deleted": total}
    finally:
        await db.close()


@router.get("/{uid}")
async def get_todo(uid: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM todos WHERE uid = ?", (uid,))
        row = await cursor.fetchone()
        if not row:
            return {"error": "Not found"}
        return dict(row)
    finally:
        await db.close()
