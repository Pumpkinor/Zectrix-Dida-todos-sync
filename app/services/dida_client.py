import logging
import time

import httpx

logger = logging.getLogger(__name__)

OAUTH_BASE = "https://dida365.com/oauth"
API_BASE = "https://api.dida365.com/open/v1"


class DidaClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    def get_auth_url(self, redirect_uri: str) -> str:
        return (
            f"{OAUTH_BASE}/authorize"
            f"?client_id={self.client_id}"
            f"&response_type=code"
            f"&scope=tasks:read tasks:write"
            f"&redirect_uri={redirect_uri}"
        )

    async def exchange_code(self, code: str, redirect_uri: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OAUTH_BASE}/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def refresh_token(self, refresh_token: str, redirect_uri: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OAUTH_BASE}/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "redirect_uri": redirect_uri,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def _request(self, method: str, path: str, access_token: str, json_data=None) -> dict:
        url = f"{API_BASE}{path}"
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, url, headers=headers, json=json_data)
            resp.raise_for_status()
            if resp.status_code == 204 or not resp.text:
                return {}
            return resp.json()

    async def list_projects(self, access_token: str) -> list[dict]:
        return await self._request("GET", "/project", access_token)

    async def get_project_tasks(self, access_token: str, project_id: str) -> list[dict]:
        data = await self._request("GET", f"/project/{project_id}/data", access_token)
        return data.get("taskList", []) if isinstance(data, dict) else []

    async def get_task(self, access_token: str, project_id: str, task_id: str) -> dict:
        return await self._request("GET", f"/project/{project_id}/task/{task_id}", access_token)

    async def create_task(self, access_token: str, task: dict) -> dict:
        return await self._request("POST", "/task", access_token, json_data=task)

    async def complete_task(self, access_token: str, project_id: str, task_id: str) -> dict:
        return await self._request("POST", f"/project/{project_id}/task/{task_id}/complete", access_token)

    async def delete_task(self, access_token: str, project_id: str, task_id: str) -> dict:
        return await self._request("DELETE", f"/project/{project_id}/task/{task_id}", access_token)


async def get_dida_client() -> DidaClient | None:
    from app.database import get_config
    client_id = await get_config("dida_client_id")
    client_secret = await get_config("dida_client_secret")
    if not client_id or not client_secret:
        return None
    return DidaClient(client_id, client_secret)


async def get_valid_access_token() -> str | None:
    """Get a valid access token, refreshing if needed."""
    from app.database import get_config, set_config

    access_token = await get_config("dida_access_token")
    refresh_token = await get_config("dida_refresh_token")
    expires_at = await get_config("dida_token_expires_at")

    if not access_token:
        return None

    # Check if token is expired (with 5 min buffer)
    if expires_at:
        try:
            if time.time() * 1000 > int(expires_at) - 300000:
                if not refresh_token:
                    logger.warning("Dida token expired and no refresh token")
                    return None
                client = await get_dida_client()
                if not client:
                    return None
                redirect_uri = await get_config("dida_redirect_uri") or "http://localhost:8000/api/dida/callback"
                logger.info("Refreshing Dida365 access token...")
                result = await client.refresh_token(refresh_token, redirect_uri)
                new_access = result.get("access_token", "")
                new_refresh = result.get("refresh_token", refresh_token)
                expires_in = result.get("expires_in", 0)
                new_expires_at = str(int(time.time() * 1000) + expires_in * 1000) if expires_in else ""

                await set_config("dida_access_token", new_access)
                await set_config("dida_refresh_token", new_refresh)
                if new_expires_at:
                    await set_config("dida_token_expires_at", new_expires_at)

                logger.info("Dida365 token refreshed successfully")
                return new_access
        except (ValueError, TypeError):
            pass

    return access_token
