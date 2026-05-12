"""
数据抓取模块 (Fetcher)

本模块负责从外部数据源（iCal 订阅源、滴答清单/Dida365 MCP 接口）抓取待办任务数据，
并将其统一转换为内部的 Todo 对象，供后续同步引擎（sync_engine）使用。

【模块主要功能】
  1. fetch_ical()   —— 通过 HTTP 请求获取 iCal 日历数据（.ics 格式），解析其中的 VTODO/VEVENT 事件
  2. fetch_dida_tasks() —— 通过 Dida365 的 MCP API 获取滴答清单的任务数据

【Python 知识点 —— import 导入语句】
  - import json              导入标准库 json 模块，用于 JSON 序列化/反序列化
  - import logging           导入标准库 logging 模块，用于记录运行日志
  - from datetime import X   从 datetime 模块中只导入 datetime 和 date 两个类
  - from typing import Optional  从 typing 模块导入 Optional 类型提示
    Optional[str] 等价于 "str 或 None"，表示该值可以为空
  - from zoneinfo import ZoneInfo 导入时区信息类，用于处理时区转换
  - import httpx             导入第三方 HTTP 客户端库 httpx（比标准库 requests 更现代，支持异步）
  - from icalendar import Calendar  导入第三方 iCal 日历解析库
  - from dateutil import parser as date_parser  导入第三方日期解析库，并给它取别名 date_parser

【Python 知识点 —— f-string（格式化字符串）】
  f"xxx {变量名}" 是 Python 3.6+ 的格式化字符串语法（f-string）。
  大括号 {} 中的变量/表达式会被自动替换为对应的值。
  例如: f"Fetched {len(todos)} todos" → 如果 todos 有 5 个元素，结果为 "Fetched 5 todos"
  等价于 Java 的 String.format() 或 JavaScript 的模板字符串 `xxx ${变量}`
"""

# ============================================================
# 导入部分
# ============================================================

import json                                    # JSON 处理：json.dumps() 把对象转成 JSON 字符串，json.loads() 把 JSON 字符串解析为对象
import logging                                 # 日志模块：用于输出运行信息到日志文件或控制台
from datetime import datetime, date            # datetime: 表示"日期+时间"（如 2026-05-09 14:30:00）；date: 表示"纯日期"（如 2026-05-09）
from typing import Optional                    # Optional[X] 类型提示：表示值可以是 X 类型，也可以是 None
from zoneinfo import ZoneInfo                  # 时区处理：ZoneInfo("Asia/Shanghai") 表示亚洲/上海时区（即中国标准时间 UTC+8）

import httpx                                   # 第三方 HTTP 客户端库，支持同步和异步请求。这里用它的异步功能 AsyncClient
from icalendar import Calendar                 # 第三方 iCal 日历解析库。Calendar.from_ical() 可以把 .ics 文本解析为日历对象
from dateutil import parser as date_parser     # 第三方日期解析库，date_parser.isoparse() 可以解析 ISO 8601 格式的日期字符串

from app.models import Todo                    # 从本项目 app/models.py 导入 Todo 数据模型类

# ============================================================
# 模块级常量和日志配置
# ============================================================

# __name__ 在模块级别运行时等于模块的路径名（如 "app.services.fetcher"）
# logging.getLogger() 创建一个以该名称命名的日志记录器，方便在日志中追踪信息来源
logger = logging.getLogger(__name__)

# 默认时区：亚洲/上海（即中国标准时间 CST，UTC+8）
DEFAULT_TIMEZONE = "Asia/Shanghai"

# iCal 优先级映射表（字典/dict）
# iCal 标准中优先级范围是 1（最高）到 5（最低）
# 本系统使用 0/1/2 三级：0=无优先级, 1=普通, 2=重要/紧急
# {键: 值} —— 通过 PRIORITY_MAP[1] 可以查到值 2
PRIORITY_MAP = {1: 2, 2: 1, 3: 1, 4: 0, 5: 0}  # iCal 1(highest)-5(lowest) → 0/1/2


# ============================================================
# iCal 数据解析辅助函数
# ============================================================

