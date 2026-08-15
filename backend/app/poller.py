from __future__ import annotations
import os
import asyncio
import logging

from sqlalchemy import select

from app import iiko_cloud, ingest, realtime
from app.db import SessionLocal
from app.models import Order

log = logging.getLogger("saya.poller")

# org_id -> последняя увиденная ревизия. Курсор живёт в памяти процесса.
# Пока курсора нет, стартовать by_revision не с чего: ревизию 0 iiko отклоняет
# (TOO_OLD_REVISION). Поэтому первый проход догоняет по дате и оттуда берёт курсор.
_revisions: dict[str, int] = {}


def _read_catchup_days() -> int:
    """Глубина догона на первом проходе, суток (POLL_CATCHUP_DAYS, по умолчанию 2)."""
    try:
        return max(int(os.environ.get("POLL_CATCHUP_DAYS", "2") or "2"), 1)
    except (TypeError, ValueError):
        return 2


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
        current = _revisions.get(org_id)
        if current is None:
            # Первый проход после старта: догоняем по дате — заодно подбираем
            # заказы, закрытые в iiko, пока вебхуки терялись.
            data = await iiko_cloud.fetch_deliveries_by_date(
                org_id, days=_read_catchup_days())
        else:
            data = await iiko_cloud.fetch_deliveries_by_revision(org_id, current)
        for wrapper in data.get("orders", []) or []:
            normalized = iiko_cloud.normalize_full_order(wrapper, org_id)
            async with SessionLocal() as session:
                order = await ingest.ingest_order(session, normalized)
                await realtime.broadcast_order(order)
        max_rev = data.get("maxRevision")
        if isinstance(max_rev, int):
            _revisions[org_id] = max(max_rev, current or 0)
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


# ── Поллер ЗАЛА (active pull через Cloud order/by_table) ─────────────────────
# Доставка едет через вебхуки/by_revision выше. Зал в Cloud сам не появляется,
# поэтому тянем его активно: init_by_table (протолкнуть) -> by_table (забрать).
# Справочники (терминалы/столы) кэшируем, исчезнувшие заказы считаем закрытыми.

_hall_cache: dict[str, dict] = {}        # org_id -> {"tgs", "tables", "ttl"}
_hall_active: dict[str, set[str]] = {}   # org_id -> iiko_order_id прошлого прохода
_HALL_CACHE_TTL = 60                     # проходов между обновлениями справочников


def _read_hall_interval() -> int:
    """Интервал опроса зала, сек. <=0 (или не число) => поллер зала ОТКЛЮЧЁН."""
    try:
        return int(os.environ.get("HALL_POLL_INTERVAL_SECONDS", "0") or "0")
    except (TypeError, ValueError):
        return 0


def _read_hall_orgs() -> list[str]:
    """Cloud org GUID для опроса зала: HALL_POLL_ORG_IDS, иначе WEBHOOK_ORG_IDS."""
    raw = (os.environ.get("HALL_POLL_ORG_IDS")
           or os.environ.get("WEBHOOK_ORG_IDS") or "")
    return [o.strip() for o in raw.split(",") if o.strip()]


async def _hall_refs(org_id: str) -> tuple[list[str], list[str]]:
    """Терминальные группы и столы филиала (с кэшированием на _HALL_CACHE_TTL проходов)."""
    cache = _hall_cache.get(org_id)
    if cache and cache["ttl"] > 0:
        cache["ttl"] -= 1
        return cache["tgs"], cache["tables"]
    tgs = await iiko_cloud.fetch_terminal_groups(org_id)
    tables = await iiko_cloud.fetch_sections(tgs)
    _hall_cache[org_id] = {"tgs": tgs, "tables": tables, "ttl": _HALL_CACHE_TTL}
    return tgs, tables


