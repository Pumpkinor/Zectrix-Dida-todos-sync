import logging

from fastapi import APIRouter, Query
from app.database import get_db

logger = logging.getLogger(__name__)

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
        logger.info(f"Cleared {total} local todo records")
        return {"ok": True, "deleted": total}
    finally:
        await db.close()


@router.delete("/dida-project")
async def clear_dida_project():
    """Delete all todos from the selected Dida365 projects on the remote side via MCP."""
    from app.services.dida_client import get_dida_mcp_client
    from app.database import get_config

    project_id_raw = await get_config("dida_project_id")
    project_ids = [p.strip() for p in project_id_raw.split(",") if p.strip()] if project_id_raw else []
    if not project_ids:
        return {"error": "未选择滴答清单项目"}

    client = await get_dida_mcp_client()
    if not client:
        return {"error": "MCP token 未配置"}

    try:
        await client.initialize()
        total_count = 0
        for project_id in project_ids:
            tasks = await client.get_undone_tasks(project_id)
            completed = await client.get_completed_tasks([project_id], "2000-01-01", "2099-12-31")
            all_tasks = tasks + completed
            for t in all_tasks:
                tid = t.get("id")
                pid = t.get("projectId") or t.get("project_id") or project_id
                if tid and pid:
                    try:
                        await client.complete_task(pid, tid)
                        total_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete dida task {tid}: {e}")
            logger.info(f"Cleared {len(all_tasks)} tasks from Dida365 project {project_id}")
        return {"ok": True, "deleted": total_count, "project_ids": project_ids}
    except Exception as e:
        logger.error(f"Clear Dida project failed: {e}", exc_info=True)
        return {"error": str(e)}


@router.delete("/zectrix")
async def clear_zectrix():
    """Delete all todos from Zectrix device, including completed ones."""
    from app.services.sync_engine import _get_forwarder

    forwarder = await _get_forwarder()
    if not forwarder:
        return {"error": "Zectrix 未配置"}

    try:
        # Fetch active todos from API
        remote_todos = await forwarder.fetch_remote_todos()
        remote_ids = {str(t.get("id", "")) for t in remote_todos}

        # Also collect remote_ids from local DB (covers completed ones no longer in API)
        db = await get_db()
        try:
            cursor = await db.execute("SELECT remote_id FROM todos WHERE remote_id IS NOT NULL")
            db_ids = {row["remote_id"] for row in await cursor.fetchall()}
        finally:
            await db.close()

        all_ids = remote_ids | db_ids
        count = 0
        for tid in all_ids:
            if tid:
                try:
                    await forwarder.delete_todo(tid)
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete Zectrix todo {tid}: {e}")
        logger.info(f"Cleared {count} tasks from Zectrix ({len(remote_ids)} active + {len(db_ids - remote_ids)} from DB)")
        return {"ok": True, "deleted": count}
    except Exception as e:
        logger.error(f"Clear Zectrix failed: {e}", exc_info=True)
        return {"error": str(e)}


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