def _unwrap(val):
    """Unwrap icalendar value types to native Python types."""
    # 以 _ 开头的函数是 Python 的"私有函数"约定（仅命名约定，不是强制的）
    # 这个函数的作用：把 icalendar 库返回的特殊类型（如 vDDDTypes、vText）转换为 Python 原生类型

    if val is None:
        return None
    # hasattr(val, 'dt') 检查对象 val 是否有 'dt' 属性
    # icalendar 库的日期/时间类型（如 vDDDTypes）都有 .dt 属性，它存储了真正的 Python datetime/date 对象
    if hasattr(val, 'dt'):
        return val.dt
    # isinstance(val, (datetime, date)) 检查 val 是否是 datetime 或 date 类型
    # (datetime, date) 是元组，isinstance 第二个参数可以是类型元组，匹配其中任意一个即可
    if isinstance(val, (datetime, date)):
        return val
    if isinstance(val, str):
        return val
    # 其他类型统一转为字符串
    return str(val)


def _parse_datetime(dt) -> Optional[str]:
    """
    将 iCal 日期时间值解析为完整的日期时间字符串。

    【参数】 dt —— iCal 组件中的日期时间值（可能是 icalendar 库的特殊类型）
    【返回值】 格式化后的字符串，如 "2026-05-09 14:30:00"；如果输入为 None 则返回 None

    【Python 知识点 —— -> Optional[str]】
      函数定义中 -> 后面的类型叫做"返回值类型注解"，表示这个函数返回 str 或 None。
      这只是给人和工具看的提示，Python 运行时不会强制检查。
    """
    dt = _unwrap(dt)                  # 先把 icalendar 特殊类型拆包为 Python 原生类型
    if dt is None:
        return None
    if isinstance(dt, datetime):      # 如果是 datetime 类型（带时间的），格式化为 "YYYY-MM-DD HH:MM:SS"
        # strftime = string format time，用于把 datetime 对象格式化为指定格式的字符串
        # %Y=四位年份, %m=两位月份, %d=两位日期, %H=24小时制小时, %M=分钟, %S=秒
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(dt, date):          # 如果是 date 类型（只有日期没有时间），格式化为 "YYYY-MM-DD"
        return dt.strftime("%Y-%m-%d")
    return str(dt)                    # 其他情况（如字符串），直接转为字符串返回


def _parse_date_only(dt) -> Optional[str]:
    """
    将 iCal 日期时间值解析为纯日期字符串（不含时间部分）。

    【参数】 dt —— iCal 组件中的日期时间值
    【返回值】 格式化后的日期字符串，如 "2026-05-09"；如果无法解析则返回 None
    """
    dt = _unwrap(dt)
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")   # datetime 也有 strftime 方法，只取日期部分
    if isinstance(dt, date):
        return dt.strftime("%Y-%m-%d")
    return None                          # 既不是 datetime 也不是 date，返回 None


def _parse_time_only(dt) -> Optional[str]:
    """
    将 iCal 日期时间值解析为纯时间字符串（不含日期部分）。

    【参数】 dt —— iCal 组件中的日期时间值
    【返回值】 格式化后的时间字符串，如 "14:30"；如果无法解析则返回 None
    """
    dt = _unwrap(dt)
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.strftime("%H:%M")      # 只取小时:分钟部分
    return None


def _parse_priority(ical_priority) -> int:
    """
    将 iCal 优先级转换为本系统使用的 0/1/2 三级优先级。

    【参数】 ical_priority —— iCal 格式的优先级（整数 1-5，或 None）
    【返回值】 本系统的优先级：0=无, 1=普通, 2=重要
    """
    if ical_priority is None:
        return 0                          # 没有设置优先级，默认为 0（无优先级）
    val = int(ical_priority)              # int() 将值转换为整数
    # dict.get(key, default) 方法：在字典中查找 key，找不到就返回 default 值（这里是 0）
    return PRIORITY_MAP.get(val, 0)


