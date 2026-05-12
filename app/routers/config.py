"""
配置管理路由模块

这个模块定义了系统配置的读写 API 端点，以及 iCal Feed 令牌生成。

【API 端点一览】
  GET  /api/config                    — 获取所有配置项
  PUT  /api/config                    — 更新配置项
  POST /api/config/generate-feed-token — 生成 iCal Feed 访问令牌

【Python / FastAPI 知识点】
  - BaseModel 是 Pydantic 库的基类，用于定义请求数据的结构（数据验证）。
    当前端发送 JSON 请求时，FastAPI 会自动将 JSON 解析为这个模型的实例，
    并验证字段类型是否正确。类似于 Java 的 DTO（数据传输对象）。

  - Optional[str] = None 表示这个字段可以不传（可选字段）。
    前端可以只传需要更新的字段，未传的字段值为 None。

  - Union[str, int] 表示这个字段的值可以是字符串或整数。
    因为前端可能发送数字类型的值（如 sync_interval_minutes: 5），
    但数据库存储的是字符串，所以需要兼容两种类型。
"""

# 导入 Python 标准库的随机令牌生成模块
import secrets

# 导入 FastAPI 核心类
from fastapi import APIRouter
# 导入 Pydantic 的数据验证基类
from pydantic import BaseModel
# 导入类型提示工具
from typing import Optional, Union
# 导入数据库配置操作函数
from app.database import get_all_config, set_config, get_config as _get_config

# 创建路由器实例
router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdate(BaseModel):
    """
    配置更新请求的数据模型。

    前端发送 PUT /api/config 时，请求体中的 JSON 会被解析为这个类的实例。
    每个字段对应一个配置项，所有字段都是可选的（Optional）。
    只传需要更新的字段即可，未传的字段不会被修改。
    """
    ical_url: Optional[str] = None                # iCal 订阅地址
    zectrix_api_key: Optional[str] = None         # Zectrix API Key
    zectrix_base_url: Optional[str] = None        # Zectrix API 地址
    zectrix_device_id: Optional[str] = None       # Zectrix 设备 MAC 地址
    sync_interval_minutes: Optional[Union[str, int]] = None  # 同步间隔（分钟），兼容字符串和数字
    bidirectional_enabled: Optional[str] = None   # 是否启用双向同步
    feed_token: Optional[str] = None              # iCal Feed 令牌
    email_smtp_host: Optional[str] = None         # SMTP 服务器地址
    email_smtp_port: Optional[Union[str, int]] = None  # SMTP 端口
    email_smtp_user: Optional[str] = None         # SMTP 用户名
    email_smtp_password: Optional[str] = None     # SMTP 密码
    email_from: Optional[str] = None              # 发件人邮箱
    email_to_dida: Optional[str] = None           # 滴答清单任务邮箱
    dida_mcp_token: Optional[str] = None          # 滴答清单 MCP API 口令
    dida_project_id: Optional[str] = None         # 要同步的滴答清单项目 ID（逗号分隔）
    dida_sync_mode: Optional[str] = None          # 数据来源方式："mcp" / "ical"
    reverse_sync_mode: Optional[str] = None       # 反向同步方式："mcp" / "feed" / "email" / "none"


@router.get("")
async def get_configuration():
    """
    获取所有配置项。

    GET /api/config

    返回一个字典，键是配置项名称，值是配置项值（均为字符串）。
    例如: {"ical_url": "", "sync_interval_minutes": "5", ...}
    """
    return await get_all_config()


@router.post("/generate-feed-token")
async def generate_feed_token():
    """
    生成 iCal Feed 的访问令牌。

    POST /api/config/generate-feed-token

    生成一个随机的 URL 安全令牌，用于保护 iCal Feed 端点不被未授权访问。
    Feed URL 格式为: http://host:port/feed/{token}.ics
    """
    # secrets.token_urlsafe(16) 生成一个 16 字节长度的随机令牌（URL 安全字符串）
    token = secrets.token_urlsafe(16)
    await set_config("feed_token", token)
    return {"ok": True, "feed_token": token}


@router.put("")
async def update_configuration(body: ConfigUpdate):
    """
    更新配置项。

    PUT /api/config

    请求体示例:
      {"sync_interval_minutes": 10, "dida_sync_mode": "mcp"}

    只更新传入的字段，未传的字段不受影响。
    如果修改了同步间隔，会自动重新调度定时任务。
    """
    # body.model_dump(exclude_none=True) 将模型转换为字典，排除值为 None 的字段
    updates = body.model_dump(exclude_none=True)
    for key, value in updates.items():
        # str(value) 将所有值转为字符串存储（数据库的 config 表 value 列是 TEXT 类型）
        await set_config(key, str(value))

    # 如果同步间隔被修改了，需要重新调度定时任务
    if "sync_interval_minutes" in updates:
        from app.scheduler import reschedule_sync
        await reschedule_sync()

    return {"ok": True, "updated": list(updates.keys())}
