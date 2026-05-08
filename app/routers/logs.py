from fastapi import APIRouter, Query
from app.database import get_db

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def list_logs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    db = await get_db()
    try:
        count_cursor = await db.execute("SELECT COUNT(*) as total FROM sync_logs")
        total = (await count_cursor.fetchone())["total"]

        offset = (page - 1) * size
        cursor = await db.execute(
            "SELECT * FROM sync_logs ORDER BY id DESC LIMIT ? OFFSET ?",
            (size, offset),
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
async def clear_logs():
    db = await get_db()
    try:
        await db.execute("DELETE FROM sync_logs")
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()