def _component_to_raw_text(component) -> str:
    """Convert a component back to raw iCal text for storage."""
    # component.to_ical() 把 icalendar 组件对象序列化为 iCal 格式的字节串（bytes）
    # .decode("utf-8", errors="replace") 把字节串解码为 UTF-8 字符串，遇到无法解码的字节用 � 替换
    return component.to_ical().decode("utf-8", errors="replace")


def _parse_vevent(component) -> Todo:
    """
    将 iCal VEVENT（日历事件）组件解析为 Todo 对象。

    TickTick/滴答清单通过 iCal 订阅导出任务时，每个任务对应一个 VEVENT 事件。

    【参数】 component —— icalendar 库解析出的一个 VEVENT 组件对象
    【返回值】 一个填充好字段的 Todo 对象

    【关于 VEVENT】
      VEVENT 是 iCal 日历标准中的"事件"类型。滴答清单把任务导出为 VEVENT 而非标准的 VTODO，
      所以需要单独的解析逻辑。关键字段映射：
        UID        → 任务唯一标识
        SUMMARY    → 任务标题
        DTSTART    → 开始时间（滴答清单映射为截止日期）
        STATUS     → 状态（COMPLETED=已完成, CANCELLED=已取消）
        COMPLETED  → 完成时间（有此字段则表示已完成）
    """
    # component.get("UID", "") 从组件中获取 UID 属性，不存在则返回空字符串 ""
    # str() 把 icalendar 的 vText 类型转为 Python 字符串
    uid = str(component.get("UID", ""))
    summary = str(component.get("SUMMARY", ""))
    # or "" 是一种常见的 Python 技巧：当左边值为假值（如 None、空字符串、0）时，使用右边的值
    # 这里防止 DESCRIPTION 为 None 时，str(None) 变成 "None" 字符串
    description = str(component.get("DESCRIPTION", "") or "")

    dtstart = component.get("DTSTART")   # 事件的开始时间（滴答清单把它作为截止日期使用）
    dtend = component.get("DTEND")       # 事件的结束时间
    due_date = _parse_date_only(dtstart) # 从开始时间中提取日期部分
    due_time = _parse_time_only(dtstart) # 从开始时间中提取时间部分

    # All-day events: dtstart is date only, no time
    # 全天事件：dtstart 只包含日期不含时间。此时 due_time 保留为 None 表示全天任务
    if due_date and not due_time:
        due_time = None

    # 判断任务是否已完成
    completed = False
    # 检查 STATUS 属性：COMPLETED 或 CANCELLED 都视为已完成
    status_val = component.get("STATUS")
    if status_val and str(status_val).upper() in ("COMPLETED", "CANCELLED"):
        # .upper() 把字符串转大写，确保比较时不区分大小写
        # in (a, b) 检查值是否在元组 (a, b) 中
        completed = True
    # 检查 COMPLETED 属性：如果存在（有完成时间戳），也视为已完成
    completed_val = component.get("COMPLETED")
    if completed_val is not None:
        completed = True

    raw = _component_to_raw_text(component)  # 保留原始 iCal 文本，用于调试和可能的回写

    # 创建并返回 Todo 对象
    # Todo(...) 调用 Todo 类的构造函数，用关键字参数为每个字段赋值
    return Todo(
        uid=uid,
        title=summary,
        description=description,
        due_date=due_date,
        due_time=due_time,
        priority=_parse_priority(component.get("PRIORITY")),
        completed=completed,
        completed_at=_parse_datetime(completed_val),
        ical_raw=raw,
        # LAST-MODIFIED 不存在时回退到 DTSTAMP（日历组件创建/更新时间戳）
        last_modified=_parse_datetime(component.get("LAST-MODIFIED") or component.get("DTSTAMP")),
    )


