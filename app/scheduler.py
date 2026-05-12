"""
定时任务调度模块

这个文件使用 APScheduler 库实现定时自动同步。
APScheduler（Advanced Python Scheduler）是一个 Python 定时任务库，
支持间隔触发、Cron 触发等多种调度方式。

【核心概念】
  - AsyncIOScheduler: APScheduler 的异步版本，适合配合 FastAPI 使用
  - IntervalTrigger: 按固定时间间隔触发（本项目中每隔 N 分钟触发一次同步）
  - Job: 一个被调度的任务。每个 Job 有唯一 ID，可以被重新调度或取消
"""

import logging
# 导入 APScheduler 的异步调度器
from apscheduler.schedulers.asyncio import AsyncIOScheduler
# 导入间隔触发器
from apscheduler.triggers.interval import IntervalTrigger

# 导入同步引擎的核心函数（真正执行同步的地方）
from app.services.sync_engine import run_sync

# 创建模块级日志记录器。__name__ 的值是 "app.scheduler"
logger = logging.getLogger(__name__)

# 创建调度器实例（全局单例）
scheduler = AsyncIOScheduler()
# 定义同步任务的唯一 ID，用于后续查找和管理这个任务
_sync_job_id = "todo_sync"


async def start_scheduler():
    """
    启动定时调度器。

    在 FastAPI 应用的 lifespan 启动阶段被调用（main.py）。
    从数据库读取用户配置的同步间隔，然后注册定时任务。
    """
    from app.database import get_config

    # 读取用户配置的同步间隔（分钟），默认 5 分钟
    minutes = int(await get_config("sync_interval_minutes") or "5")
    # 添加定时任务
    # _run_with_log 是实际执行的函数
    # IntervalTrigger(minutes=minutes) 表示每隔 minutes 分钟触发一次
    # replace_existing=True 表示如果已有同 ID 的任务就替换
    scheduler.add_job(
        _run_with_log,
        trigger=IntervalTrigger(minutes=minutes),
        id=_sync_job_id,
        replace_existing=True,
    )
    # 启动调度器（开始计时）
    scheduler.start()
    logger.info(f"Scheduler started, sync every {minutes} minutes")


async def reschedule_sync():
    """
    在用户修改同步间隔后重新调度任务。

    当用户在 Web 管理页面修改了"同步频率"配置并保存后，
    config 路由会调用这个函数来更新定时任务的触发间隔。
    """
    from app.database import get_config

    minutes = int(await get_config("sync_interval_minutes") or "5")
    # reschedule_job: 保留任务但替换触发器（即改变执行间隔）
    scheduler.reschedule_job(
        _sync_job_id,
        trigger=IntervalTrigger(minutes=minutes),
    )
    logger.info(f"Sync interval updated to {minutes} minutes")


async def _run_with_log():
    """
    定时任务的包装函数。

    对 run_sync() 做一层异常捕获，防止同步失败导致调度器崩溃。
    函数名以下划线开头是 Python 的惯例，表示"这是内部函数，不要在外部直接调用"。
    """
    try:
        await run_sync()
    except Exception as e:
        # 记录错误日志但不抛出异常，让调度器继续运行
        logger.error(f"Sync job failed: {e}")
