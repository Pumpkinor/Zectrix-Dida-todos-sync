import json
import logging
from datetime import datetime, date
from typing import Optional

import httpx
from icalendar import Calendar

from app.models import Todo

logger = logging.getLogger(__name__)

PRIORITY_MAP = {1: 2, 2: 1, 3: 1, 4: 0, 5: 0}  # iCal 1(highest)-5(lowest) → 0/1/2


def _unwrap(val):
    """Unwrap icalendar value types to native Python types."""
    if val is None:
        return None
    # vDDDTypes, vText, vInt, etc. — use .dt for date/time types
    if hasattr(val, 'dt'):
        return val.dt
    if isinstance(val, (datetime, date)):
        return val
    if isinstance(val, str):
        return val
    return str(val)


def _parse_datetime(dt) -> Optional[str]:
    dt = _unwrap(dt)
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(dt, date):
        return dt.strftime("%Y-%m-%d")
    return str(dt)


def _parse_date_only(dt) -> Optional[str]:
    dt = _unwrap(dt)
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    if isinstance(dt, date):
        return dt.strftime("%Y-%m-%d")
    return None


def _parse_time_only(dt) -> Optional[str]:
    dt = _unwrap(dt)
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.strftime("%H:%M")
    return None


def _parse_priority(ical_priority) -> int:
    if ical_priority is None:
        return 0
    val = int(ical_priority)
    return PRIORITY_MAP.get(val, 0)


def _component_to_raw_text(component) -> str:
    """Convert a component back to raw iCal text for storage."""
    return component.to_ical().decode("utf-8", errors="replace")


def _parse_vevent(component) -> Todo:
    """Parse a VEVENT component (TickTick/Dida365 format) into a Todo."""
    uid = str(component.get("UID", ""))
    summary = str(component.get("SUMMARY", ""))
    description = str(component.get("DESCRIPTION", "") or "")

    dtstart = component.get("DTSTART")
    dtend = component.get("DTEND")
    due_date = _parse_date_only(dtstart)
    due_time = _parse_time_only(dtstart)

    # All-day events: dtstart is date only, no time
    if due_date and not due_time:
        due_time = None

    # Check completion status
    completed = False
    status_val = component.get("STATUS")
    if status_val and str(status_val).upper() in ("COMPLETED", "CANCELLED"):
        completed = True
    completed_val = component.get("COMPLETED")
    if completed_val is not None:
        completed = True

    raw = _component_to_raw_text(component)

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
        last_modified=_parse_datetime(component.get("LAST-MODIFIED") or component.get("DTSTAMP")),
    )


def _parse_vtodo(component) -> Todo:
    """Parse a VTODO component into a Todo."""
    uid = str(component.get("UID", ""))
    due = component.get("DUE")
    due_date = _parse_date_only(due)
    due_time = _parse_time_only(due)

    completed_val = component.get("COMPLETED")
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


async def fetch_ical(url: str) -> list[Todo]:
    """Fetch iCal feed and parse VTODO/VEVENT components into Todo objects."""
    fetch_url = url.replace("webcal://", "https://")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(fetch_url)
        response.raise_for_status()

    cal = Calendar.from_ical(response.text)
    todos = []

    for component in cal.walk():
        if component.name == "VTODO":
            todo = _parse_vtodo(component)
        elif component.name == "VEVENT":
            todo = _parse_vevent(component)
        else:
            continue

        if not todo.uid:
            continue
        todos.append(todo)

    logger.info(f"Fetched {len(todos)} todos from iCal feed")
    return todos


def _dida_priority_to_local(p: int) -> int:
    """Convert Dida365 priority (0=None, 1=Low, 3=Medium, 5=High) to local (0/1/2)."""
    return {0: 0, 1: 1, 3: 1, 5: 2}.get(p, 0)


def _parse_dida_date(d) -> str | None:
    """Parse Dida365 date string to YYYY-MM-DD."""
    if not d:
        return None
    s = str(d)
    return s[:10] if len(s) >= 10 else None


def _parse_dida_time(d) -> str | None:
    """Parse Dida365 datetime string to HH:MM."""
    if not d:
        return None
    s = str(d)
    if "T" in s and len(s) > 16:
        return s[11:16]
    return None


async def fetch_dida_tasks() -> list[Todo]:
    """Fetch tasks from Dida365 via MCP API. Returns list of Todo with accurate status."""
    from app.services.dida_client import get_dida_mcp_client
    from app.database import get_config
    from datetime import date, timedelta

    client = await get_dida_mcp_client()
    if not client:
        raise Exception("Dida365 MCP not configured (missing dida_mcp_token)")

    await client.initialize()

    project_id = await get_config("dida_project_id")
    if not project_id:
        projects = await client.list_projects()
        if projects:
            project_id = projects[0]["id"]
            logger.info(f"Using first Dida365 project: {projects[0].get('name', '')} ({project_id})")
        else:
            raise Exception("No Dida365 projects found")

    # Fetch undone tasks
    undone_tasks = await client.get_undone_tasks(project_id)
    logger.info(f"Dida365 MCP: {len(undone_tasks)} undone tasks from project {project_id}")

    # Fetch recently completed tasks (last 30 days)
    today = date.today()
    start = (today - timedelta(days=30)).isoformat()
    end = today.isoformat()
    completed_tasks = await client.get_completed_tasks([project_id], start, end)
    logger.info(f"Dida365 MCP: {len(completed_tasks)} completed tasks from {start} to {end}")

    raw_tasks = undone_tasks + completed_tasks
    logger.info(f"Dida365 MCP: {len(raw_tasks)} total tasks")

    todos = []
    for t in raw_tasks:
        status = t.get("status", 0)
        is_completed = status == 2
        due = t.get("dueDate")

        todo = Todo(
            uid=f"dida-{t['id']}",
            title=t.get("title", ""),
            description=t.get("content", "") or t.get("desc", "") or "",
            due_date=_parse_dida_date(due),
            due_time=_parse_dida_time(due),
            priority=_dida_priority_to_local(t.get("priority", 0)),
            completed=is_completed,
            completed_at=t.get("completedTime"),
            ical_raw="",
            last_modified=t.get("modifiedTime"),
        )
        # Store original Dida365 IDs for reverse operations
        todo._dida_task_id = t["id"]
        todo._dida_project_id = t.get("projectId", project_id)
        todos.append(todo)

    return todos
