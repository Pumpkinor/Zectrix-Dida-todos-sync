"""
同步引擎模块 —— 整个项目的核心

这个文件实现了滴答清单（Dida365）和 Zectrix 墨水屏之间的双向同步逻辑。
它是项目中代码量最大、逻辑最复杂的文件。

【整体架构】
  同步分为两个方向：

  1. 正向同步（滴答清单 → Zectrix）— run_sync() 函数
     Step 1: 从滴答清单抓取任务（MCP API 或 iCal）
     Step 2: 与本地数据库比对，新增/更新任务
     Step 3: 检测已被删除的任务（数据库有但 API 不再返回的）
     Step 4: 将未同步的任务转发到 Zectrix

  2. 反向同步（Zectrix → 滴答清单）— run_reverse_sync() 函数
     Phase 1: 从 Zectrix 拉取所有任务
     Phase 2: 与本地数据库中有 remote_id 的记录进行匹配
     Phase 3: 检测 Zectrix 上的变更（更新/删除）
     Phase 4: 导入 Zectrix 新建的任务到本地
     Phase 5: 将 Zectrix 上的字段修改回写到滴答清单
     Phase 6: 将 Zectrix 上完成的任务在滴答清单也标记完成
     Phase 7: 将 Zectrix 上新建的任务在滴答清单也创建

【Python 知识点】
  - 这个文件大量使用 async/await，因为涉及网络请求（httpx）和数据库操作（aiosqlite）
  - try/except/finally 用于异常处理和资源清理
  - f-string（如 f"text {variable}"）用于字符串格式化
  - set（集合）用于高效的成员判断和集合运算（交集、差集）
"""

import logging
from datetime import datetime

# 从项目内部模块导入数据库操作函数
from app.database import get_db, get_config, add_sync_log
# 导入数据抓取函数（两种模式：MCP API 和 iCal）
from app.services.fetcher import fetch_ical, fetch_dida_tasks
# 导入 Zectrix 前向转发器
from app.services.forwarders.zectrix import ZectrixForwarder

# 创建日志记录器。__name__ 的值是 "app.services.sync_engine"
logger = logging.getLogger(__name__)


async def _get_forwarder() -> ZectrixForwarder | None:
    """
    创建并返回一个 ZectrixForwarder 实例。

    从数据库读取 Zectrix 的连接配置（API Key、设备 ID、API 地址），
    如果缺少必要配置则返回 None。

    返回:
      ZectrixForwarder 实例，或 None（未配置时）
    """
    api_key = await get_config("zectrix_api_key")
    device_id = await get_config("zectrix_device_id")
    base_url = await get_config("zectrix_base_url")
    if not api_key or not device_id:
        # 缺少必要配置，无法创建转发器
        logger.warning("Zectrix not configured: missing api_key or device_id")
        return None
    return ZectrixForwarder(api_key, device_id, base_url)


async def _use_dida_api() -> bool:
    """
    检查是否使用滴答清单 MCP API 模式。

    读取配置项 dida_sync_mode，如果值为 "mcp" 则使用 API 模式，
    否则使用 iCal 订阅模式。

    返回:
      True = 使用 MCP API, False = 使用 iCal
    """
    mode = await get_config("dida_sync_mode")
    return mode == "mcp"


