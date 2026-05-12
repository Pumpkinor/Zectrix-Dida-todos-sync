"""
同步日志路由模块

这个模块定义了同步日志的查询和清空 API 端点。

【API 端点一览】
  GET    /api/logs — 分页查询同步日志
  DELETE /api/logs — 清空所有同步日志

【同步日志的作用】
  每次 sync_engine 执行同步时，会往 sync_logs 表写入日志记录。
  前端"同步日志"Tab 展示这些记录，帮助用户了解同步状态。
"""

# 导入 FastAPI 核心类和查询参数工具
from fastapi import APIRouter, Query
# 导入数据库操作函数
from app.database import get_db

# 创建路由器实例
router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def list_logs(
    page: int = Query(1, ge=1),        # 页码，默认第1页，最小值1
    size: int = Query(20, ge=1, le=100),  # 每页条数，默认20，范围 1~100
):
    """
    分页查询同步日志。

    GET /api/logs?page=1&size=20

    按日志 ID 倒序排列（最新的在最前面）。

    返回格式:
      {
        "total": 50,
        "page": 1,
        "size": 20,
        "data": [
          {
            "id": 100,
            "action": "fetch",         // 动作类型
            "status": "success",       // 执行结果
            "detail": "Fetched 10...", // 详情
            "count": 10,               // 数据条数
            "created_at": "2026-05-09 10:30:00"  // 创建时间
          },
          ...
        ]
      }
    """
    db = await get_db()
    try:
        # 查询总记录数
        count_cursor = await db.execute("SELECT COUNT(*) as total FROM sync_logs")
        total = (await count_cursor.fetchone())["total"]

        # 计算分页偏移量并查询当前页数据
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
            "data": [dict(r) for r in rows],  # 将 Row 对象转换为字典
        }
    finally:
        await db.close()


@router.delete("")
async def clear_logs():
    """
    清空所有同步日志。

    DELETE /api/logs

    删除 sync_logs 表中的所有记录。不可恢复。
    """
    db = await get_db()
    try:
        await db.execute("DELETE FROM sync_logs")
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()
