"""
数据库模块

这个文件负责 SQLite 数据库的连接管理和初始化。
所有数据库操作都通过 aiosqlite（异步版 SQLite）执行。

【Python 知识点】
  - async/await: Python 的异步编程语法。
    在函数定义前加 async 表示这是一个"协程"（coroutine），
    调用时需要用 await 等待结果。
    类比 JavaScript 的 async function / await，语义完全一致。

  - aiosqlite: Python 标准 sqlite3 库的异步版本。
    因为 FastAPI 是异步框架，数据库操作也要用异步版本，避免阻塞。

  - PRAGMA: SQLite 特有的配置指令，用于设置数据库行为。

【数据库表结构】
  共 3 张表：
  1. todos     — 存储所有待办任务（来源于滴答清单或 Zectrix）
  2. sync_logs — 存储每次同步操作的日志记录
  3. config    — 存储键值对形式的系统配置
"""

# aiosqlite 是异步 SQLite 库，提供与标准 sqlite3 类似的接口但支持 async/await
import aiosqlite
# 从项目的 config 模块导入数据库文件路径常量
from app.config import DB_PATH

# ─── 建表 SQL 语句 ────────────────────────────────────────────────────
# 这是一个多行字符串（Python 用三引号 """...""" 表示），
# 包含了三张表的 CREATE TABLE 语句。
# IF NOT EXISTS 表示"如果表不存在才创建"，避免重复建表报错。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    uid TEXT PRIMARY KEY,             -- 全局唯一 ID，如 "dida-abc123" 或 "zectrix-456"
    title TEXT NOT NULL DEFAULT '',   -- 任务标题，不允许为 NULL
    description TEXT DEFAULT '',      -- 任务描述
    due_date TEXT,                    -- 截止日期 "YYYY-MM-DD"
    due_time TEXT,                    -- 截止时间 "HH:MM"
    priority INTEGER DEFAULT 0,      -- 优先级：0=无, 1=重要, 2=紧急
    completed INTEGER DEFAULT 0,     -- 是否完成：0=未完成, 1=已完成（SQLite 没有 bool 类型，用整数代替）
    completed_at TEXT,                -- 完成时间
    ical_raw TEXT DEFAULT '',         -- 原始 iCal 数据（iCal 模式下保存）
    last_modified TEXT,               -- 最后修改时间（用于增量同步判断）
    synced INTEGER DEFAULT 0,        -- 是否已同步到 Zectrix：0=未同步, 1=已同步
    synced_at TEXT,                   -- 同步完成的时间
    remote_id TEXT,                   -- Zectrix 上的任务 ID（同步后填入）
    remote_updated_at TEXT,           -- Zectrix 上任务的最后更新时间
    source TEXT DEFAULT 'dida',       -- 任务来源：'dida'=滴答清单, 'zectrix'=Zectrix 设备
    created_at TEXT DEFAULT (datetime('now', 'localtime')),  -- 记录创建时间（自动填入当前时间）
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))   -- 记录更新时间（自动填入当前时间）
);