async def run_sync():
    """
    执行一次完整的正向同步周期。

    这是定时任务和手动同步都会调用的入口函数。
    完整流程：
      1. 从数据源抓取任务
      2. 与本地数据库比对并更新
      3. 检测被删除的任务
      4. 将变更转发到 Zectrix
      5. 执行反向同步

    【Python 知识点】
      这个函数是一个大型 try 块的示例：
      - try: 尝试执行可能出错的代码
      - except Exception as e: 如果出错，捕获异常并处理
      - finally: 无论是否出错，最后都要执行的清理代码
    """
    logger.info("========== SYNC START ==========")

    # 判断使用哪种数据源
    use_api = await _use_dida_api()
    ical_url = await get_config("ical_url")

    if not use_api and not ical_url:
        logger.warning("No data source configured (neither Dida API nor iCal URL)")
        return  # 提前退出函数

    # ── Step 1: 抓取任务 ──
    logger.info(f"── Step 1: Fetch tasks ({'Dida API' if use_api else 'iCal'}) ──")
    try:
        if use_api:
            # MCP API 模式：通过 JSON-RPC 协议调用滴答清单 API
            remote_todos = await fetch_dida_tasks()
            source_name = "dida_api"
        else:
            # iCal 模式：解析 iCal 订阅数据
            remote_todos = await fetch_ical(ical_url)
            source_name = "ical"
    except Exception as e:
        logger.error(f"[Step 1] FAILED: {e}")
        await add_sync_log("fetch", "failed", str(e), 0)
        return  # 抓取失败，整个同步中止

    # 打印每个抓取到的任务的基本信息（调试用）
    for t in remote_todos:
        logger.info(f"  {source_name} todo: uid={t.uid}, title={t.title}, due={t.due_date}, completed={t.completed}")
    logger.info(f"[Step 1] DONE: fetched {len(remote_todos)} todos from {source_name}")
    await add_sync_log("fetch", "success", f"Fetched {len(remote_todos)} todos from {source_name}", len(remote_todos))

    # 构建快速查找结构
    # set（集合）用于存储所有远程任务的 uid，用于后续判断"哪些本地任务不在远程了"
    remote_uids = {t.uid for t in remote_todos}
    # dict（字典）用于按 uid 快速查找远程任务对象
    remote_map = {t.uid: t for t in remote_todos}

    db = await get_db()  # 获取数据库连接
    try:
        # ── Step 2: 将远程任务写入/更新到本地数据库 ──
        logger.info("── Step 2: Upsert todos into DB ──")
        new_count = 0      # 新增计数
        updated_count = 0  # 更新计数
        skipped_count = 0  # 未变化计数

        for todo in remote_todos:
            # 获取额外的滴答清单 ID 信息（用于跨平台关联）
            # getattr() 安全地获取对象的属性，如果属性不存在则返回默认值
            dida_task_id = getattr(todo, '_dida_task_id', None)
            dida_project_id = getattr(todo, '_dida_project_id', None)

            # 查找本地数据库中是否已有这条记录
            existing = await _find_existing_source_todo(db, todo, dida_task_id)

            if existing is None:
                # ─── 本地不存在 → 新增 ───
                await db.execute(
                    """INSERT INTO todos (uid, title, description, due_date, due_time, priority,
                       completed, completed_at, ical_raw, last_modified, source,
                       dida_task_id, dida_project_id, reminders, repeat_flag)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'dida', ?, ?, ?, ?)""",
                    (todo.uid, todo.title, todo.description, todo.due_date, todo.due_time,
                     todo.priority, int(todo.completed), todo.completed_at, todo.ical_raw,
                     todo.last_modified, dida_task_id, dida_project_id,
                     todo.reminders, todo.repeat_flag),
                )
                new_count += 1
                logger.info(f"  NEW: uid={todo.uid}, title={todo.title}, completed={todo.completed}")
            elif _is_updated(existing, todo):
                # ─── 本地存在但有变更 → 更新 ───
                existing_uid = existing["uid"]
                await db.execute(
                    """UPDATE todos SET title=?, description=?, due_date=?, due_time=?, priority=?,
                       completed=?, completed_at=?, ical_raw=?, last_modified=?,
                       synced=0, updated_at=datetime('now','localtime'),
                       dida_task_id=COALESCE(?, dida_task_id),
                       dida_project_id=COALESCE(?, dida_project_id),
                       reminders=?, repeat_flag=?,
                       source=CASE WHEN ? IS NOT NULL THEN 'dida' ELSE source END
                       WHERE uid=?""",
                    (todo.title, todo.description, todo.due_date, todo.due_time, todo.priority,
                     int(todo.completed), todo.completed_at, todo.ical_raw, todo.last_modified,
                     dida_task_id, dida_project_id, todo.reminders, todo.repeat_flag,
                     dida_task_id, existing_uid),
                )
                updated_count += 1
                logger.info(f"  UPDATED: uid={existing_uid}, source_uid={todo.uid}, title={todo.title}, completed={todo.completed}")
            else:
                # ─── 本地存在且无变更，但可能需要更新关联 ID ───
                if dida_task_id and _needs_dida_link_update(existing, dida_task_id, dida_project_id):
                    await db.execute(
                        """UPDATE todos SET
                           dida_task_id=COALESCE(?, dida_task_id),
                           dida_project_id=COALESCE(?, dida_project_id),
                           source='dida',
                           updated_at=datetime('now','localtime')
                           WHERE uid=?""",
                        (dida_task_id, dida_project_id, existing["uid"]),
                    )
                skipped_count += 1
        logger.info(f"[Step 2] DONE: {new_count} new, {updated_count} updated, {skipped_count} unchanged")

        # ── Step 3: 检测已删除的任务 ──
        # 远程 API 不再返回的任务，说明用户在滴答清单中删除或完成了它
        logger.info("── Step 3: Detect removed todos (dida source only) ──")
        if use_api:
            # MCP API 模式：查找本地 source='dida' 但不在远程结果中的任务
            if remote_uids:
                # 构建动态 SQL：WHERE uid NOT IN (?, ?, ...)
                # ",".join(...) 用逗号连接多个 "?" 占位符
                removed_cursor = await db.execute(
                    "SELECT uid, remote_id, completed FROM todos WHERE source = 'dida' AND uid NOT IN ({})".format(
                        ",".join("?" for _ in remote_uids)
                    ),
                    tuple(remote_uids),  # set 转为 tuple 用于 SQL 参数
                )
            else:
                removed_cursor = await db.execute(
                    "SELECT uid, remote_id, completed FROM todos WHERE source = 'dida'"
                )
        else:
            # iCal 模式：逻辑相同
            if remote_uids:
                removed_cursor = await db.execute(
                    "SELECT uid, remote_id, completed FROM todos WHERE source = 'dida' AND uid NOT IN ({})".format(
                        ",".join("?" for _ in remote_uids)
                    ),
                    tuple(remote_uids),
                )
            else:
                removed_cursor = await db.execute(
                    "SELECT uid, remote_id, completed FROM todos WHERE source = 'dida'"
                )

        removed_rows = await removed_cursor.fetchall()
        for row in removed_rows:
            if not row["completed"]:
                # 只处理未完成的任务（已完成的本来就不需要标记）
                logger.info(f"  MARKED COMPLETED (removed from {source_name}): uid={row['uid']}")
                await db.execute(
                    "UPDATE todos SET completed=1, synced=0, updated_at=datetime('now','localtime') WHERE uid=?",
                    (row["uid"],),
                )
        await db.commit()
        logger.info(f"[Step 3] DONE: {len(removed_rows)} todos no longer in {source_name}")

        # ── Step 4: 将未同步的任务转发到 Zectrix ──
        logger.info("── Step 4: Forward unsynced todos to Zectrix ──")
        forwarder = await _get_forwarder()
        sync_ok = 0     # 同步成功计数
        sync_fail = 0   # 同步失败计数

        if forwarder:
            # 先去重：清理因跨平台关联产生的重复记录
            await _dedupe_dida_linked_todos(db, forwarder=forwarder)
            await db.commit()

            # 查询所有 synced=0（未同步）的任务
            cursor = await db.execute("SELECT * FROM todos WHERE synced = 0")
            unsynced = await cursor.fetchall()
            logger.info(f"  Found {len(unsynced)} unsynced todos")

            for row in unsynced:
                try:
                    remote_id = row["remote_id"]
                    is_completed = bool(row["completed"])  # int → bool 转换（0→False, 1→True）
                    title = row["title"]
                    source = row["source"] if "source" in row.keys() else "dida"

                    # 纯 Zectrix 来源的任务由反向同步处理，这里跳过
                    # 但如果该任务已关联了滴答清单 ID，说明它是跨平台的，需要处理
                    dida_task_id = row["dida_task_id"] if "dida_task_id" in row.keys() else None
                    if source == "zectrix" and not dida_task_id:
                        logger.info(f"  SKIP (zectrix source): uid={row['uid']}, title={title}")
                        await db.execute(
                            "UPDATE todos SET synced=1, synced_at=datetime('now','localtime') WHERE uid=?",
                            (row["uid"],),
                        )
                        sync_ok += 1
                        continue  # 跳过本次循环的剩余代码，进入下一次迭代

                    # ─── 已完成的任务 → 在 Zectrix 上也标记完成 ───
                    if is_completed:
                        if remote_id:
                            # 已在 Zectrix 上存在 → 直接标记完成
                            logger.info(f"  COMPLETE on Zectrix: uid={row['uid']}, remote_id={remote_id}, title={title}")
                            await forwarder.complete_todo(remote_id)
                        else:
                            # 还没在 Zectrix 上 → 先创建再完成
                            logger.info(f"  CREATE+COMPLETE on Zectrix: uid={row['uid']}, title={title}")
                            rid = await forwarder.create_todo(_row_to_todo(row))
                            await forwarder.complete_todo(rid)
                            await db.execute(
                                "UPDATE todos SET remote_id=? WHERE uid=?",
                                (rid, row["uid"]),
                            )
                        await db.execute(
                            "UPDATE todos SET synced=1, synced_at=datetime('now','localtime') WHERE uid=?",
                            (row["uid"],),
                        )
                        sync_ok += 1
                        continue

                    # ─── 活跃任务 → 创建或更新到 Zectrix ───
                    if not remote_id:
                        # 没有 remote_id → 还没同步过，在 Zectrix 上创建
                        rid = await forwarder.create_todo(_row_to_todo(row))
                        logger.info(f"  CREATE on Zectrix: uid={row['uid']} → remote_id={rid}, title={title}")
                        await db.execute(
                            "UPDATE todos SET synced=1, synced_at=datetime('now','localtime'), remote_id=? WHERE uid=?",
                            (rid, row["uid"]),
                        )
                    else:
                        # 有 remote_id → 已存在，更新 Zectrix 上的任务
                        logger.info(f"  UPDATE on Zectrix: remote_id={remote_id}, title={title}")
                        await forwarder.update_todo(remote_id, _row_to_todo(row))
                        await db.execute(
                            "UPDATE todos SET synced=1, synced_at=datetime('now','localtime') WHERE uid=?",
                            (row["uid"],),
                        )
                    sync_ok += 1
                except Exception as e:
                    # 单条任务同步失败不影响其他任务
                    logger.error(f"  FAILED: uid={row['uid']}, title={row['title']}, error={e}")
                    sync_fail += 1

            await db.commit()
            logger.info(f"[Step 4] DONE: {sync_ok} ok, {sync_fail} failed")
        else:
            logger.info("[Step 4] SKIPPED: no forwarder configured")

        await add_sync_log(
            "sync",
            "success" if sync_fail == 0 else "partial",
            f"Forwarded {sync_ok} todos" + (f", {sync_fail} failed" if sync_fail else ""),
            sync_ok,
        )

        # ── Step 5: 反向同步（Zectrix → 滴答清单）──
        if forwarder:
            await run_reverse_sync(forwarder, db)

        logger.info("========== SYNC COMPLETE ==========")
    except Exception as e:
        logger.error(f"========== SYNC FAILED: {e} ==========", exc_info=True)
    finally:
        # 确保数据库连接被关闭
        await db.close()


