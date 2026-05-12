"""
待办任务 CRUD 路由模块

这个模块定义了待办任务（Todo）的增删查 API 端点，以及数据清理操作。

【API 端点一览】
  GET    /api/todos              — 分页查询待办列表（支持按状态筛选）
  GET    /api/todos/{uid}        — 查询单个待办详情
  DELETE /api/todos              — 清空所有本地待办记录
  DELETE /api/todos/dida-project — 清空滴答清单选中项目的所有任务
  DELETE /api/todos/zectrix      — 清空 Zectrix 设备上的所有任务

【Python / FastAPI 知识点】
  - Query 参数：通过 Query() 函数声明 URL 查询参数，可以设置默认值、
    取值范围和描述。例如 ?status=pending&page=2&size=20

  - f-string SQL：注意这里用 f-string 拼接 WHERE 子句（不涉及用户输入的部分），
    而用户可控的值仍通过 ? 占位符传入，避免 SQL 注入。
"""

import logging

# 导入 FastAPI 核心类和查询参数工具
from fastapi import APIRouter, Query
# 导入数据库操作函数
from app.database import get_db

# 创建日志记录器
logger = logging.getLogger(__name__)

# 创建路由器实例。prefix="/api/todos" 表示这个路由器下所有端点的 URL 都以 /api/todos 开头
router = APIRouter(prefix="/api/todos", tags=["todos"])


@router.get("")
async def list_todos(
    status: str = Query(None, description="completed / pending / all"),  # 筛选状态，默认 None=全部
    page: int = Query(1, ge=1),       # 页码，默认第1页，ge=1 表示最小值为1（大于等于1）
    size: int = Query(20, ge=1, le=100),  # 每页条数，默认20，范围 1~100
):
    """
    分页查询待办列表。

    GET /api/todos?status=pending&page=1&size=20

    参数:
      status: 筛选条件。"completed"=已完成, "pending"=待完成, 不传=全部
      page:   页码（从 1 开始）
      size:   每页条数

    返回:
      {
        "total": 100,        // 总记录数
        "page": 1,           // 当前页
        "size": 20,          // 每页条数
        "data": [...]        // 当前页的数据数组
      }
    """
    db = await get_db()
    try:
        # 根据 status 参数构建 WHERE 条件
        where = ""
        params = []
        if status == "completed":
            where = "WHERE completed = 1"
        elif status == "pending":
            where = "WHERE completed = 0"

        # 查询总记录数（用于分页计算）
        count_cursor = await db.execute(f"SELECT COUNT(*) as total FROM todos {where}", params)
        total = (await count_cursor.fetchone())["total"]

        # 计算分页偏移量：offset = (page - 1) * size
        offset = (page - 1) * size
        # 查询当前页的数据
        # ORDER BY due_date IS NULL 将无截止日期的排到最后
        #         due_date ASC 按截止日期升序
        #         updated_at DESC 同日按更新时间倒序
        cursor = await db.execute(
            f"SELECT * FROM todos {where} ORDER BY due_date IS NULL, due_date ASC, updated_at DESC LIMIT ? OFFSET ?",
            params + [size, offset],
        )
        rows = await cursor.fetchall()
        return {
            "total": total,
            "page": page,
            "size": size,
            "data": [dict(r) for r in rows],  # 将每一行（Row 对象）转换为普通字典
        }
    finally:
        await db.close()


@router.delete("")
async def clear_todos():
    """
    清空本地所有待办记录。

    DELETE /api/todos

    只删除本地 SQLite 数据库中的记录，不影响滴答清单和 Zectrix 上的数据。
    用于重置同步状态。
    """
    db = await get_db()
    try:
        # 先查询要删除的总数（用于返回给前端展示）
        cursor = await db.execute("SELECT COUNT(*) as total FROM todos")
        total = (await cursor.fetchone())["total"]
        # 删除所有记录
        await db.execute("DELETE FROM todos")
        await db.commit()
        logger.info(f"Cleared {total} local todo records")
        return {"ok": True, "deleted": total}
    finally:
        await db.close()


@router.delete("/dida-project")
async def clear_dida_project():
    """
    清空滴答清单选中项目中的所有任务。

    DELETE /api/todos/dida-project

    通过 MCP API 将选中项目中的所有任务标记为已完成（等效于删除）。
    这是一个危险操作，会直接修改滴答清单的远程数据。
    """
    from app.services.dida_client import get_dida_mcp_client
    from app.database import get_config

    # 获取用户配置的要清理的项目 ID 列表
    project_id_raw = await get_config("dida_project_id")
    project_ids = [p.strip() for p in project_id_raw.split(",") if p.strip()] if project_id_raw else []
    if not project_ids:
        return {"error": "未选择滴答清单项目"}

    client = await get_dida_mcp_client()
    if not client:
        return {"error": "MCP token 未配置"}

    try:
        await client.initialize()  # MCP 协议握手
        total_count = 0
        for project_id in project_ids:
            # 获取该项目下所有未完成和已完成的任务
            tasks = await client.get_undone_tasks(project_id)
            completed = await client.get_completed_tasks([project_id], "2000-01-01", "2099-12-31")
            all_tasks = tasks + completed
            # 逐个标记为已完成
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
    """
    清空 Zectrix 设备上的所有任务。

    DELETE /api/todos/zectrix

    同时从 Zectrix API 和本地数据库的 remote_id 中收集所有任务 ID，
    逐个调用 Zectrix API 删除。已完成但不再出现在 API 中的任务也会被清理。
    """
    from app.services.sync_engine import _get_forwarder

    forwarder = await _get_forwarder()
    if not forwarder:
        return {"error": "Zectrix 未配置"}

    try:
        # 从 Zectrix API 获取当前活跃的任务 ID
        remote_todos = await forwarder.fetch_remote_todos()
        remote_ids = {str(t.get("id", "")) for t in remote_todos}

        # 从本地数据库获取所有已知的 remote_id（包括已完成但已不在 API 中的）
        db = await get_db()
        try:
            cursor = await db.execute("SELECT remote_id FROM todos WHERE remote_id IS NOT NULL")
            db_ids = {row["remote_id"] for row in await cursor.fetchall()}
        finally:
            await db.close()

        # 合并两个 ID 集合（集合并集运算 | ），确保不遗漏
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
    """
    查询单个待办任务的详情。

    GET /api/todos/{uid}

    路径参数 {uid} 会自动传入函数的 uid 参数。
    例如 GET /api/todos/dida-abc123 会查询 uid="dida-abc123" 的任务。
    """
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM todos WHERE uid = ?", (uid,))
        row = await cursor.fetchone()
        if not row:
            return {"error": "Not found"}
        return dict(row)
    finally:
        await db.close()
