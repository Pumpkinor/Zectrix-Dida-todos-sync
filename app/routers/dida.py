from fastapi import APIRouter
from app.database import get_config
from app.services.dida_client import get_dida_mcp_client

router = APIRouter(prefix="/api/dida", tags=["dida"])


@router.get("/projects")
async def dida_projects():
    """List Dida365 projects via MCP."""
    client = await get_dida_mcp_client()
    if not client:
        return {"error": "MCP token not configured"}

    try:
        await client.initialize()
        projects = await client.list_projects()
        return {"projects": [{"id": p.get("id", ""), "name": p.get("name", "")} for p in projects]}
    except Exception as e:
        return {"error": str(e)}
