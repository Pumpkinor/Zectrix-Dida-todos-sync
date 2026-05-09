import logging
from datetime import datetime

from app.database import get_db, get_config, add_sync_log
from app.services.fetcher import fetch_ical, fetch_dida_tasks
from app.services.forwarders.zectrix import ZectrixForwarder

logger = logging.getLogger(__name__)


async def _get_forwarder() -> ZectrixForwarder | None:
    api_key = await get_config("zectrix_api_key")
    device_id = await get_config("zectrix_device_id")
    base_url = await get_config("zectrix_base_url")
    if not api_key or not device_id:
        logger.warning("Zectrix not configured: missing api_key or device_id")
        return None
    return ZectrixForwarder(api_key, device_id, base_url)


async def _use_dida_api() -> bool:
    """Check if Dida365 MCP mode is selected and configured."""
    mode = await get_config("dida_sync_mode")
    return mode == "mcp"


async def run_sync():
    """Run a full sync cycle: fetch → compare → store → forward."""
    logger.info("========== SYNC START ==========")

    use_api = await _use_dida_api()
    ical_url = await get_config("ical_url")

    if not use_api and not ical_url:
        logger.warning("No data source configured (neither Dida API nor iCal URL)")
        return

    # ── Step 1: Fetch tasks ──
    logger.info(f"── Step 1: Fetch tasks ({'Dida API' if use_api else 'iCal'}) ──")
    try:
        if use_api:
            remote_todos = await fetch_dida_tasks()
            source_name = "dida_api"
        else:
            remote_todos = await fetch_ical(ical_url)
            source_name = "ical"
    except Exception as e:
        logger.error(f"[Step 1] FAILED: {e}")
        await add_sync_log("fetch", "failed", str(e), 0)
        return

    for t in remote_todos:
        logger.info(f"  {source_name} todo: uid={t.uid}, title={t.title}, due={t.due_date}, completed={t.completed}")
    logger.info(f"[Step 1] DONE: fetched {len(remote_todos)} todos from {source_name}")
    await add_sync_log("fetch", "success", f"Fetched {len(remote_todos)} todos from {source_name}", len(remote_todos))

    remote_uids = {t.uid for t in remote_todos}
    # Build a map for quick lookup: uid → todo
    remote_map = {t.uid: t for t in remote_todos}

    db = await get_db()
    try:
        # ── Step 2: Upsert todos into DB ──
        logger.info("── Step 2: Upsert todos into DB ──")
        new_count = 0
        updated_count = 0
        skipped_count = 0

        for todo in remote_todos:
            # Store dida IDs as extra columns if available
            dida_task_id = getattr(todo, '_dida_task_id', None)
            dida_project_id = getattr(todo, '_dida_project_id', None)

            existing = await _find_existing_source_todo(db, todo, dida_task_id)

            if existing is None:
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

        # ── Step 3: Detect removed/completed tasks ──
        logger.info("── Step 3: Detect removed todos (dida source only) ──")
        if use_api:
            # With API: tasks in DB but not in API response are deleted in Dida365
            # OR we can check completed status directly from status field
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
        else:
            # With iCal: tasks missing from feed are considered removed/completed
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
                logger.info(f"  MARKED COMPLETED (removed from {source_name}): uid={row['uid']}")
                await db.execute(
                    "UPDATE todos SET completed=1, synced=0, updated_at=datetime('now','localtime') WHERE uid=?",
                    (row["uid"],),
                )
        await db.commit()
        logger.info(f"[Step 3] DONE: {len(removed_rows)} todos no longer in {source_name}")

        # ── Step 4: Forward unsynced todos to Zectrix ──
        logger.info("── Step 4: Forward unsynced todos to Zectrix ──")
        forwarder = await _get_forwarder()
        sync_ok = 0
        sync_fail = 0

        if forwarder:
            await _dedupe_dida_linked_todos(db, forwarder=forwarder)
            await db.commit()

            cursor = await db.execute("SELECT * FROM todos WHERE synced = 0")
            unsynced = await cursor.fetchall()
            logger.info(f"  Found {len(unsynced)} unsynced todos")

            for row in unsynced:
                try:
                    remote_id = row["remote_id"]
                    is_completed = bool(row["completed"])
                    title = row["title"]
                    source = row["source"] if "source" in row.keys() else "dida"

                    # Pure Zectrix-origin tasks should be handled by reverse sync. Once a
                    # Dida task id exists, the row is linked and Dida-side edits must be
                    # propagated back to the existing Zectrix todo.
                    dida_task_id = row["dida_task_id"] if "dida_task_id" in row.keys() else None
                    if source == "zectrix" and not dida_task_id:
                        logger.info(f"  SKIP (zectrix source): uid={row['uid']}, title={title}")
                        await db.execute(
                            "UPDATE todos SET synced=1, synced_at=datetime('now','localtime') WHERE uid=?",
                            (row["uid"],),
                        )
                        sync_ok += 1
                        continue

                    # Completed dida task → complete on Zectrix
                    if is_completed:
                        if remote_id:
                            logger.info(f"  COMPLETE on Zectrix: uid={row['uid']}, remote_id={remote_id}, title={title}")
                            await forwarder.complete_todo(remote_id)
                        else:
                            # Create on Zectrix then complete
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

                    # Active task → create or update on Zectrix
                    if not remote_id:
                        rid = await forwarder.create_todo(_row_to_todo(row))
                        logger.info(f"  CREATE on Zectrix: uid={row['uid']} → remote_id={rid}, title={title}")
                        await db.execute(
                            "UPDATE todos SET synced=1, synced_at=datetime('now','localtime'), remote_id=? WHERE uid=?",
                            (rid, row["uid"]),
                        )
                    else:
                        logger.info(f"  UPDATE on Zectrix: remote_id={remote_id}, title={title}")
                        await forwarder.update_todo(remote_id, _row_to_todo(row))
                        await db.execute(
                            "UPDATE todos SET synced=1, synced_at=datetime('now','localtime') WHERE uid=?",
                            (row["uid"],),
                        )
                    sync_ok += 1
                except Exception as e:
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

        # ── Step 5: Reverse sync ──
        if forwarder:
            await run_reverse_sync(forwarder, db)

        logger.info("========== SYNC COMPLETE ==========")
    except Exception as e:
        logger.error(f"========== SYNC FAILED: {e} ==========", exc_info=True)
    finally:
        await db.close()