async def _find_existing_source_todo(db, todo, dida_task_id: str | None):
    """
    在本地数据库中查找与远程任务对应的已有记录。

    这个函数需要处理跨平台关联的情况：
    当一个 Zectrix 任务被创建到滴答清单后，本地保留的是 zectrix-* uid，
    但下次从滴答清单抓取时，同一个任务会有 dida-* uid。
    通过 dida_task_id 匹配，避免为同一任务创建重复记录。

    参数:
      db:            数据库连接
      todo:          远程抓取到的 Todo 对象
      dida_task_id:  滴答清单原始任务 ID（可能为 None）

    返回:
      数据库行（aiosqlite.Row）或 None
    """
    if dida_task_id:
        # 有 dida_task_id → 优先通过它查找（处理跨平台关联）
        # ORDER BY 确保优先匹配 zectrix 来源且有 remote_id 的行（即原始的那条）
        cursor = await db.execute(
            """SELECT * FROM todos
               WHERE dida_task_id = ? OR uid = ?
               ORDER BY
                 CASE WHEN source = 'zectrix' AND remote_id IS NOT NULL THEN 0 ELSE 1 END,
                 created_at ASC
               LIMIT 1""",
            (dida_task_id, todo.uid),
        )
        return await cursor.fetchone()

    # 没有 dida_task_id → 直接按 uid 查找
    cursor = await db.execute("SELECT * FROM todos WHERE uid = ?", (todo.uid,))
    return await cursor.fetchone()


