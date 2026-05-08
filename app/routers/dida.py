from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from app.database import get_config, set_config
from app.services.dida_client import get_dida_client

router = APIRouter(prefix="/api/dida", tags=["dida"])


@router.get("/auth")
async def dida_auth(request: Request):
    """Start Dida365 OAuth flow."""
    client = await get_dida_client()
    if not client:
        return HTMLResponse("Dida365 client not configured. Set dida_client_id and dida_client_secret in config.", status_code=400)

    redirect_uri = str(request.base_url.replace(path="/api/dida/callback"))
    await set_config("dida_redirect_uri", redirect_uri)

    auth_url = client.get_auth_url(redirect_uri)
    return RedirectResponse(auth_url)


@router.get("/callback")
async def dida_callback(code: str = None, error: str = None):
    """Handle Dida365 OAuth callback."""
    if error:
        return HTMLResponse(f"<h2>Authorization failed</h2><p>{error}</p>", status_code=400)

    if not code:
        return HTMLResponse("<h2>Authorization failed</h2><p>No code received</p>", status_code=400)

    client = await get_dida_client()
    if not client:
        return HTMLResponse("Dida365 client not configured.", status_code=400)

    redirect_uri = await get_config("dida_redirect_uri")

    try:
        result = await client.exchange_code(code, redirect_uri)
        access_token = result.get("access_token", "")
        refresh_token = result.get("refresh_token", "")
        expires_in = result.get("expires_in", 0)

        import time
        expires_at = str(int(time.time() * 1000) + expires_in * 1000) if expires_in else ""

        await set_config("dida_access_token", access_token)
        await set_config("dida_refresh_token", refresh_token)
        if expires_at:
            await set_config("dida_token_expires_at", expires_at)

        # Fetch and store default project
        if access_token:
            projects = await client.list_projects(access_token)
            if projects:
                await set_config("dida_project_id", projects[0]["id"])
                project_name = projects[0].get("name", "Unknown")
            else:
                project_name = "None"

        return HTMLResponse("""
            <h2>Authorization successful!</h2>
            <p>Dida365 is now connected. You can close this page.</p>
            <script>setTimeout(() => window.close(), 3000);</script>
        """)
    except Exception as e:
        return HTMLResponse(f"<h2>Token exchange failed</h2><p>{e}</p>", status_code=500)


@router.get("/status")
async def dida_status():
    """Check Dida365 API connection status."""
    access_token = await get_config("dida_access_token")
    client_id = await get_config("dida_client_id")
    project_id = await get_config("dida_project_id")

    if not client_id:
        return {"connected": False, "reason": "client_id not configured"}

    if not access_token:
        return {"connected": False, "reason": "not authenticated"}

    # Try to verify token by listing projects
    try:
        client = await get_dida_client()
        if client:
            from app.services.dida_client import get_valid_access_token
            token = await get_valid_access_token()
            if token:
                projects = await client.list_projects(token)
                return {
                    "connected": True,
                    "projects": [{"id": p["id"], "name": p["name"]} for p in projects],
                    "active_project": project_id,
                }
    except Exception as e:
        return {"connected": False, "reason": f"API error: {e}"}

    return {"connected": False, "reason": "unknown"}
