"""
全局配置模块

这个文件定义了项目运行所需的路径常量和配置项默认值。

【Python 知识点】
  - os.path.dirname() / os.path.join() 是 Python 处理文件路径的标准方式。
    dirname 获取目录名，join 拼接路径（自动处理斜杠方向）。

  - os.makedirs(path, exist_ok=True) 创建目录。
    exist_ok=True 表示"如果目录已存在不报错"。

  - DEFAULTS 是一个字典（dict），键是配置项名称，值是默认值。
    字典用 {key: value, ...} 语法定义。
"""

import os
import secrets

# ─── 路径常量 ────────────────────────────────────────────────────────

# __file__ 是 Python 内置变量，指向当前文件（config.py）的路径
# os.path.abspath(__file__) 获取绝对路径，例如 "E:\code\todo-list-trans\app\config.py"
# os.path.dirname() 取其父目录，得到 "E:\code\todo-list-trans\app"
# 再取一次 dirname，得到项目根目录 "E:\code\todo-list-trans"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据目录，存放 SQLite 数据库文件
DATA_DIR = os.path.join(BASE_DIR, "data")

# SQLite 数据库文件的完整路径
DB_PATH = os.path.join(DATA_DIR, "todos.db")

# 前端静态文件目录
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# 确保数据目录存在（首次运行时自动创建）
os.makedirs(DATA_DIR, exist_ok=True)

# ─── 配置项默认值 ────────────────────────────────────────────────────
#
# 这些默认值在数据库初始化时写入 config 表。
# 用户可以通过 Web 管理界面修改这些值。
# 键名对应数据库 config 表的 key 列，值对应 value 列。
#
DEFAULTS = {
    # --- iCal 相关 ---
    "ical_url": "",                   # iCal 订阅地址（仅在 dida_sync_mode=ical 时使用）

    # --- Zectrix 连接配置 ---
    "zectrix_api_key": "",            # Zectrix Cloud 平台的 X-API-Key 认证密钥
    "zectrix_base_url": "https://cloud.zectrix.com",  # Zectrix Cloud API 基础地址
    "zectrix_device_id": "",          # Zectrix 墨水屏设备的 MAC 地址（如 "AA:BB:CC:DD:EE:FF"）

    # --- 同步频率 ---
    "sync_interval_minutes": "5",     # 自动同步间隔，单位：分钟

    # --- 双向同步开关 ---
    "bidirectional_enabled": "false",  # 是否启用双向同步（当前版本始终启用）

    # --- iCal Feed 相关 ---
    "feed_token": "",                 # iCal Feed 的访问令牌（随机生成，防止未授权访问）

    # --- 邮件发送配置（反向同步方式之一） ---
    "email_smtp_host": "",            # SMTP 服务器地址，如 "smtp.qq.com"
    "email_smtp_port": "465",         # SMTP 端口。465=SSL直连, 587=STARTTLS
    "email_smtp_user": "",            # SMTP 登录用户名
    "email_smtp_password": "",        # SMTP 登录密码或授权码
    "email_from": "",                 # 发件人邮箱地址
    "email_to_dida": "",              # 滴答清单的任务创建邮箱（发给这个邮箱 = 在滴答清单创建任务）

    # --- 滴答清单 MCP API 配置 ---
    "dida_mcp_token": "",             # 滴答清单 API 口令（在滴答清单 App 的"设置→账户与安全→API口令"获取）
    "dida_project_id": "",            # 要同步的滴答清单项目 ID，多个用逗号分隔。空=同步所有项目
    "dida_sync_mode": "mcp",          # 数据来源方式："mcp"=MCP API（推荐）, "ical"=iCal 订阅

    # --- 反向同步配置 ---
    "reverse_sync_mode": "mcp",       # 反向同步方式：
                                      #   "mcp"   = 通过 MCP API 回写（推荐）
                                      #   "feed"  = 生成 iCal 订阅链接
                                      #   "email" = 通过邮件发送到滴答清单
                                      #   "none"  = 关闭反向同步
}