def _normalized(value) -> str:
    """
    将任意值规范化为字符串。

    None → ""（空字符串）
    其他 → str(value)

    用于字段比较时消除 None 和 "" 的差异。
    """
    return "" if value is None else str(value)


def _row_field_changed(existing_row, remote_todo) -> bool:
    """
    检查本地数据库行和远程 Todo 对象之间是否有字段变化。

    逐字段比较 title, description, due_date, due_time, priority, completed_at, reminders，
    以及 repeat_flag（通过 _repeat_as_zectrix 统一格式后再比较）。

    参数:
      existing_row:  本地数据库行
      remote_todo:   远程 Todo 对象

    返回:
      True = 有字段变化, False = 无变化
    """
    fields = (
        "title",
        "description",
        "due_date",
        "due_time",
        "priority",
        "completed_at",
        "reminders",
    )
    for field in fields:
        if field in existing_row.keys() and _normalized(existing_row[field]) != _normalized(getattr(remote_todo, field, None)):
            return True
    # repeat_flag 比较需要统一格式（RRULE 格式 vs Zectrix 格式）
    if "repeat_flag" in existing_row.keys():
        return _repeat_as_zectrix(existing_row["repeat_flag"]) != _repeat_as_zectrix(remote_todo.repeat_flag)
    return False


def _needs_dida_link_update(existing_row, dida_task_id: str | None, dida_project_id: str | None) -> bool:
    """
    判断是否需要更新已有记录的滴答清单关联信息。

    当本地记录的 dida_task_id、dida_project_id 与最新的不匹配时返回 True。
    """
    if not dida_task_id:
        return False
    return (
        existing_row["dida_task_id"] != dida_task_id
        or (dida_project_id is not None and existing_row["dida_project_id"] != dida_project_id)
        or existing_row["source"] != "dida"
    )


def _is_updated(existing_row, remote_todo) -> bool:
    """
    判断远程任务相比本地记录是否有更新。

    判断逻辑（任一条件满足即视为有更新）：
      1. 本地记录标记为未同步（synced=0）→ 说明之前更新过但还没同步到 Zectrix
      2. 完成状态发生了变化
      3. 关键字段（title, description 等）发生了变化
      4. last_modified 时间戳变了
    """
    if existing_row["synced"] == 0:
        return True
    # 检测完成状态变化
    if bool(existing_row["completed"]) != remote_todo.completed:
        return True
    # 检测字段变化
    if _row_field_changed(existing_row, remote_todo):
        return True
    # 检测 last_modified 变化
    existing_lm = existing_row["last_modified"]
    if existing_lm is None and remote_todo.last_modified is not None:
        return True
    if existing_lm is not None and remote_todo.last_modified is not None:
        return existing_lm != remote_todo.last_modified
    return False


def _remote_field_changed(local_row, remote: dict) -> bool:
    """
    检查本地记录和 Zectrix 远程数据之间是否有字段变化。

    用于反向同步中判断 Zectrix 上的任务是否被用户修改了。

    参数:
      local_row:  本地数据库行
      remote:     Zectrix API 返回的任务字典

    返回:
      True = 有变化, False = 无变化
    """
    # 检查 Zectrix 上的完成状态
    is_completed = remote.get("status") == 1 or remote.get("completed") is True
    repeat_type = remote.get("repeatType", "none") or "none"
    comparisons = (
        ("title", remote.get("title", "")),
        ("description", remote.get("description", "")),
        ("due_date", remote.get("dueDate")),
        ("due_time", remote.get("dueTime")),
        ("priority", remote.get("priority", 0)),
        ("completed", int(is_completed)),
    )
    for field, remote_value in comparisons:
        if field in local_row.keys() and _normalized(local_row[field]) != _normalized(remote_value):
            return True
    if "repeat_flag" in local_row.keys():
        return _repeat_as_zectrix(local_row["repeat_flag"]) != _repeat_as_zectrix(repeat_type)
    return False


def _repeat_as_zectrix(repeat_flag: str | None) -> str:
    """
    将重复规则统一转换为 Zectrix 格式。

    滴答清单使用 RRULE 格式（如 "RRULE:FREQ=DAILY"），
    Zectrix 使用简单字符串（如 "daily"）。
    统一为 Zectrix 格式后再做比较。

    参数:
      repeat_flag: 重复规则字符串，可能是 RRULE 格式或 Zectrix 格式

    返回:
      "daily" / "weekly" / "monthly" / "yearly" / "none"
    """
    if not repeat_flag:
        return "none"
    value = str(repeat_flag).strip()
    lower = value.lower()
    # 如果已经是 Zectrix 格式，直接返回
    if lower in {"none", "daily", "weekly", "monthly", "yearly"}:
        return lower
    # 否则尝试从 RRULE 字符串中提取频率
    upper = value.upper()
    if "FREQ=DAILY" in upper:
        return "daily"
    if "FREQ=WEEKLY" in upper:
        return "weekly"
    if "FREQ=MONTHLY" in upper:
        return "monthly"
    if "FREQ=YEARLY" in upper:
        return "yearly"
    return "none"


