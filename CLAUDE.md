# Todo List Sync Service

滴答清单 (Dida365) iCal → 本地 SQLite → Zectrix Cloud 同步服务。

## Quick Start

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

## Architecture

- FastAPI backend with SQLite storage
- APScheduler for periodic iCal sync
- Pluggable forwarder system (Zectrix first)
- Vue 3 + TailwindCSS frontend (local assets)

## Key Files

- `app/services/fetcher.py` - iCal fetch and parse
- `app/services/sync_engine.py` - Incremental sync logic
- `app/services/forwarders/base.py` - Forwarder interface
- `app/services/forwarders/zectrix.py` - Zectrix implementation

## API

- `GET /api/todos` - List todos
- `POST /api/sync` - Manual sync trigger
- `GET /api/logs` - Sync logs
- `GET/PUT /api/config` - Configuration

## Config Keys

- `ical_url` - iCal subscription URL
- `zectrix_api_key` - Zectrix X-API-Key
- `zectrix_device_id` - Default device MAC
- `sync_interval_minutes` - Polling interval (default: 5)