async def _find_existing_source_todo(db, todo, dida_task_id: str | None):
    """Find an existing local row for a Dida/iCal todo.

    Dida tasks created from Zectrix keep their original zectrix-* uid locally.
    Matching by dida_task_id prevents the next Dida fetch from creating a second
    dida-* row for the same cross-linked task.
    """
    if dida_task_id:
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

    cursor = await db.execute("SELECT * FROM todos WHERE uid = ?", (todo.uid,))
    return await cursor.fetchone()


def _normalized(value) -> str:
    return "" if value is None else str(value)


def _row_field_changed(existing_row, remote_todo) -> bool:
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
    if "repeat_flag" in existing_row.keys():
        return _repeat_as_zectrix(existing_row["repeat_flag"]) != _repeat_as_zectrix(remote_todo.repeat_flag)
    return False


def _needs_dida_link_update(existing_row, dida_task_id: str | None, dida_project_id: str | None) -> bool:
    if not dida_task_id:
        return False
    return (
        existing_row["dida_task_id"] != dida_task_id
        or (dida_project_id is not None and existing_row["dida_project_id"] != dida_project_id)
        or existing_row["source"] != "dida"
    )


def _is_updated(existing_row, remote_todo) -> bool:
    if existing_row["synced"] == 0:
        return True
    # Detect completion status change
    if bool(existing_row["completed"]) != remote_todo.completed:
        return True
    if _row_field_changed(existing_row, remote_todo):
        return True
    existing_lm = existing_row["last_modified"]
    if existing_lm is None and remote_todo.last_modified is not None:
        return True
    if existing_lm is not None and remote_todo.last_modified is not None:
        return existing_lm != remote_todo.last_modified
    return False


def _remote_field_changed(local_row, remote: dict) -> bool:
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
    if not repeat_flag:
        return "none"
    value = str(repeat_flag).strip()
    lower = value.lower()
    if lower in {"none", "daily", "weekly", "monthly", "yearly"}:
        return lower
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
    repeat_type = (repeat_type or "none").lower()
    return {
        "daily": "RRULE:FREQ=DAILY",
        "weekly": "RRULE:FREQ=WEEKLY",
        "monthly": "RRULE:FREQ=MONTHLY",
        "yearly": "RRULE:FREQ=YEARLY",
    }.get(repeat_type, "none")


def _choose_canonical_dida_row(rows):
    return sorted(
        rows,
        key=lambda row: (
            0 if row["source"] == "zectrix" and row["remote_id"] else 1,
            row["created_at"] or "",
            row["uid"],
        ),
    )[0]