def _zectrix_repeat_to_dida(repeat_type: str | None) -> str:
    """
    将 Zectrix 重复格式转换为滴答清单 RRULE 格式。

    反向同步时使用：Zectrix 上的重复规则需要转换后才能写入滴答清单。

    参数:
      repeat_type: Zectrix 格式的重复类型（如 "daily"）

    返回:
      RRULE 字符串（如 "RRULE:FREQ=DAILY"），或 "none"
    """
    repeat_type = (repeat_type or "none").lower()
    return {
        "daily": "RRULE:FREQ=DAILY",
        "weekly": "RRULE:FREQ=WEEKLY",
        "monthly": "RRULE:FREQ=MONTHLY",
        "yearly": "RRULE:FREQ=YEARLY",
    }.get(repeat_type, "none")


def _choose_canonical_dida_row(rows):
    """
    在多条重复记录中选择应该保留的那一条。

    排序规则：
      1. 优先保留 source='zectrix' 且有 remote_id 的（即原始的 Zectrix 记录）
      2. 其次按创建时间最早的
      3. 最后按 uid 排序

    返回排序后的第一条记录。
    """
    return sorted(
        rows,
        key=lambda row: (
            0 if row["source"] == "zectrix" and row["remote_id"] else 1,
            row["created_at"] or "",
            row["uid"],
        ),
    )[0]


async def _dedupe_dida_linked_todos(db, forwarder=None) -> int:
    """
    去重：清理同一滴答清单任务在本地数据库中的重复记录。

    【为什么会重复？】
    当一个 Zectrix 任务被反向创建到滴答清单后：
    1. 本地保留了 zectrix-{id} 记录，并关联了 dida_task_id
    2. 下次正向同步时，又抓到了同一个任务（uid 为 dida-{task_id}）
    3. 如果匹配逻辑失败，就会创建第二条记录

    【去重逻辑】
    找到所有 dida_task_id 相同的多条记录，保留最"正宗"的那条，删除其余的。
    如果提供了 forwarder，同时删除 Zectrix 上的重复副本。

    参数:
      db:        数据库连接
      forwarder: Zectrix 转发器（可选，用于同时删除远端重复）

    返回:
      删除的记录数量
    """
    # 找到所有有重复 dida_task_id 的分组
    cursor = await db.execute(
        """SELECT dida_task_id
           FROM todos
           WHERE dida_task_id IS NOT NULL AND dida_task_id != ''
           GROUP BY dida_task_id
           HAVING COUNT(*) > 1"""
    )
    duplicate_groups = await cursor.fetchall()
    removed = 0

    for group in duplicate_groups:
        dida_task_id = group["dida_task_id"]
        cursor = await db.execute(
            "SELECT * FROM todos WHERE dida_task_id = ? ORDER BY created_at ASC",
            (dida_task_id,),
        )
        rows = await cursor.fetchall()
        if len(rows) < 2:
            continue

        # 选择要保留的记录
        keep = _choose_canonical_dida_row(rows)
        keep_remote_id = keep["remote_id"]
        for row in rows:
            if row["uid"] == keep["uid"]:
                continue  # 跳过要保留的那条

            # 删除 Zectrix 上的重复副本
            duplicate_remote_id = row["remote_id"]
            if forwarder and duplicate_remote_id and duplicate_remote_id != keep_remote_id:
                try:
                    logger.info(
                        "  DELETE duplicate Zectrix todo: dida_task_id=%s, uid=%s, remote_id=%s, keep_uid=%s",
                        dida_task_id,
                        row["uid"],
                        duplicate_remote_id,
                        keep["uid"],
                    )
                    await forwarder.delete_todo(duplicate_remote_id)
                except Exception as e:
                    logger.warning(
                        "  Failed to delete duplicate Zectrix todo: remote_id=%s, error=%s",
                        duplicate_remote_id,
                        e,
                    )
                    continue

            # 删除本地的重复记录
            logger.info(
                "  DELETE duplicate local todo: dida_task_id=%s, uid=%s, keep_uid=%s",
                dida_task_id,
                row["uid"],
                keep["uid"],
            )
            await db.execute("DELETE FROM todos WHERE uid = ?", (row["uid"],))
            removed += 1

    if removed:
        logger.info("  DEDUPED: removed %s duplicate local todo rows", removed)
    return removed


def _row_to_todo(row) -> dict:
    """
    将数据库行转换为 Todo 对象。

    用于将本地数据库中的任务数据传给 ZectrixForwarder 的方法。
    forwarder 需要的是 Todo 对象而不是原始的数据库行。

    参数:
      row: 数据库查询结果的行

    返回:
      Todo 对象
    """
    from app.models import Todo
    return Todo(
        uid=row["uid"],
        title=row["title"],
        description=row["description"],
        due_date=row["due_date"],
        due_time=row["due_time"],
        priority=row["priority"] or 0,
        completed=bool(row["completed"]),
        completed_at=row["completed_at"],
        reminders=row["reminders"] if "reminders" in row.keys() else "",
        repeat_flag=row["repeat_flag"] if "repeat_flag" in row.keys() else "",
    )


