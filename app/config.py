import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "todos.db")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

os.makedirs(DATA_DIR, exist_ok=True)

DEFAULTS = {
    "ical_url": "",
    "zectrix_api_key": "",
    "zectrix_base_url": "https://cloud.zectrix.com",
    "zectrix_device_id": "",
    "sync_interval_minutes": "5",
    "bidirectional_enabled": "false",
    "feed_token": "",
    "email_smtp_host": "",
    "email_smtp_port": "465",
    "email_smtp_user": "",
    "email_smtp_password": "",
    "email_from": "",
    "email_to_dida": "",
    "dida_mcp_token": "",
    "dida_project_id": "",
    "dida_sync_mode": "mcp",
    "reverse_sync_mode": "mcp",
}