def _parse_vtodo(component) -> Todo:
    """
    将 iCal VTODO（待办事项）组件解析为 Todo 对象。

    VTODO 是 iCal 日历标准中的"待办事项"类型，比 VEVENT 更适合表示任务。

    【参数】 component —— icalendar 库解析出的一个 VTODO 组件对象
    【返回值】 一个填充好字段的 Todo 对象

    【关键字段映射】
      UID        → 任务唯一标识
      SUMMARY    → 任务标题
      DUE        → 截止日期/时间（VTODO 标准字段）
      STATUS     → 状态（COMPLETED=已完成）
      COMPLETED  → 完成时间戳
    """
    uid = str(component.get("UID", ""))
    due = component.get("DUE")            # VTODO 用 DUE 字段表示截止日期（VEVENT 用 DTSTART）
    due_date = _parse_date_only(due)
    due_time = _parse_time_only(due)

    completed_val = component.get("COMPLETED")
    # completed_val is not None：只要 COMPLETED 字段存在（不论值是什么），就认为任务已完成
    completed = completed_val is not None

    status_val = component.get("STATUS")
    if status_val and str(status_val).upper() == "COMPLETED":
        completed = True

    raw = _component_to_raw_text(component)

    return Todo(
        uid=uid,
        title=str(component.get("SUMMARY", "")),
        description=str(component.get("DESCRIPTION", "") or ""),
        due_date=due_date,
        due_time=due_time,
        priority=_parse_priority(component.get("PRIORITY")),
        completed=completed,
        completed_at=_parse_datetime(completed_val),
        ical_raw=raw,
        last_modified=_parse_datetime(component.get("LAST-MODIFIED")),
    )


# ============================================================
# iCal 数据源抓取（主函数）
# ============================================================

async def fetch_ical(url: str) -> list[Todo]:
    """
    通过 HTTP 请求获取 iCal 日历订阅数据，解析其中的 VTODO 和 VEVENT 组件为 Todo 对象列表。

    【参数】 url —— iCal 订阅源的 URL 地址（通常以 webcal:// 或 https:// 开头）
    【返回值】 解析后的 Todo 对象列表（Python 的 list 类型）

    【Python 知识点 —— async/await 异步编程】
      - async def 定义的函数叫做"异步函数"或"协程"
      - await 关键字表示"等待异步操作完成"，在等待期间不会阻塞程序，其他代码可以继续执行
      - 异步编程特别适合网络请求这种"等待时间较长"的操作，可以在等待网络响应的同时做别的事
      - 比喻：同步 = 排队等外卖；异步 = 拿号去干别的，到了再取

    【Python 知识点 —— list[Todo]】
      表示返回值是一个列表（Python 的 list），列表中每个元素都是 Todo 类型。
      这是 Python 3.9+ 的类型注解语法，等价于早期版本的 List[Todo]。
    """
    # webcal:// 是苹果日历等应用使用的协议前缀，实际就是 HTTPS
    # 需要替换为 https:// 才能用 httpx 发起 HTTP 请求
    fetch_url = url.replace("webcal://", "https://")

    # async with 是异步上下文管理器，确保用完后正确关闭 HTTP 连接
    # httpx.AsyncClient 是异步 HTTP 客户端，timeout=30 设置请求超时时间为 30 秒
    async with httpx.AsyncClient(timeout=30) as client:
        # await 等待异步 HTTP GET 请求完成，response 变量存储服务器响应
        response = await client.get(fetch_url)
        # raise_for_status() 检查 HTTP 状态码，如果不是 2xx（成功），就抛出异常
        response.raise_for_status()

    # Calendar.from_ical() 把 iCal 格式的文本字符串解析为日历对象
    # response.text 是服务器返回的响应正文（文本形式）
    cal = Calendar.from_ical(response.text)
    todos = []                              # 创建空列表，用于收集解析后的 Todo 对象

    # cal.walk() 遍历日历对象中的所有组件（包括 VEVENT、VTODO、VTIMEZONE 等）
    for component in cal.walk():
        if component.name == "VTODO":       # 组件名是 VTODO → 用 VTODO 解析器
            todo = _parse_vtodo(component)
        elif component.name == "VEVENT":    # 组件名是 VEVENT → 用 VEVENT 解析器
            todo = _parse_vevent(component)
        else:
            continue                        # 其他类型的组件（如 VTIMEZONE）跳过不管

        if not todo.uid:                    # 跳过没有 UID 的任务（无法唯一标识，不应纳入同步）
            continue
        # todos.append(todo) 把解析好的 Todo 对象添加到列表末尾
        todos.append(todo)

    # f-string：在字符串中嵌入表达式的值。len(todos) 返回列表的长度（元素个数）
    logger.info(f"Fetched {len(todos)} todos from iCal feed")
    return todos


