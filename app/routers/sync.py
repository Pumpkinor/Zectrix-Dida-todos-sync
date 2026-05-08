from fastapi import APIRouter
from app.services.sync_engine import run_sync, run_reverse_sync, _get_forwarder

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("")
async def trigger_sync():
    await run_sync()
    return {"ok": True, "message": "Sync completed"}


@router.get("/zectrix-test")
async def test_zectrix_fetch():
    """Test fetching todos from Zectrix to verify connectivity."""
    forwarder = await _get_forwarder()
    if not forwarder:
        return {"ok": False, "error": "Zectrix not configured (missing api_key or device_id)"}
    try:
        todos = await forwarder.fetch_remote_todos()
        return {"ok": True, "count": len(todos), "todos": todos}
    except Exception as e:
        return {"ok": False, "error": str(e)}
