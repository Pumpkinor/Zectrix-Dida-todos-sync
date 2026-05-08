# Todo List Sync Service - Design Document

## Overview

从滴答清单 (Dida365) iCal 订阅链接获取待办数据，保存到本地数据库，并同步转发到第三方平台（首个目标：Zectrix Cloud）。

## Architecture

```
滴答清单 iCal Feed
       │
       ▼
  ┌──────────┐    ┌──────────────┐    ┌────────────────┐
  │ Sync      │───▶│ SQLite       │───▶│ Forwarder      │───▶ Zectrix API
  │ Service   │    │ (本地存储)    │    │ (可插拔)        │
  └──────────┘    └──────────────┘    └────────────────┘
       ▲                                      ▲
       │                                      │
  ┌──────────────────────────────────────────────────┐
  │              FastAPI Web Application              │
  │  - 查看/搜索待办                                   │
  │  - 手动触发同步                                    │
  │  - 配置管理（iCal URL、API Key、DeviceId、频率）    │
  │  - 同步日志查看                                    │
  └──────────────────────────────────────────────────┘
```

## Tech Stack

- **Backend**: Python 3.11+ / FastAPI
- **Database**: SQLite (aiosqlite)
- **Scheduler**: APScheduler
- **iCal Parsing**: icalendar library
- **Frontend**: Vue 3 + TailwindCSS (local assets, no CDN)
- **HTTP Client**: httpx (async)

## Data Model

### todos

| Field | Type | Description |
|-------|------|-------------|
| uid | TEXT PK | iCal VTODO UID |
| title | TEXT | Title |
| description | TEXT | Description |
| due_date | TEXT | Due date (yyyy-MM-dd) |
| due_time | TEXT | Due time (HH:mm) |
| priority | INTEGER | 0=normal, 1=important, 2=urgent |
| completed | BOOLEAN | Completion status |
| completed_at | TEXT | Completion timestamp |
| ical_raw | TEXT | Raw iCal data (JSON) |
| last_modified | TEXT | LAST-MODIFIED from iCal |
| synced | BOOLEAN | Whether synced to target |
| synced_at | TEXT | Last sync timestamp |
| remote_id | TEXT | Target platform todo ID |
| created_at | TEXT | Local creation time |
| updated_at | TEXT | Local update time |

### sync_logs

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER PK | Auto increment |
| action | TEXT | fetch / sync / error |
| status | TEXT | success / failed |
| detail | TEXT | Description |
| count | INTEGER | Affected items |
| created_at | TEXT | Timestamp |

### config

| Field | Type | Description |
|-------|------|-------------|
| key | TEXT PK | Config key |
| value | TEXT | Config value |

Key configs: `ical_url`, `zectrix_api_key`, `zectrix_device_id`, `sync_interval_minutes`

## Sync Flow (Incremental)

1. Fetch iCal feed, parse all VTODO components
2. Compare with local DB (by UID):
   - Not in local → INSERT + mark unsynced
   - Exists and LAST-MODIFIED changed → UPDATE + mark unsynced
   - Not in iCal feed → mark as deleted
3. Process unsynced todos via Forwarder:
   - No remote_id → create_todo() → save returned ID
   - Has remote_id + changed → update_todo()
   - Completed → complete_todo()
   - Deleted → delete_todo()
4. Write sync_log entry

## Project Structure

```
todo-list-trans/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── routers/
│   │   ├── todos.py
│   │   ├── config.py
│   │   └── logs.py
│   ├── services/
│   │   ├── fetcher.py
│   │   ├── sync_engine.py
│   │   └── forwarders/
│   │       ├── base.py
│   │       └── zectrix.py
│   └── scheduler.py
├── frontend/
│   ├── index.html
│   └── assets/
│       ├── vue.global.prod.js
│       └── tailwindcss.min.js
├── data/                    # SQLite DB files
├── openapi.yaml
├── requirements.txt
└── CLAUDE.md
```

## Pluggable Forwarder

```python
class BaseForwarder(ABC):
    @abstractmethod
    async def create_todo(self, todo) -> str: ...
    @abstractmethod
    async def update_todo(self, remote_id, todo): ...
    @abstractmethod
    async def complete_todo(self, remote_id): ...
    @abstractmethod
    async def delete_todo(self, remote_id): ...
```

New targets: create a file inheriting BaseForwarder, register in config.

## Frontend (Single-file SPA)

Three tabs:
1. **Todo List** - view/filter todos by status/date, show sync status
2. **Sync Logs** - sync history with success/failure counts
3. **Settings** - iCal URL, API Key, DeviceId, sync interval, manual sync button

All assets local (no CDN).

## Decisions

- **One-way sync first** (Dida → Zectrix), architecture reserves bidirectional support
- **All todos bound to configured default device** on Zectrix
- **Auto polling + manual trigger** for sync
- **Incremental sync** via UID + LAST-MODIFIED comparison
