from fastapi import APIRouter, Response
from fastapi.responses import Response as FastResponse

from app.database import get_db, get_config

router = APIRouter(tags=["feed"])


@router.get("/feed/{token}.ics")
async def serve_ical_feed(token: str):
    feed_token = await get_config("feed_token")
    if not feed_token or token != feed_token:
        return FastResponse(content="Forbidden", status_code=403)

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM todos ORDER BY due_date ASC, updated_at DESC")
        rows = await cursor.fetchall()
    finally:
        await db.close()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TodoSync//feed 1.0//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:TodoSync",
    ]

    for row in rows:
        uid = row["uid"]
        title = _escape(row["title"] or "")
        desc = _escape(row["description"] or "")
        due = row["due_date"]
        completed = bool(row["completed"])
        priority = row["priority"] or 0

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}@todosync")
        lines.append(f"SUMMARY:{title}")

        if due:
            lines.append(f"DTSTART;VALUE=DATE:{due.replace('-', '')}")
            import datetime
            try:
                dt = datetime.date.fromisoformat(due)
                next_day = dt + datetime.timedelta(days=1)
                lines.append(f"DTEND;VALUE=DATE:{next_day.isoformat().replace('-', '')}")
            except Exception:
                pass

        if desc:
            lines.append(f"DESCRIPTION:{desc}")

        if priority:
            lines.append(f"PRIORITY:{priority}")

        if completed:
            lines.append("STATUS:CONFIRMED")
            if row.get("completed_at"):
                lines.append(f"COMPLETED:{_format_dt(row['completed_at'])}")

        lines.append(f"DTSTAMP:{_now_stamp()}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    content = "\r\n".join(lines)
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=todosync.ics"},
    )


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _now_stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _format_dt(dt_str: str) -> str:
    if not dt_str:
        return _now_stamp()
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%Y%m%dT%H%M%SZ")
    except Exception:
        return _now_stamp()
