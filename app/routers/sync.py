"""
同步触发路由模块

这个模块定义了手动触发同步和测试 Zectrix 连通性的 API 端点。

【Python / FastAPI 知识点】
  - APIRouter 是 FastAPI 的路由组织工具。一个大应用可以有多个 Router，
    每个 Router 负责一组相关的 API 端点，最后统一注册到主应用上。
    类似于 Spring Boot 的 @RestController。

  - @router.post("") 装饰器将函数绑定到 HTTP POST 请求。
    路径 "" 表示该 Router 前缀下的根路径。
    例如 prefix="/api/sync" + "" = POST /api/sync

  - async def 表示这是一个异步函数。FastAPI 会自动在事件循环中调度它。
"""

# 导入 FastAPI 的路由器类
from fastapi import APIRouter
# 导入同步引擎的核心函数和辅助函数
from app.services.sync_engine import run_sync, run_reverse_sync, _get_forwarder

# 创建路由器实例，设置 URL 前缀为 /api/sync，标签为 "sync"（用于 API 文档分组）
router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("")
async def trigger_sync():
    """
    手动触发一次完整同步。

    POST /api/sync

    调用 sync_engine.run_sync() 执行正向同步 + 反向同步。
    前端"立即同步"按钮调用此接口。
    """
    await run_sync()
    return {"ok": True, "message": "Sync completed"}


@router.get("/zectrix-test")
async def test_zectrix_fetch():
    """
    测试 Zectrix 连通性。

    GET /api/sync/zectrix-test

    尝试从 Zectrix 拉取待办列表，验证 API Key 和设备 ID 配置是否正确。
    主要用于调试和配置验证。
    """
    forwarder = await _get_forwarder()
    if not forwarder:
        return {"ok": False, "error": "Zectrix not configured (missing api_key or device_id)"}
    try:
        todos = await forwarder.fetch_remote_todos()
        return {"ok": True, "count": len(todos), "todos": todos}
    except Exception as e:
        return {"ok": False, "error": str(e)}
