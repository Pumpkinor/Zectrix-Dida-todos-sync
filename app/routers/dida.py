"""
滴答清单项目列表路由模块

这个模块提供了一个获取滴答清单项目列表的 API 端点。
用于前端配置页面中"获取项目"按钮的交互。

【API 端点】
  GET /api/dida/projects — 获取滴答清单的所有项目列表

【为什么需要这个接口？】
  用户配置同步时，需要选择要同步哪些滴答清单项目。
  前端调用这个接口获取项目列表，展示复选框供用户勾选。
"""

import logging
# 导入 FastAPI 核心类
from fastapi import APIRouter
# 导入数据库配置读取函数
from app.database import get_config
# 导入滴答清单 MCP 客户端
from app.services.dida_client import get_dida_mcp_client

# 创建日志记录器
logger = logging.getLogger(__name__)

# 创建路由器实例。prefix="/api/dida" 表示所有端点的 URL 以 /api/dida 开头
router = APIRouter(prefix="/api/dida", tags=["dida"])


@router.get("/projects")
async def dida_projects():
    """
    获取滴答清单的所有项目列表。

    GET /api/dida/projects

    通过 MCP API 连接滴答清单，获取用户的所有项目（包括默认收集箱和自定义项目）。
    前端展示为复选框列表，用户可以勾选要同步的项目。

    返回示例:
      {"projects": [{"id": "inbox123", "name": "收集箱"}, {"id": "proj456", "name": "工作"}]}

    错误情况:
      {"error": "MCP token not configured"}  — 未配置 API 口令
      {"error": "连接超时"}                    — 网络错误
    """
    client = await get_dida_mcp_client()
    if not client:
        return {"error": "MCP token not configured"}

    try:
        await client.initialize()  # MCP 协议握手（必须先初始化才能调用其他方法）
        projects = await client.list_projects()
        # 只返回 id 和 name 两个字段（过滤掉不需要的元数据）
        result = [{"id": p.get("id", ""), "name": p.get("name", "")} for p in projects]
        logger.info(f"Dida /projects API: returning {len(result)} projects")
        return {"projects": result}
    except Exception as e:
        logger.error(f"Dida /projects API failed: {e}", exc_info=True)
        return {"error": str(e)}
