import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services.sync_engine import run_sync

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_sync_job_id = "todo_sync"


async def start_scheduler():
    from app.database import get_config

    minutes = int(await get_config("sync_interval_minutes") or "5")
    scheduler.add_job(
        _run_with_log,
        trigger=IntervalTrigger(minutes=minutes),
        id=_sync_job_id,
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started, sync every {minutes} minutes")


async def reschedule_sync():
    """Reschedule the sync job after config change."""
    from app.database import get_config

    minutes = int(await get_config("sync_interval_minutes") or "5")
    scheduler.reschedule_job(
        _sync_job_id,
        trigger=IntervalTrigger(minutes=minutes),
    )
    logger.info(f"Sync interval updated to {minutes} minutes")


async def _run_with_log():
    try:
        await run_sync()
    except Exception as e:
        logger.error(f"Sync job failed: {e}")
