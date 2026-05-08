import secrets

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Union
from app.database import get_all_config, set_config, get_config as _get_config

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdate(BaseModel):
    ical_url: Optional[str] = None
    zectrix_api_key: Optional[str] = None
    zectrix_base_url: Optional[str] = None
    zectrix_device_id: Optional[str] = None
    sync_interval_minutes: Optional[Union[str, int]] = None
    bidirectional_enabled: Optional[str] = None
    feed_token: Optional[str] = None
    email_smtp_host: Optional[str] = None
    email_smtp_port: Optional[Union[str, int]] = None
    email_smtp_user: Optional[str] = None
    email_smtp_password: Optional[str] = None
    email_from: Optional[str] = None
    email_to_dida: Optional[str] = None
    dida_mcp_token: Optional[str] = None
    dida_project_id: Optional[str] = None
    dida_sync_mode: Optional[str] = None
    reverse_sync_mode: Optional[str] = None


@router.get("")
async def get_configuration():
    return await get_all_config()


@router.post("/generate-feed-token")
async def generate_feed_token():
    token = secrets.token_urlsafe(16)
    await set_config("feed_token", token)
    return {"ok": True, "feed_token": token}


@router.put("")
async def update_configuration(body: ConfigUpdate):
    updates = body.model_dump(exclude_none=True)
    for key, value in updates.items():
        await set_config(key, str(value))

    # If sync interval changed, reschedule
    if "sync_interval_minutes" in updates:
        from app.scheduler import reschedule_sync
        await reschedule_sync()

    return {"ok": True, "updated": list(updates.keys())}
