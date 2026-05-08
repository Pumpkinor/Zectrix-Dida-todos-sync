import logging
import json

import httpx

logger = logging.getLogger(__name__)

MCP_URL = "https://mcp.dida365.com"


class DidaMCPClient:
    """Dida365 client via MCP (Model Context Protocol) with Bearer Token."""

    def __init__(self, token: str):
        self.token = token
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _call(self, method: str, params: dict = None) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                MCP_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.token}",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if "error" in data:
            raise Exception(f"MCP error: {data['error'].get('message', data['error'])}")
        return data.get("result", {})

    async def _call_tool(self, name: str, arguments: dict) -> str:
        result = await self._call("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        for c in content:
            if c.get("type") == "text":
                return c["text"]
        return ""

    def _parse_ndjson(self, text: str) -> list[dict]:
        """Parse newline-delimited JSON objects from MCP response."""
        objects = []
        current = ""
        depth = 0
        for char in text:
            if char == '{':
                depth += 1
            if depth > 0:
                current += char
            if char == '}':
                depth -= 1
                if depth == 0 and current.strip():
                    try:
                        objects.append(json.loads(current))
                    except json.JSONDecodeError:
                        pass
                    current = ""
        return objects

    async def initialize(self) -> dict:
        return await self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "todo-sync", "version": "1.0"},
        })

    async def list_projects(self) -> list[dict]:
        """List all projects including inbox (discovered from tasks)."""
        text = await self._call_tool("list_projects", {})
        projects = self._parse_ndjson(text) if text else []

        # Discover inbox project ID from a broad task query
        known_ids = {p.get("id") for p in projects if p.get("id")}
        try:
            filter_text = await self._call_tool("filter_tasks", {
                "filter": {"status": [0, 2]}
            })
            if filter_text:
                tasks = self._parse_ndjson(filter_text)
                for t in tasks:
                    pid = t.get("projectId") or t.get("project_id")
                    if pid and pid not in known_ids:
                        projects.append({
                            "id": pid,
                            "name": f"收集箱 ({pid})" if pid.startswith("inbox") else pid,
                        })
                        known_ids.add(pid)
        except Exception as e:
            logger.debug(f"Task-based project discovery failed: {e}")

        return projects

    async def get_undone_tasks(self, project_id: str) -> list[dict]:
        text = await self._call_tool("get_project_with_undone_tasks", {"project_id": project_id})
        if not text:
            return []
        data = json.loads(text) if text.strip().startswith('{') else {}
        return data.get("tasks", [])

    async def get_completed_tasks(self, project_ids: list[str], start_date: str, end_date: str) -> list[dict]:
        text = await self._call_tool("list_completed_tasks_by_date", {
            "search": {},
            "project_ids": project_ids,
            "start_date": start_date,
            "end_date": end_date,
        })
        return self._parse_ndjson(text) if text else []

    async def complete_task(self, project_id: str, task_id: str) -> str:
        return await self._call_tool("complete_task", {
            "project_id": project_id,
            "task_id": task_id,
        })

    async def create_task(self, title: str, project_id: str = None,
                          content: str = "", due_date: str = None,
                          priority: int = 0) -> str:
        args = {"title": title}
        if project_id:
            args["project_id"] = project_id
        if content:
            args["content"] = content
        if due_date:
            args["due_date"] = due_date
        if priority:
            args["priority"] = priority
        return await self._call_tool("create_task", args)

    async def get_task(self, task_id: str) -> dict:
        text = await self._call_tool("get_task_by_id", {"task_id": task_id})
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {}
        return {}


async def get_dida_mcp_client() -> DidaMCPClient | None:
    from app.database import get_config
    token = await get_config("dida_mcp_token")
    if not token:
        return None
    return DidaMCPClient(token)