async def run_reverse_sync(forwarder, db=None):
    """
    执行反向同步：从 Zectrix 拉取变更并回写到本地数据库和滴答清单。

    完整流程（7 个阶段）：
      Phase 1: 从 Zectrix 获取所有任务
      Phase 2: 与本地有 remote_id 的记录匹配
      Phase 3: 更新匹配的记录 / 检测 Zectrix 上的删除
      Phase 4: 导入 Zectrix 新建的任务
      Phase 5: 字段修改回写到滴答清单（MCP API）
      Phase 6: 完成状态回写到滴答清单（MCP API）
      Phase 7: 新建任务回写到滴答清单（MCP API）

    参数:
      forwarder: ZectrixForwarder 实例
      db:        数据库连接（可选，如果不传则新建连接）
    """
    logger.info("── Step 5: Reverse sync (Zectrix → local) ──")

    close_db = False
    if db is None:
        # 如果没有传入数据库连接，创建一个新的
        db = await get_db()
        close_db = True  # 标记需要在使用完后关闭

    try:
        # ── Phase 1: 从 Zectrix 拉取所有任务 ──
        logger.info("  [Phase 1] Fetching todos from Zectrix...")
        remote_todos = await forwarder.fetch_remote_todos()
        # 构建按 ID 查找的字典和 ID 集合
        remote_map = {str(t["id"]): t for t in remote_todos}
        remote_ids = set(remote_map.keys())

        for rid, t in remote_map.items():
            logger.info(f"    Zectrix todo: id={rid}, title={t.get('title')}, status={t.get('status')}, due={t.get('dueDate')}")

        logger.info(f"  [Phase 1] DONE: fetched {len(remote_todos)} todos, ids={sorted(remote_ids)}")

        # ── Phase 2: 与本地数据库匹配 ──
        logger.info("  [Phase 2] Match with local DB...")
        # 查询所有有 remote_id 的本地记录（即已同步到 Zectrix 的任务）
        cursor = await db.execute(
            """SELECT uid, title, description, due_date, due_time, priority,
                      remote_id, remote_updated_at, completed, source,
                      dida_task_id, dida_project_id, repeat_flag
               FROM todos
               WHERE remote_id IS NOT NULL"""
        )
        local_linked = await cursor.fetchall()
        local_remote_ids = {row["remote_id"] for row in local_linked}

        for row in local_linked:
            logger.info(f"    Local linked: uid={row['uid']}, remote_id={row['remote_id']}, source={row['source']}, completed={row['completed']}")

        logger.info(f"  [Phase 2] DONE: {len(local_linked)} local todos with remote_id, remote_ids={sorted(local_remote_ids)}")

        updated = 0    # Zectrix 上有更新的任务数
        deleted = 0    # Zectrix 上已删除的任务数
        created = 0    # Zectrix 上新建的任务数

        # ── Phase 3: 更新匹配的记录 / 检测删除 ──
        logger.info("  [Phase 3] Update matched & detect deletions...")
        for row in local_linked:
            rid = row["remote_id"]
            if rid in remote_ids:
                # ─── Zectrix 上仍存在 → 检查是否有变更 ───
                remote = remote_map[rid]
                remote_update = str(remote.get("updateDate", "")) if remote.get("updateDate") is not None else ""
                local_update = row["remote_updated_at"] or ""

                if remote_update != local_update or _remote_field_changed(row, remote):
                    # 有变更 → 用 Zectrix 的数据更新本地记录
                    is_completed = remote.get("status") == 1 or remote.get("completed") is True
                    repeat_type = remote.get("repeatType", "none") or "none"
                    logger.info(f"    UPDATE: uid={row['uid']}, remote_id={rid}, title={remote.get('title')}, completed={is_completed}")
                    await db.execute(
                        """UPDATE todos SET title=?, description=?, due_date=?, due_time=?,
                           priority=?, completed=?, remote_updated_at=?,
                           repeat_flag=?, synced=1, updated_at=datetime('now','localtime')
                           WHERE uid=?""",
                        (
                            remote.get("title", ""),
                            remote.get("description", ""),
                            remote.get("dueDate"),
                            remote.get("dueTime"),
                            remote.get("priority", 0),
                            int(is_completed),
                            remote_update,
                            repeat_type,
                            row["uid"],
                        ),
                    )
                    updated += 1
                else:
                    logger.debug(f"    UNCHANGED: uid={row['uid']}, remote_id={rid}")
            else:
                # ─── Zectrix 上不存在了 → 用户在 Zectrix 上删除了 ───
                if not row["completed"]:
                    logger.info(f"    DELETED on Zectrix: uid={row['uid']}, remote_id={rid}")
                    await db.execute(
                        "UPDATE todos SET completed=1, updated_at=datetime('now','localtime') WHERE uid=?",
                        (row["uid"],),
                    )
                    deleted += 1
        logger.info(f"  [Phase 3] DONE: {updated} updated, {deleted} deleted")

        # ── Phase 4: 导入 Zectrix 新建的任务 ──
        logger.info("  [Phase 4] Import new Zectrix todos...")
        # 集合差集：Zectrix 上有但本地没有的 remote_id
        new_remote_ids = remote_ids - local_remote_ids
        if new_remote_ids:
            logger.info(f"    New remote ids: {sorted(new_remote_ids)}")
        else:
            logger.info("    No new todos to import")

        for rid in new_remote_ids:
            remote = remote_map[rid]
            is_completed = remote.get("status") == 1 or remote.get("completed") is True
            remote_update = str(remote.get("updateDate", "")) if remote.get("updateDate") is not None else ""
            repeat_type = remote.get("repeatType", "none") or "none"
            uid = f"zectrix-{rid}"  # Zectrix 来源的任务用 "zectrix-{ID}" 作为 uid

            logger.info(f"    IMPORT: uid={uid}, id={rid}, title={remote.get('title')}, due={remote.get('dueDate')}, status={remote.get('status')}")
            await db.execute(
                """INSERT INTO todos (uid, title, description, due_date, due_time, priority,
                   completed, remote_id, remote_updated_at, repeat_flag, synced, ical_raw, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '', 'zectrix')""",
                (
                    uid,
                    remote.get("title", ""),
                    remote.get("description", ""),
                    remote.get("dueDate"),
                    remote.get("dueTime"),
                    remote.get("priority", 0),
                    int(is_completed),
                    rid,
                    remote_update,
                    repeat_type,
                ),
            )
            created += 1

        await db.commit()
        logger.info(f"  [Phase 4] DONE: {created} imported, db committed")

        # ── Phase 5: 将 Zectrix 上的字段修改回写到滴答清单 ──
        logger.info("  [Phase 5] Reverse sync updates to Dida365...")
        dida_updated = await _reverse_update_to_dida(db, local_linked, remote_map)
        logger.info(f"  [Phase 5] DONE: {dida_updated} tasks updated on Dida365")

        # ── Phase 6: 将 Zectrix 上完成的任务回写到滴答清单 ──
        logger.info("  [Phase 6] Reverse sync completions to Dida365...")
        dida_completed = await _reverse_complete_to_dida(db, local_linked, remote_map)
        logger.info(f"  [Phase 6] DONE: {dida_completed} tasks completed on Dida365")

        # ── Phase 7: 将 Zectrix 上新建的任务回写到滴答清单 ──
        logger.info("  [Phase 7] Create Zectrix tasks on Dida365...")
        dida_created = await _reverse_create_to_dida(db)
        logger.info(f"  [Phase 7] DONE: {dida_created} tasks created on Dida365")

        logger.info(f"── Step 5 DONE: {updated} updated, {created} new, {deleted} deleted, {dida_updated} dida-updated, {dida_completed} dida-completed, {dida_created} dida-created ──")
        await add_sync_log(
            "reverse_sync",
            "success",
            f"Reverse sync: {updated} updated, {created} new, {deleted} deleted, {dida_updated} dida-updated, {dida_completed} dida-completed, {dida_created} dida-created",
            updated + created + deleted + dida_updated + dida_completed + dida_created,
        )
    except Exception as e:
        logger.error(f"── Step 5 FAILED: {e} ──", exc_info=True)
        try:
            await db.rollback()  # 回滚事务，撤销未提交的修改
        except Exception:
            pass
        await add_sync_log("reverse_sync", "failed", str(e), 0)
    finally:
        if close_db:
            await db.close()