# ============================================================
# 滴答清单 (Dida365) 数据解析辅助函数
# ============================================================

def _dida_priority_to_local(p: int) -> int:
    """
    将滴答清单的优先级转换为本系统的 0/1/2 三级优先级。

    【参数】 p —— 滴答清单优先级（0=无, 1=低, 3=中, 5=高）
    【返回值】 本系统优先级（0=无, 1=普通, 2=重要）
    """
    # {0: 0, 1: 1, 3: 1, 5: 2} 是一个字典（dict），用 .get(p, 0) 查找 p 对应的值
    # 如果 p 不在字典的键中（如 p=2 或 p=4），则返回默认值 0
    return {0: 0, 1: 1, 3: 1, 5: 2}.get(p, 0)


def _parse_dida_datetime(d, timezone_name: str | None = None) -> datetime | None:
    """
    解析滴答清单返回的日期时间字符串为 Python datetime 对象，并转换到指定时区。

    【参数】
      d             —— 滴答清单返回的日期时间值（通常是字符串或 None）
      timezone_name —— 目标时区名称（如 "Asia/Shanghai"），默认使用 DEFAULT_TIMEZONE
    【返回值】 转换后的 datetime 对象，或 None（输入为空时）

    【Python 知识点 —— str | None 类型注解】
      str | None 是 Python 3.10+ 引入的联合类型语法，等价于 Optional[str] 或 Union[str, None]。
      表示参数可以是 str 类型或 None。

    【Python 知识点 —— try/except 异常处理】
      try:
          ...（可能出错的代码）
      except (TypeError, ValueError):
          ...（出错后执行的处理代码）
      如果 try 块中的代码抛出了 TypeError 或 ValueError 类型的异常，
      程序不会崩溃，而是跳到 except 块中执行。
    """
    if not d:
        return None
    try:
        # date_parser.isoparse() 解析 ISO 8601 格式的日期字符串
        # ISO 8601 格式示例: "2026-05-09T14:30:00+08:00"
        dt = date_parser.isoparse(str(d))
    except (TypeError, ValueError):
        # 如果解析失败（格式不正确），返回 None
        return None
    # dt.tzinfo 检查 datetime 对象是否携带时区信息
    # 如果没有时区信息（naive datetime），直接返回，不做时区转换
    if dt.tzinfo is None:
        return dt
    try:
        # ZoneInfo(时区名) 创建时区对象
        target_tz = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except Exception:
        # 如果时区名无效，回退到默认时区
        target_tz = ZoneInfo(DEFAULT_TIMEZONE)
    # .astimezone(target_tz) 把 datetime 转换到目标时区
    return dt.astimezone(target_tz)


def _parse_dida_date(d, timezone_name: str | None = None) -> str | None:
    """
    将滴答清单的日期时间值解析为纯日期字符串（YYYY-MM-DD 格式）。

    【参数】
      d             —— 滴答清单返回的日期时间值
      timezone_name —— 时区名称
    【返回值】 日期字符串如 "2026-05-09"，或 None
    """
    if not d:
        return None
    dt = _parse_dida_datetime(d, timezone_name)
    if dt:
        return dt.strftime("%Y-%m-%d")       # 格式化为 "年-月-日"
    # 如果日期时间解析失败，尝试直接截取字符串前 10 个字符（YYYY-MM-DD 刚好 10 个字符）
    s = str(d)
    return s[:10] if len(s) >= 10 else None   # s[:10] 取字符串的第 0 到第 9 个字符（共 10 个）