CREATE TABLE IF NOT EXISTS sync_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- 自增主键
    action TEXT NOT NULL,                  -- 动作类型："fetch" / "sync" / "reverse_sync"
    status TEXT NOT NULL,                  -- 执行结果："success" / "failed" / "partial"
    detail TEXT DEFAULT '',                -- 详细描述
    count INTEGER DEFAULT 0,              -- 涉及数据条数
    created_at TEXT DEFAULT (datetime('now', 'localtime'))  -- 日志创建时间
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,           -- 配置项名称
    value TEXT DEFAULT ''           -- 配置项值（全部以字符串形式存储）
);
"""


async def get_db() -> aiosqlite.Connection:
    """
    获取一个数据库连接。

    每次调用都会创建新的连接（本项目中没有使用连接池）。
    调用方使用完毕后需要手动关闭（调用 await db.close()）。

    【Python 知识点】
      -> aiosqlite.Connection 是返回类型注解，表示这个函数返回一个 aiosqlite 连接对象。
    """
    db = await aiosqlite.connect(DB_PATH)    # 异步打开数据库文件
    db.row_factory = aiosqlite.Row           # 设置行工厂：让查询结果可以通过列名访问（像字典一样）
    await db.execute("PRAGMA journal_mode=WAL")    # WAL 模式：提高并发读写性能
    await db.execute("PRAGMA foreign_keys=ON")     # 启用外键约束
    return db


async def init_db():
    """
    初始化数据库。

    在 FastAPI 应用启动时（lifespan 函数中）调用一次。
    做三件事：
      1. 执行建表 SQL（表不存在时才创建）
      2. 将所有默认配置写入 config 表（已存在的不覆盖，INSERT OR IGNORE）
      3. 执行数据库迁移（给旧数据库添加新列）
    """
    db = await get_db()
    try:
        # 执行建表语句
        await db.executescript(_SCHEMA)

        # 导入默认配置项，写入 config 表
        from app.config import DEFAULTS
        for key, value in DEFAULTS.items():
            # INSERT OR IGNORE：如果 key 已存在则跳过，不覆盖用户已修改的配置
            await db.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                (key, value),
            )
        await db.commit()

        # ─── 以下是数据库迁移（Migration） ───
        # 随着项目迭代，表结构会变化。这里用 "ALTER TABLE ... ADD COLUMN" 添加新列。
        # 因为 ALTER TABLE ADD COLUMN 在列已存在时会报错，所以用 try/except 忽略错误。

        # 迁移1: 添加 remote_updated_at 列
        try:
            await db.execute("ALTER TABLE todos ADD COLUMN remote_updated_at TEXT")
            await db.commit()
        except Exception:
            pass  # 列已存在，忽略错误

        # 迁移2: 添加 source 列，并将 zectrix 来源的旧数据修正为 source='zectrix'
        try:
            await db.execute("ALTER TABLE todos ADD COLUMN source TEXT DEFAULT 'dida'")
            await db.commit()
            # UPDATE 语句：将 uid 以 "zectrix-" 开头且 source 还是默认值 'dida' 的行修正为 'zectrix'
            await db.execute("UPDATE todos SET source = 'zectrix' WHERE uid LIKE 'zectrix-%' AND source = 'dida'")
            await db.commit()
        except Exception:
            pass  # 列已存在，忽略错误

        # 迁移3: 添加 dida_task_id 列（存储滴答清单原始任务 ID，用于跨平台关联）
        try:
            await db.execute("ALTER TABLE todos ADD COLUMN dida_task_id TEXT")
            await db.commit()
        except Exception:
            pass

        # 迁移4: 添加 dida_project_id 列（存储滴答清单项目 ID）
        try:
            await db.execute("ALTER TABLE todos ADD COLUMN dida_project_id TEXT")
            await db.commit()
        except Exception:
            pass

        # 迁移5: 添加 reminders 列（存储滴答清单的提醒设置，JSON 格式）
        try:
            await db.execute("ALTER TABLE todos ADD COLUMN reminders TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass

        # 迁移6: 添加 repeat_flag 列（存储重复规则）
        try:
            await db.execute("ALTER TABLE todos ADD COLUMN repeat_flag TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass
    finally:
        # finally 块中的代码无论是否发生异常都会执行
        # 确保数据库连接一定被关闭
        await db.close()


async def get_config(key: str) -> str:
    """
    从 config 表中读取单个配置项的值。

    参数:
      key: 配置项名称，如 "sync_interval_minutes"

    返回:
      配置项的值（字符串），如果不存在返回空字符串 ""
    """
    db = await get_db()
    try:
        # 执行 SELECT 查询，? 是参数占位符（防止 SQL 注入）
        cursor = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cursor.fetchone()    # 获取第一行结果
        return row["value"] if row else ""  # 如果有结果返回 value 列，否则返回空字符串
    finally:
        await db.close()


async def set_config(key: str, value: str):
    """
    写入或更新 config 表中的一个配置项。

    使用 "UPSERT" 语义：如果 key 存在则更新，不存在则插入。

    参数:
      key:   配置项名称
      value: 要设置的值
    """
    db = await get_db()
    try:
        # ON CONFLICT(key) DO UPDATE：SQLite 的 UPSERT 语法
        # 如果 key 冲突（已存在），就执行 UPDATE
        await db.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
        await db.commit()    # 提交事务，使修改持久化
    finally:
        await db.close()


async def get_all_config() -> dict:
    """
    获取所有配置项，以字典形式返回。

    返回示例:
      {"ical_url": "", "sync_interval_minutes": "5", ...}
    """
    db = await get_db()
    try:
        cursor = await db.execute("SELECT key, value FROM config")
        rows = await cursor.fetchall()
        # 字典推导式：{表达式 for 变量 in 可迭代对象}
        # 这里遍历每一行，取 row["key"] 作为字典键，row["value"] 作为值
        return {row["key"]: row["value"] for row in rows}
    finally:
        await db.close()


async def add_sync_log(action: str, status: str, detail: str = "", count: int = 0):
    """
    写入一条同步日志。

    在 sync_engine 的各个步骤中调用，记录同步操作的执行情况。

    参数:
      action: 动作类型（如 "fetch", "sync", "reverse_sync"）
      status: 执行结果（"success", "failed", "partial"）
      detail: 详细描述
      count:  涉及的数据条数
    """
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO sync_logs (action, status, detail, count) VALUES (?, ?, ?, ?)",
            (action, status, detail, count),
        )
        await db.commit()
    finally:
        await db.close()


async def clear_sync_logs():
    """清空 sync_logs 表中的所有日志记录。"""
    db = await get_db()
    try:
        await db.execute("DELETE FROM sync_logs")
        await db.commit()
    finally:
        await db.close()
