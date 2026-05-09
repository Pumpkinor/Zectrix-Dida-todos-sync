import logging
from typing import Optional

import httpx

from app.models import Todo
from app.services.forwarders.base import BaseForwarder

logger = logging.getLogger(__name__)

# Map Dida365 RRULE FREQ to Zectrix repeatType
_REPEAT_MAP = {
    "DAILY": "daily",
    "WEEKLY": "weekly",
    "MONTHLY": "monthly",
    "YEARLY": "yearly",
}


def _dida_repeat_to_zectrix(repeat_flag: str) -> str:
    """Extract repeatType from Dida365 repeatFlag (RRULE/ERULE string)."""
    if not repeat_flag:
        return "none"
    upper = repeat_flag.upper()
    for key, val in _REPEAT_MAP.items():
        if f"FREQ={key}" in upper:
            return val
    return "none"


class ZectrixForwarder(BaseForwarder):
    def __init__(self, api_key: str, device_id: str, base_url: str = "https://cloud.zectrix.com"):
        self.api_key = api_key
        self.device_id = device_id
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, json_data: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method, url, headers=self._headers(), json=json_data
            )
            response.raise_for_status()
            return response.json()

    async def create_todo(self, todo: Todo) -> str:
        repeat_type = _dida_repeat_to_zectrix(todo.repeat_flag)
        body = {
            "title": todo.title,
            "description": todo.description or "",
            "priority": todo.priority,
            "deviceId": self.device_id,
            "repeatType": repeat_type,
        }
        if todo.due_date:
            body["dueDate"] = todo.due_date
        if todo.due_time:
            body["dueTime"] = todo.due_time

        result = await self._request("POST", "/open/v1/todos", body)
        remote_id = str(result.get("data", {}).get("id", ""))
        logger.info(f"Created Zectrix todo: {remote_id} ({todo.title})")
        return remote_id

    async def update_todo(self, remote_id: str, todo: Todo):
        repeat_type = _dida_repeat_to_zectrix(todo.repeat_flag)
        body = {
            "title": todo.title,
            "description": todo.description or "",
            "priority": todo.priority,
            "repeatType": repeat_type,
        }
        if todo.due_date:
            body["dueDate"] = todo.due_date
        if todo.due_time:
            body["dueTime"] = todo.due_time

        await self._request("PUT", f"/open/v1/todos/{remote_id}", body)
        logger.info(f"Updated Zectrix todo: {remote_id}")

    async def complete_todo(self, remote_id: str):
        await self._request("PUT", f"/open/v1/todos/{remote_id}/complete")
        logger.info(f"Completed Zectrix todo: {remote_id}")

    async def delete_todo(self, remote_id: str):
        await self._request("DELETE", f"/open/v1/todos/{remote_id}")
        logger.info(f"Deleted Zectrix todo: {remote_id}")

    async def fetch_remote_todos(self) -> list[dict]:
        result = await self._request("GET", f"/open/v1/todos?deviceId={self.device_id}")
        todos = result.get("data", [])
        logger.info(f"Fetched {len(todos)} todos from Zectrix")
        return todos
