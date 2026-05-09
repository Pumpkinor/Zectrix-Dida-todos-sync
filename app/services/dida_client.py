import logging
import json
from datetime import datetime, time
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

MCP_URL = "https://mcp.dida365.com"
DEFAULT_TIMEZONE = "Asia/Shanghai"


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
                    "Authorization": f"Bearer {self.token}",
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

    def _build_task_payload(self, title: str = None, project_id: str = None,
                            content: str = "", due_date: str = None,
                            due_time: str = None, priority: int = 0,
                            reminders: str = "", repeat_flag: str = "") -> dict:
        task = {}
        if title is not None:
            task["title"] = title
        if project_id:
            task["projectId"] = project_id
        if content:
            task["content"] = content
        due_dt = _format_dida_datetime(due_date, due_time)
        if due_dt:
            task["startDate"] = due_dt
            task["dueDate"] = due_dt
            task["timeZone"] = DEFAULT_TIMEZONE
            task["isAllDay"] = due_time is None
        dida_priority = _local_priority_to_dida(priority)
        if dida_priority:
            task["priority"] = dida_priority
        if reminders:
            try:
                task["reminders"] = json.loads(reminders)
            except (json.JSONDecodeError, ValueError):
                pass
        if repeat_flag and repeat_flag != "none":
            task["repeatFlag"] = repeat_flag
        return task

    async def create_task(self, title: str, project_id: str = None,
                          content: str = "", due_date: str = None,
                          due_time: str = None, priority: int = 0, reminders: str = "",
                          repeat_flag: str = "") -> str:
        task = self._build_task_payload(
            title=title,
            project_id=project_id,
            content=content,
            due_date=due_date,
            due_time=due_time,
            priority=priority,
            reminders=reminders,
            repeat_flag=repeat_flag,
        )
        return await self._call_tool("create_task", {"task": task})

    async def update_task(self, task_id: str, title: str = None, project_id: str = None,
                          content: str = "", due_date: str = None,
                          due_time: str = None, priority: int = 0,
                          reminders: str = "", repeat_flag: str = "") -> str:
        task = self._build_task_payload(
            title=title,
            project_id=project_id,
            content=content,
            due_date=due_date,
            due_time=due_time,
            priority=priority,
            reminders=reminders,
            repeat_flag=repeat_flag,
        )
        logger.info(f"MCP update_task: task={task_id}, keys={list(task.keys())}")
        return await self._call_tool("update_task", {"task_id": task_id, "task": task})

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


def _local_priority_to_dida(priority: int) -> int:
    return {0: 0, 1: 3, 2: 5, 3: 5, 5: 5}.get(priority or 0, 0)


def _format_dida_datetime(due_date: str | None, due_time: str | None) -> str | None:
    if not due_date:
        return None
    hour = 0
    minute = 0
    if due_time:
        parts = due_time.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    dt = datetime.combine(
        datetime.strptime(due_date, "%Y-%m-%d").date(),
        time(hour=hour, minute=minute),
        tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")
