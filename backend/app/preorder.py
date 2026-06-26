from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from app import realtime
from app.config import settings
from app.db import SessionLocal
from app.models import Order

log = logging.getLogger("saya.preorder")
_TERMINAL = ("Closed", "Deleted", "Cancelled")


async def release_due(session, now: datetime) -> int:
    lead = settings.preorder_lead_minutes
    rows = (await session.execute(
        select(Order).where(Order.preorder_held == True,  # noqa: E712
                            Order.complete_before != None,  # noqa: E711
                            Order.complete_before <= now + timedelta(minutes=lead),
                            Order.status.notin_(_TERMINAL)))).scalars().all()
    for o in rows:
        o.preorder_held = False
    if rows:
        await session.commit()
    for o in rows:
        await realtime.broadcast_order(o)
    return len(rows)


async def run_releaser() -> None:
    interval = settings.preorder_check_seconds
    if interval <= 0:
        log.info("preorder releaser disabled (interval=%s)", interval)
        return
    log.info("preorder releaser started: interval=%ss", interval)
    while True:
        try:
            async with SessionLocal() as session:
                await release_due(session, datetime.utcnow())
        except Exception:  # noqa: BLE001 — устойчивость
            log.exception("preorder releaser failed")
        await asyncio.sleep(interval)


def start_releaser() -> asyncio.Task:
    return asyncio.create_task(run_releaser())


async def stop_releaser(task) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