def _parse_dida_time(d, timezone_name: str | None = None) -> str | None:
    """
    将滴答清单的日期时间值解析为纯时间字符串（HH:MM 格式）。

    【参数】
      d             —— 滴答清单返回的日期时间值
      timezone_name —— 时区名称
    【返回值】 时间字符串如 "14:30"，或 None
    """
    if not d:
        return None
    dt = _parse_dida_datetime(d, timezone_name)
    if dt:
        return dt.strftime("%H:%M")           # 格式化为 "小时:分钟"
    # 如果日期时间解析失败，尝试从 ISO 格式字符串中手动截取时间部分
    s = str(d)
    # ISO 格式: "2026-05-09T14:30:00+08:00" —— T 后面第 11~16 个字符就是 "HH:MM"
    if "T" in s and len(s) > 16:
        return s[11:16]                       # s[11:16] 取第 11 到第 15 个字符（共 5 个）
    return None


# ============================================================
# 滴答清单 (Dida365) 数据源抓取（主函数）
# ============================================================

async def fetch_dida_tasks() -> list[Todo]:
    """
    通过 Dida365 的 MCP（Model Context Protocol）API 获取滴答清单的任务数据。

    返回解析后的 Todo 对象列表，包含已完成和未完成的任务。

    【返回值】 Todo 对象列表

    【Python 知识点 —— 函数内的 import】
      在函数内部使用 import（而非文件顶部），叫做"延迟导入"或"局部导入"。
      好处：避免循环导入问题（A 导入 B，B 又导入 A），仅在需要时才加载模块。

    【Python 知识点 —— 列表推导式（List Comprehension）】
      [表达式 for 变量 in 可迭代对象 if 条件]
      是一种简洁的列表生成语法。
      示例: [p.strip() for p in text.split(",") if p.strip()]
      等价于:
        result = []
        for p in text.split(","):
            if p.strip():
                result.append(p.strip())
    """
    # 延迟导入：避免模块级别的循环依赖
    from app.services.dida_client import get_dida_mcp_client  # 滴答清单 MCP 客户端工厂函数
    from app.database import get_config                       # 数据库配置读取函数
    from datetime import date, timedelta                      # timedelta 表示时间差（如"30天前"）

    # 获取 MCP 客户端实例（需要配置 dida_mcp_token 才能创建）
    client = await get_dida_mcp_client()
    if not client:
        # raise Exception(...) 抛出异常，中断当前函数执行，通知调用方出错了
        raise Exception("Dida365 MCP not configured (missing dida_mcp_token)")

    # 初始化 MCP 客户端连接
    await client.initialize()

    # 从数据库配置中读取用户设定的滴答清单项目 ID（多个用逗号分隔）
    project_id_raw = await get_config("dida_project_id")
    # 列表推导式：
    #   project_id_raw.split(",") 把 "id1,id2,id3" 拆分为 ["id1", " id2", " id3"]
    #   p.strip() 去除每个元素前后的空白字符
    #   if p.strip() 过滤掉空字符串（如输入 "id1,,id2" 中间的空段）
    project_ids = [p.strip() for p in project_id_raw.split(",") if p.strip()] if project_id_raw else []

    # 如果用户没有配置项目 ID，则获取所有项目并使用全部
    if not project_ids:
        projects = await client.list_projects()
        if projects:
            # 从项目列表中提取每个项目的 "id" 字段，组成新的列表
            project_ids = [p["id"] for p in projects]
            logger.info(f"No project selected, using all {len(project_ids)} projects")
        else:
            raise Exception("No Dida365 projects found")

    logger.info(f"Fetching from Dida365 projects: {project_ids}")

    all_raw = []                            # 收集所有项目的原始任务数据
    today = date.today()                    # 获取今天的日期
    # timedelta(days=30) 表示 30 天的时间差
    # (today - timedelta(days=30)).isoformat() 得到 30 天前的日期，格式为 "YYYY-MM-DD"
    start = (today - timedelta(days=30)).isoformat()
    end = today.isoformat()                 # 今天的日期

    # set() 创建集合（set），集合查找速度比列表快得多（O(1) vs O(n)）
    # 后面用 project_id_set 来快速判断任务是否属于目标项目
    project_id_set = set(project_ids)

    # 遍历每个项目 ID，分别获取未完成和已完成的任务
    for pid in project_ids:
        # 获取该项目下所有未完成的任务
        undone = await client.get_undone_tasks(pid)
        # 获取该项目在过去 30 天内完成的任务
        completed = await client.get_completed_tasks([pid], start, end)
        # undone + completed 把两个列表合并为一个新列表（+ 运算符连接列表）
        combined = undone + completed
        # MCP may return tasks from other projects — filter strictly
        # 过滤：只保留属于目标项目的任务（MCP 有时会返回其他项目的任务）
        # 列表推导式中的条件：t.get("projectId") in project_id_set 检查任务的项目 ID 是否在目标集合中
        # or 连接两个条件，因为不同 API 版本返回的字段名可能不同（projectId 或 project_id）
        combined = [t for t in combined if t.get("projectId") in project_id_set or t.get("project_id") in project_id_set]
        logger.info(f"  Project {pid}: {len(undone)} undone + {len(completed)} completed = {len(combined)} after filter")
        # all_raw.extend(combined) 把 combined 列表中的所有元素追加到 all_raw 列表末尾
        # extend 和 append 的区别：extend 把列表拆开逐个添加，append 把整个列表作为一个元素添加
        all_raw.extend(combined)

    raw_tasks = all_raw
    logger.info(f"Dida365 MCP: {len(raw_tasks)} total tasks from {len(project_ids)} projects")

    # 将原始任务数据转换为 Todo 对象
    todos = []
    for t in raw_tasks:
        # t.get("status", 0) 获取 status 字段，不存在则默认为 0
        # 滴答清单 status: 0=未完成, 2=已完成
        status = t.get("status", 0)
        is_completed = status == 2
        # 优先使用 dueDate（截止日期），没有则用 startDate（开始日期）
        due = t.get("dueDate") or t.get("startDate")
        timezone_name = t.get("timeZone") or DEFAULT_TIMEZONE

        # Serialize reminders and repeat as JSON strings for storage
        # 把提醒设置序列化为 JSON 字符串存入数据库
        # json.dumps() 把 Python 对象转为 JSON 字符串
        # ensure_ascii=False 允许 JSON 中包含非 ASCII 字符（如中文）
        reminders_raw = t.get("reminders")
        reminders_str = json.dumps(reminders_raw, ensure_ascii=False) if reminders_raw else ""
        repeat_str = t.get("repeatFlag") or ""    # 重复规则，如 "RRULE:FREQ=DAILY;INTERVAL=1"

        # f"dida-{t['id']}" 生成唯一标识符，前缀 "dida-" 表示来源是滴答清单
        # t['id'] 和 t.get('id') 的区别：前者在键不存在时会抛出 KeyError，后者返回 None
        todo = Todo(
            uid=f"dida-{t['id']}",
            title=t.get("title", ""),
            description=t.get("content", "") or t.get("desc", "") or "",
            due_date=_parse_dida_date(due, timezone_name),
            # 三元表达式：值1 if 条件 else 值2 —— 条件为真取值1，否则取值2
            # isAllDay 为 True 时不设时间（全天任务），否则解析具体时间
            due_time=None if t.get("isAllDay") else _parse_dida_time(due, timezone_name),
            priority=_dida_priority_to_local(t.get("priority", 0)),
            completed=is_completed,
            completed_at=t.get("completedTime"),
            ical_raw="",                 # MCP 模式不使用 iCal 原始数据，设为空字符串
            last_modified=t.get("modifiedTime"),
            reminders=reminders_str,
            repeat_flag=repeat_str,
        )
        # 在 Todo 对象上动态添加自定义属性（以下划线 _ 开头表示内部使用）
        # Python 允许在运行时给对象添加任意属性，这叫"动态属性"
        # _dida_task_id 和 _dida_project_id 不在 Todo 类定义中，但在这里临时挂载
        # 后续反向同步（从本系统写回滴答清单）时需要用到这些原始 ID
        todo._dida_task_id = t["id"]
        todo._dida_project_id = t.get("projectId") or t.get("project_id") or project_ids[0]
        todos.append(todo)

    return todos