async def _dedupe_dida_linked_todos(db, forwarder=None) -> int:
    """Remove local duplicate rows that point at the same Dida task.

    When a Zectrix-origin task is created on Dida, the local zectrix-* row gets a
    dida_task_id. Older versions then created a second dida-* row for that same
    task and forwarded it to Zectrix. This cleanup keeps the original linked row
    and removes the extra local row. If a forwarder is available, the extra
    Zectrix copy is deleted as well.
    """
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

        keep = _choose_canonical_dida_row(rows)
        keep_remote_id = keep["remote_id"]
        for row in rows:
            if row["uid"] == keep["uid"]:
                continue

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
    """Pull all changes from Zectrix back to local DB."""
    logger.info("── Step 5: Reverse sync (Zectrix → local) ──")

    close_db = False
    if db is None:
        db = await get_db()
        close_db = True

    try:
        # ── Phase 1: Fetch from Zectrix ──
        logger.info("  [Phase 1] Fetching todos from Zectrix...")
        remote_todos = await forwarder.fetch_remote_todos()
        remote_map = {str(t["id"]): t for t in remote_todos}
        remote_ids = set(remote_map.keys())

        for rid, t in remote_map.items():
            logger.info(f"    Zectrix todo: id={rid}, title={t.get('title')}, status={t.get('status')}, due={t.get('dueDate')}")

        logger.info(f"  [Phase 1] DONE: fetched {len(remote_todos)} todos, ids={sorted(remote_ids)}")

        # ── Phase 2: Match with local ──
        logger.info("  [Phase 2] Match with local DB...")
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

        updated = 0
        deleted = 0
        created = 0

        # ── Phase 3: Update matched / detect deletions ──
        logger.info("  [Phase 3] Update matched & detect deletions...")
        for row in local_linked:
            rid = row["remote_id"]
            if rid in remote_ids:
                remote = remote_map[rid]
                remote_update = str(remote.get("updateDate", "")) if remote.get("updateDate") is not None else ""
                local_update = row["remote_updated_at"] or ""

                if remote_update != local_update or _remote_field_changed(row, remote):
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
                if not row["completed"]:
                    logger.info(f"    DELETED on Zectrix: uid={row['uid']}, remote_id={rid}")
                    await db.execute(
                        "UPDATE todos SET completed=1, updated_at=datetime('now','localtime') WHERE uid=?",
                        (row["uid"],),
                    )
                    deleted += 1
        logger.info(f"  [Phase 3] DONE: {updated} updated, {deleted} deleted")

        # ── Phase 4: Import new Zectrix todos ──
        logger.info("  [Phase 4] Import new Zectrix todos...")
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
            uid = f"zectrix-{rid}"

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

        # ── Phase 5: Reverse sync updates to Dida365 ──
        logger.info("  [Phase 5] Reverse sync updates to Dida365...")
        dida_updated = await _reverse_update_to_dida(db, local_linked, remote_map)
        logger.info(f"  [Phase 5] DONE: {dida_updated} tasks updated on Dida365")

        logger.info("  [Phase 6] Reverse sync completions to Dida365...")
        dida_completed = await _reverse_complete_to_dida(db, local_linked, remote_map)
        logger.info(f"  [Phase 6] DONE: {dida_completed} tasks completed on Dida365")

        # ── Phase 7: Create Zectrix-originated tasks on Dida365 ──
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
            await db.rollback()
        except Exception:
            pass
        await add_sync_log("reverse_sync", "failed", str(e), 0)
    finally:
        if close_db:
            await db.close()


async def _reverse_update_to_dida(db, local_linked, remote_map) -> int:
    """Update existing Dida365 tasks when linked Zectrix fields changed."""
    from app.services.dida_client import get_dida_mcp_client

    reverse_mode = await get_config("reverse_sync_mode")
    if reverse_mode != "mcp":
        logger.info(f"    Reverse sync mode is '{reverse_mode}', skipping MCP update")
        return 0

    client = await get_dida_mcp_client()
    if not client:
        logger.info("    Dida MCP not configured, skipping reverse update")
        return 0

    try:
        await client.initialize()
    except Exception as e:
        logger.warning(f"    Dida MCP init failed: {e}")
        return 0

    updated_count = 0
    for row in local_linked:
        rid = row["remote_id"]
        if rid not in remote_map:
            continue

        dida_task_id = row["dida_task_id"] if "dida_task_id" in row.keys() else None
        dida_project_id = row["dida_project_id"] if "dida_project_id" in row.keys() else None
        if not dida_task_id:
            continue

        remote = remote_map[rid]
        zectrix_completed = remote.get("status") == 1 or remote.get("completed") is True
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
    """If a task was completed on Zectrix, also complete it on Dida365 via MCP."""
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

        # Task completed on Zectrix but not yet on Dida365 (only for dida-sourced tasks)
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
    """Create Zectrix-originated tasks on Dida365 that don't yet have a dida_task_id."""
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

    project_id_raw = await get_config("dida_project_id")
    project_ids = [p.strip() for p in project_id_raw.split(",") if p.strip()] if project_id_raw else []
    if not project_ids:
        logger.info("    No Dida365 project selected, skipping reverse creation")
        return 0
    target_project = project_ids[0]

    cursor = await db.execute(
        "SELECT uid, title, description, due_date, due_time, priority, completed, reminders, repeat_flag FROM todos WHERE source = 'zectrix' AND dida_task_id IS NULL"
    )
    zectrix_tasks = await cursor.fetchall()
    logger.info(f"    Found {len(zectrix_tasks)} Zectrix tasks without dida_task_id")

    created_count = 0
    for row in zectrix_tasks:
        title = row["title"]
        if not title:
            continue
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
            # Parse the created task ID from MCP response
            import json
            task_data = json.loads(result) if result.strip().startswith("{") else {}
            dida_task_id = task_data.get("id")
            if dida_task_id:
                await db.execute(
                    "UPDATE todos SET dida_task_id=?, dida_project_id=? WHERE uid=?",
                    (dida_task_id, target_project, row["uid"]),
                )
                await db.commit()
                logger.info(f"    CREATED on Dida365: title={title}, task_id={dida_task_id}")
                # If the task is completed on Zectrix, also complete it on Dida365
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
