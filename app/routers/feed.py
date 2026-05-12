"""
iCal Feed 导出路由模块

这个模块提供了一个 iCal 订阅源端点，将本地数据库中的待办任务导出为 iCal 格式。
滴答清单可以通过订阅这个 URL 来导入 Zectrix 上的任务。

【API 端点】
  GET /feed/{token}.ics — 获取 iCal 格式的待办列表

【iCal 格式说明】
  iCal（iCalendar）是一个标准的日历数据交换格式，扩展名为 .ics。
  它用文本格式描述日历事件，例如：
    BEGIN:VCALENDAR     ← 日历开始
    BEGIN:VEVENT        ← 事件开始
    SUMMARY:买牛奶      ← 事件标题
    DTSTART;VALUE=DATE:20260509  ← 开始日期
    END:VEVENT          ← 事件结束
    END:VCALENDAR       ← 日历结束

【Python / FastAPI 知识点】
  - 路径参数 {token} 在函数签名中自动接收。
    例如 GET /feed/abc123.ics，token 的值就是 "abc123"。
"""

# 导入 FastAPI 核心类
from fastapi import APIRouter, Response
# 导入另一种响应类（用于自定义 HTTP 响应）
from fastapi.responses import Response as FastResponse

# 导入数据库操作函数
from app.database import get_db, get_config

# 创建路由器（注意：没有 prefix，因为 feed 路径是特殊的）
router = APIRouter(tags=["feed"])


@router.get("/feed/{token}.ics")
async def serve_ical_feed(token: str):
    """
    生成并返回 iCal 格式的待办列表。

    GET /feed/{token}.ics

    token 是一个随机生成的安全令牌，防止未授权访问。
    如果 token 不匹配，返回 403 Forbidden。

    返回 Content-Type: text/calendar，浏览器会识别为日历文件。
    """
    # 验证 token 是否匹配（安全校验）
    feed_token = await get_config("feed_token")
    if not feed_token or token != feed_token:
        return FastResponse(content="Forbidden", status_code=403)

    # 从数据库获取所有待办任务
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM todos ORDER BY due_date ASC, updated_at DESC")
        rows = await cursor.fetchall()
    finally:
        await db.close()

    # ─── 构建 iCal 文本内容 ───
    # iCal 格式有严格的换行要求（\r\n）和转义规则
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TodoSync//feed 1.0//EN",   # 产品标识
        "CALSCALE:GREGORIAN",                  # 日历系统：公历
        "METHOD:PUBLISH",                      # 方法：发布
        "X-WR-CALNAME:TodoSync",              # 日历名称
    ]

    for row in rows:
        uid = row["uid"]
        title = _escape(row["title"] or "")        # 转义特殊字符
        desc = _escape(row["description"] or "")
        due = row["due_date"]
        completed = bool(row["completed"])
        priority = row["priority"] or 0

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}@todosync")         # 唯一标识符
        lines.append(f"SUMMARY:{title}")              # 标题

        if due:
            # DTSTART: 截止日期，VALUE=DATE 表示全天事件（只有日期没有时间）
            # 日期格式为 YYYYMMDD（去掉连字符）
            lines.append(f"DTSTART;VALUE=DATE:{due.replace('-', '')}")
            import datetime
            try:
                dt = datetime.date.fromisoformat(due)
                # DTEND 设为截止日期的下一天（iCal 全天事件约定：结束日期不包含当天）
                next_day = dt + datetime.timedelta(days=1)
                lines.append(f"DTEND;VALUE=DATE:{next_day.isoformat().replace('-', '')}")
            except Exception:
                pass

        if desc:
            lines.append(f"DESCRIPTION:{desc}")

        if priority:
            lines.append(f"PRIORITY:{priority}")

        if completed:
            lines.append("STATUS:CONFIRMED")    # 已完成状态
            if row.get("completed_at"):
                lines.append(f"COMPLETED:{_format_dt(row['completed_at'])}")

        # DTSTAMP 是这个 iCal 记录的最后修改时间（必填字段）
        lines.append(f"DTSTAMP:{_now_stamp()}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    # iCal 标准要求使用 \r\n（CRLF）换行
    content = "\r\n".join(lines)
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",  # MIME 类型
        headers={"Content-Disposition": f"attachment; filename=todosync.ics"},  # 浏览器下载时的文件名
    )


def _escape(text: str) -> str:
    """
    转义 iCal 格式的特殊字符。

    iCal 格式中，逗号、分号、反斜杠、换行符需要用反斜杠转义。
    例如 "买牛奶,面包" → "买牛奶\\,面包"
    """
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _now_stamp() -> str:
    """
    获取当前 UTC 时间的 iCal 时间戳。

    格式：YYYYMMDDTHHMMSSZ
    例如：20260509T054000Z
    T 是日期和时间的分隔符，Z 表示 UTC 时区。
    """
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _format_dt(dt_str: str) -> str:
    """
    将 ISO 格式的时间字符串转换为 iCal 时间戳。

    例如 "2026-05-09 10:30:00" → "20260509T103000Z"
    """
    if not dt_str:
        return _now_stamp()
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%Y%m%dT%H%M%SZ")
    except Exception:
        return _now_stamp()
