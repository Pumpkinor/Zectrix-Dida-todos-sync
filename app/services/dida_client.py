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

        logger.debug(f"MCP request: method={method}, params_keys={list(params.keys()) if params else []}")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                MCP_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.token[:8]}...",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if "error" in data:
            err_msg = data['error'].get('message', str(data['error']))
            logger.error(f"MCP error: method={method}, error={err_msg}")
            raise Exception(f"MCP error: {err_msg}")

        result = data.get("result", {})
        logger.debug(f"MCP response: method={method}, result_keys={list(result.keys()) if isinstance(result, dict) else type(result).__name__}")
        return result

    async def _call_tool(self, name: str, arguments: dict) -> str:
        logger.info(f"MCP tool call: {name}, args={json.dumps(arguments, ensure_ascii=False)[:200]}")
        result = await self._call("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        # MCP returns multiple content items - concatenate all text items
        texts = []
        for c in content:
            if c.get("type") == "text":
                texts.append(c["text"])
        combined = "\n".join(texts)
        logger.info(f"MCP tool result: {name}, content_items={len(content)}, total_text_len={len(combined)}")
        return combined

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
        """List all projects from Dida365 via MCP."""
        text = await self._call_tool("list_projects", {})
        projects = self._parse_ndjson(text) if text else []
        logger.info(f"MCP list_projects: got {len(projects)} projects")
        for p in projects:
            logger.info(f"  Project: id={p.get('id')}, name={p.get('name')}")
        return projects

    async def get_undone_tasks(self, project_id: str) -> list[dict]:
        text = await self._call_tool("get_project_with_undone_tasks", {"project_id": project_id})
        if not text:
            return []
        data = json.loads(text) if text.strip().startswith('{') else {}
        tasks = data.get("tasks", [])
        logger.info(f"MCP get_undone_tasks: project={project_id}, count={len(tasks)}")
        return tasks

    async def get_completed_tasks(self, project_ids: list[str], start_date: str, end_date: str) -> list[dict]:
        text = await self._call_tool("list_completed_tasks_by_date", {
            "search": {},
            "project_ids": project_ids,
            "start_date": start_date,
            "end_date": end_date,
        })
        tasks = self._parse_ndjson(text) if text else []
        logger.info(f"MCP get_completed_tasks: projects={project_ids}, range={start_date}~{end_date}, count={len(tasks)}")
        return tasks

    async def complete_task(self, project_id: str, task_id: str) -> str:
        logger.info(f"MCP complete_task: project={project_id}, task={task_id}")
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