async def _hall_active_from_db(org_id: str) -> set[str]:
    """Открытые заказы зала филиала из БД — стартовое состояние после рестарта.

    _hall_active живёт в памяти, и перезапуск процесса её обнуляет. Без досева
    заказы, открытые до рестарта, никогда не попадают в «пропавшие» — они
    висят на планшете вечно, даже когда касса их давно закрыла.
    """
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Order.iiko_order_id).where(
                Order.org_id == org_id,
                Order.service_type.in_(("Common", "DineIn")),
                Order.status.in_(("New", "Bill"))))).scalars().all()
    return set(rows)


async def _close_vanished(org_id: str, vanished: set[str]) -> None:
    """Заказы, пропавшие из выдачи by_table (закрыты/удалены на кассе) —
    помечаем Closed и рассылаем, чтобы исчезли с экранов."""
    if not vanished:
        return
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Order).where(Order.iiko_order_id.in_(vanished)))).scalars().all()
        changed = [o for o in rows if o.status not in ("Closed", "Deleted")]
        for o in changed:
            o.status = "Closed"
        if changed:
            await session.commit()
            for o in changed:
                await realtime.broadcast_order(o)


async def hall_poll_once(org_id: str) -> None:
    """Один проход по залу: init_by_table -> by_table -> ingest + broadcast.

    Ошибки по филиалу логируются и проглатываются, чтобы не ронять общий цикл.
    """
    try:
        tgs, tables = await _hall_refs(org_id)
        if not tables:
            return
        # Протолкнуть текущее состояние зала из терминалов в Cloud.
        for tg in tgs:
            try:
                await iiko_cloud.order_init_by_table(org_id, tg, tables)
            except Exception:  # noqa: BLE001 — init не критичен для чтения
                log.exception("hall init_by_table failed org=%s tg=%s", org_id, tg)
        # Забрать открытые заказы зала.
        wrappers = await iiko_cloud.order_by_table([org_id], tables, ["New", "Bill"])
        # present — ВСЁ, что iiko вернул, включая пустые столы. seen — то, что
        # мы показали кухне. Различать обязательно: iiko отдаёт только что
        # открытый стол с items: [] и наполняет его позже. Если считать такой
        # заказ отсутствующим, следующий проход снимет его с планшета, хотя
        # касса его не закрывала.
        present: set[str] = set()
        for w in wrappers:
            normalized = iiko_cloud.normalize_table_order(w, org_id)
            if not normalized.iiko_order_id:
                continue
            present.add(normalized.iiko_order_id)
            if not normalized.items:
                continue
            async with SessionLocal() as session:
                order = await ingest.ingest_order(session, normalized)
                await realtime.broadcast_order(order)
        # Закрытие: было в прошлом проходе, пропало сейчас. На первом проходе
        # после старта «прошлый проход» берём из БД (см. _hall_active_from_db).
        previous = _hall_active.get(org_id)
        if previous is None:
            previous = await _hall_active_from_db(org_id)
        await _close_vanished(org_id, previous - present)
        _hall_active[org_id] = present
    except Exception:  # noqa: BLE001 — устойчивость важнее точечной обработки
        log.exception("hall poller: hall_poll_once failed for org %s", org_id)


async def run_hall_poller() -> None:
    """Фоновый цикл опроса зала. Отключён по умолчанию (interval<=0 или нет org)."""
    interval = _read_hall_interval()
    orgs = _read_hall_orgs()
    if interval <= 0 or not orgs:
        log.info("hall poller disabled (interval=%s, orgs=%s)", interval, len(orgs))
        return
    log.info("hall poller started: interval=%ss, orgs=%s", interval, orgs)
    while True:
        for org in orgs:
            await hall_poll_once(org)
        await asyncio.sleep(interval)


def start_poller() -> asyncio.Task:
    """Запускает run_poller() как фоновую задачу и возвращает её."""
    return asyncio.create_task(run_poller())


def start_hall_poller() -> asyncio.Task:
    """Запускает run_hall_poller() как фоновую задачу и возвращает её."""
    return asyncio.create_task(run_hall_poller())


async def stop_poller(task: asyncio.Task | None) -> None:
    """Отменяет фоновую задачу поллера и дожидается её завершения."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