async def _reverse_update_to_dida(db, local_linked, remote_map) -> int:
    """
    反向同步 Phase 5: 将 Zectrix 上修改的字段回写到滴答清单。

    遍历本地已关联的记录，如果 Zectrix 上的数据有变化，
    通过 MCP API 更新滴答清单上对应的任务。

    参数:
      db:           数据库连接
      local_linked: 本地有 remote_id 的记录列表
      remote_map:   Zectrix 任务字典 {remote_id: task_data}

    返回:
      成功更新的任务数量
    """
    from app.services.dida_client import get_dida_mcp_client

    # 检查反向同步模式是否为 MCP
    reverse_mode = await get_config("reverse_sync_mode")
    if reverse_mode != "mcp":
        logger.info(f"    Reverse sync mode is '{reverse_mode}', skipping MCP update")
        return 0

    client = await get_dida_mcp_client()
    if not client:
        logger.info("    Dida MCP not configured, skipping reverse update")
        return 0

    try:
        await client.initialize()  # MCP 协议握手
    except Exception as e:
        logger.warning(f"    Dida MCP init failed: {e}")
        return 0

    updated_count = 0
    for row in local_linked:
        rid = row["remote_id"]
        if rid not in remote_map:
            continue  # Zectrix 上已删除的任务，跳过

        dida_task_id = row["dida_task_id"] if "dida_task_id" in row.keys() else None
        dida_project_id = row["dida_project_id"] if "dida_project_id" in row.keys() else None
        if not dida_task_id:
            continue  # 没有关联滴答清单 ID 的任务，跳过

        remote = remote_map[rid]
        zectrix_completed = remote.get("status") == 1 or remote.get("completed") is True
        # 已完成的任务由 Phase 6 处理，这里只处理字段修改
        if zectrix_completed or not _remote_field_changed(row, remote):
            continue

        try:
            logger.info(
                "    Updating Dida365 via MCP: task_id=%s, title=%s, due=%s %s",
                dida_task_id,
                remote.get("title"),
                remote.get("dueDate"),
                remote.get("dueTime"),
            )
            await client.update_task(
                task_id=dida_task_id,
                project_id=dida_project_id,
                title=remote.get("title", ""),
                content=remote.get("description", "") or "",
                due_date=remote.get("dueDate"),
                due_time=remote.get("dueTime"),
                priority=remote.get("priority", 0),
                repeat_flag=_zectrix_repeat_to_dida(remote.get("repeatType")),
            )
            updated_count += 1
        except Exception as e:
            logger.error(f"    Failed to update Dida365: task_id={dida_task_id}, error={e}")

    return updated_count


