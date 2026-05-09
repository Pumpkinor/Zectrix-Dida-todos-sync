from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Todo:
    uid: str
    title: str
    description: str = ""
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    priority: int = 0
    completed: bool = False
    completed_at: Optional[str] = None
    ical_raw: str = ""
    last_modified: Optional[str] = None
    synced: bool = False
    synced_at: Optional[str] = None
    remote_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    reminders: str = ""          # JSON array of trigger strings (Dida365 format)
    repeat_flag: str = ""        # RRULE/ERULE string (Dida365) or repeatType (Zectrix)


@dataclass
class SyncLog:
    id: Optional[int] = None
    action: str = ""
    status: str = ""
    detail: str = ""
    count: int = 0
    created_at: Optional[str] = None
