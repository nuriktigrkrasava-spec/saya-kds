from __future__ import annotations
import os
import asyncio
import logging

from app import iiko_cloud, ingest, realtime
from app.db import SessionLocal

log = logging.getLogger("saya.poller")

# org_id -> последняя увиденная ревизия. Курсор живёт в памяти процесса:
# начинаем с 0 (весь хвост), затем двигаем вперёд по maxRevision из ответа.
_revisions: dict[str, int] = {}


def _read_orgs() -> list[str]:
    """Список Cloud org GUID для опроса из POLL_ORG_IDS (через запятую)."""
    raw = os.environ.get("POLL_ORG_IDS", "") or ""
    return [o.strip() for o in raw.split(",") if o.strip()]


def _read_interval() -> int:
    """Интервал опроса в секундах. <=0 (или не число) => поллер ОТКЛЮЧЁН."""
    try:
        return int(os.environ.get("POLL_INTERVAL_SECONDS", "0") or "0")
    except (TypeError, ValueError):
        return 0


async def poll_once(org_id: str) -> None:
    """Один проход by_revision по филиалу: ингест + рассылка изменённых заказов.

    Любая ошибка по филиалу логируется и проглатывается, чтобы один сбой не ронял
    общий цикл и не мешал остальным филиалам.
    """
    try:
        current = _revisions.get(org_id, 0)
        data = await iiko_cloud.fetch_deliveries_by_revision(org_id, current)
        for wrapper in data.get("orders", []) or []:
            normalized = iiko_cloud.normalize_full_order(wrapper, org_id)
            async with SessionLocal() as session:
                order = await ingest.ingest_order(session, normalized)
                await realtime.broadcast_order(order)
        max_rev = data.get("maxRevision")
        if isinstance(max_rev, int):
            _revisions[org_id] = max(max_rev, current)
    except Exception:  # noqa: BLE001 — устойчивость важнее точечной обработки
        log.exception("poller: poll_once failed for org %s", org_id)


async def run_poller() -> None:
    """Фоновый цикл опроса. Отключён по умолчанию (interval<=0 или нет org)."""
    interval = _read_interval()
    orgs = _read_orgs()
    if interval <= 0 or not orgs:
        log.info("poller disabled (interval=%s, orgs=%s)", interval, len(orgs))
        return
    log.info("poller started: interval=%ss, orgs=%s", interval, orgs)
    while True:
        for org in orgs:
            await poll_once(org)
        await asyncio.sleep(interval)


def start_poller() -> asyncio.Task:
    """Запускает run_poller() как фоновую задачу и возвращает её."""
    return asyncio.create_task(run_poller())


async def stop_poller(task: asyncio.Task | None) -> None:
    """Отменяет фоновую задачу поллера и дожидается её завершения."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