async def _reverse_complete_to_dida(db, local_linked, remote_map) -> int:
    """
    反向同步 Phase 6: 将 Zectrix 上完成的任务在滴答清单也标记完成。

    条件：
      - Zectrix 上任务状态为已完成（status==1）
      - 本地记录的 completed 还是 False
      - 任务来源是 dida（滴答清单的任务才需要回写）

    参数:
      db:           数据库连接
      local_linked: 本地有 remote_id 的记录列表
      remote_map:   Zectrix 任务字典

    返回:
      成功标记完成的任务数量
    """
    from app.services.dida_client import get_dida_mcp_client

    reverse_mode = await get_config("reverse_sync_mode")
    if reverse_mode != "mcp":
        logger.info(f"    Reverse sync mode is '{reverse_mode}', skipping MCP completion")
        return 0

    client = await get_dida_mcp_client()
    if not client:
        logger.info("    Dida MCP not configured, skipping reverse completion")
        return 0

    try:
        await client.initialize()
    except Exception as e:
        logger.warning(f"    Dida MCP init failed: {e}")
        return 0

    completed_count = 0
    for row in local_linked:
        rid = row["remote_id"]
        if rid not in remote_map:
            continue

        remote = remote_map[rid]
        zectrix_completed = remote.get("status") == 1 or remote.get("completed") is True
        local_completed = bool(row["completed"])
        source = row["source"] if "source" in row.keys() else "dida"

        # 只有当 Zectrix 完成了但本地还没标记、且任务是滴答清单来源时才回写
        if zectrix_completed and not local_completed and source == "dida":
            dida_task_id = row["dida_task_id"] if "dida_task_id" in row.keys() else None
            dida_project_id = row["dida_project_id"] if "dida_project_id" in row.keys() else None

            if dida_task_id and dida_project_id:
                try:
                    logger.info(f"    Completing on Dida365 via MCP: task_id={dida_task_id}, project_id={dida_project_id}, title={remote.get('title')}")
                    await client.complete_task(dida_project_id, dida_task_id)
                    completed_count += 1
                    logger.info(f"    COMPLETED on Dida365: task_id={dida_task_id}")
                except Exception as e:
                    logger.error(f"    Failed to complete on Dida365: task_id={dida_task_id}, error={e}")

    return completed_count


async def _reverse_create_to_dida(db) -> int:
    """
    反向同步 Phase 7: 将 Zectrix 上新建的任务创建到滴答清单。

    查找本地 source='zectrix' 且没有 dida_task_id 的记录，
    这些是用户直接在 Zectrix 上创建的任务，需要在滴答清单上也创建一份。

    创建成功后，将返回的滴答清单任务 ID 保存到本地记录的 dida_task_id 字段。

    参数:
      db: 数据库连接

    返回:
      成功创建的任务数量
    """
    from app.services.dida_client import get_dida_mcp_client

    reverse_mode = await get_config("reverse_sync_mode")
    if reverse_mode != "mcp":
        logger.info(f"    Reverse sync mode is '{reverse_mode}', skipping MCP creation")
        return 0

    client = await get_dida_mcp_client()
    if not client:
        logger.info("    Dida MCP not configured, skipping reverse creation")
        return 0

    try:
        await client.initialize()
    except Exception as e:
        logger.warning(f"    Dida MCP init failed: {e}")
        return 0

    # 确定目标项目：将 Zectrix 任务创建到用户配置的第一个滴答清单项目中
    project_id_raw = await get_config("dida_project_id")
    project_ids = [p.strip() for p in project_id_raw.split(",") if p.strip()] if project_id_raw else []
    if not project_ids:
        logger.info("    No Dida365 project selected, skipping reverse creation")
        return 0
    target_project = project_ids[0]

    # 查找所有 Zectrix 来源且还没关联滴答清单的任务
    cursor = await db.execute(
        "SELECT uid, title, description, due_date, due_time, priority, completed, reminders, repeat_flag FROM todos WHERE source = 'zectrix' AND dida_task_id IS NULL"
    )
    zectrix_tasks = await cursor.fetchall()
    logger.info(f"    Found {len(zectrix_tasks)} Zectrix tasks without dida_task_id")

    created_count = 0
    for row in zectrix_tasks:
        title = row["title"]
        if not title:
            continue  # 没有标题的任务跳过
        try:
            result = await client.create_task(
                title=title,
                project_id=target_project,
                content=row["description"] or "",
                due_date=row["due_date"],
                due_time=row["due_time"],
                priority=row["priority"] or 0,
                reminders=row["reminders"] if "reminders" in row.keys() else "",
                repeat_flag=row["repeat_flag"] if "repeat_flag" in row.keys() else "",
            )
            # 解析 MCP 返回的 JSON，提取新创建任务的 ID
            import json
            task_data = json.loads(result) if result.strip().startswith("{") else {}
            dida_task_id = task_data.get("id")
            if dida_task_id:
                # 将滴答清单任务 ID 关联到本地记录
                await db.execute(
                    "UPDATE todos SET dida_task_id=?, dida_project_id=? WHERE uid=?",
                    (dida_task_id, target_project, row["uid"]),
                )
                await db.commit()
                logger.info(f"    CREATED on Dida365: title={title}, task_id={dida_task_id}")
                # 如果 Zectrix 上这个任务已完成，也在滴答清单上标记完成
                if row["completed"]:
                    try:
                        await client.complete_task(target_project, dida_task_id)
                        logger.info(f"    COMPLETED on Dida365: title={title}, task_id={dida_task_id}")
                    except Exception as e:
                        logger.warning(f"    Failed to complete on Dida365: title={title}, error={e}")
                created_count += 1
            else:
                logger.warning(f"    Created on Dida365 but no task_id returned: title={title}, result={result[:200]}")
                created_count += 1
        except Exception as e:
            logger.error(f"    Failed to create on Dida365: title={title}, error={e}")

    return created_count
