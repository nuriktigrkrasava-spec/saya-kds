from __future__ import annotations
import os
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app import telegram
from app.db import SessionLocal
from app.models import Order, TelegramLink

log = logging.getLogger("saya.alerts")

# Роли менеджеров: только они получают алерты.
MANAGER_ROLES = {"MN0", "Администратор", "УПРЭКСПО", "ADM", "ADM2"}

# Пороги детекции.
OVERDUE_SECONDS = 10 * 60      # блюдо принято, но не готово >= 10 мин
ANOMALY_SECONDS = 60           # готово быстрее 60с после принятия => подозрительно

# Дедупликация: ключ f"{order_id}:{kind}", чтобы не слать один алерт дважды.
_alerted: set[str] = set()


def _read_interval() -> int:
    """Интервал проверки в секундах. <=0 (или не число) => алерты ОТКЛЮЧЕНЫ."""
    try:
        return int(os.environ.get("ALERTS_INTERVAL_SECONDS", "0") or "0")
    except (TypeError, ValueError):
        return 0


async def _managers_for_order(session, order: Order) -> list[TelegramLink]:
    """TelegramLink менеджеров для org заказа. Если по org никого — пробуем
    совпадение по branch_code (на случай, когда линк привязан к филиалу)."""
    rows = (await session.execute(
        select(TelegramLink).where(
            TelegramLink.org_id == order.org_id,
            TelegramLink.role.in_(MANAGER_ROLES),
        )
    )).scalars().all()
    if not rows and order.org_id:
        rows = (await session.execute(
            select(TelegramLink).where(
                TelegramLink.branch_code == order.org_id,
                TelegramLink.role.in_(MANAGER_ROLES),
            )
        )).scalars().all()
    return list(rows)


async def _notify(session, order: Order, kind: str, text: str) -> None:
    """Шлёт текст всем менеджерам заказа и помечает ключ как отправленный."""
    key = f"{order.id}:{kind}"
    if key in _alerted:
        return
    managers = await _managers_for_order(session, order)
    for link in managers:
        if link.telegram_id:
            await telegram.send_message(link.telegram_id, text)
    _alerted.add(key)


async def check_once() -> None:
    """Один проход: ищем просроченные заказы и аномалии, шлём алерты менеджерам."""
    now = datetime.utcnow()
    async with SessionLocal() as session:
        orders = (await session.execute(
            select(Order).where(
                Order.status != "Closed",
                Order.delivered_at.is_(None),
            )
        )).scalars().all()

        for order in orders:
            try:
                for item in order.items:
                    acc = item.station_accepted_at
                    rdy = item.station_ready_at

                    # OVERDUE: принято, не готово, и висит >= 10 мин.
                    if acc is not None and rdy is None:
                        elapsed = (now - acc).total_seconds()
                        if elapsed >= OVERDUE_SECONDS:
                            mins = int(elapsed // 60)
                            text = (
                                f"⚠️ Заказ №{order.number} просрочен "
                                f"(готовится {mins} мин)"
                            )
                            await _notify(session, order, "overdue", text)

                    # ANOMALY: оба таймстампа есть, готово быстрее 60с.
                    if acc is not None and rdy is not None:
                        secs = (rdy - acc).total_seconds()
                        if secs < ANOMALY_SECONDS:
                            text = (
                                f"🚨 Аномалия: блюдо «{item.name}» готово за "
                                f"{int(secs)}с (подозрительно)"
                            )
                            await _notify(session, order, f"anomaly:{item.id}", text)
            except Exception:  # noqa: BLE001 — один битый заказ не валит цикл
                log.exception("alerts: check failed for order %s", order.id)


async def run_alerts() -> None:
    """Фоновый цикл проверки. Отключён по умолчанию (interval<=0)."""
    interval = _read_interval()
    if interval <= 0:
        log.info("alerts disabled (interval=%s)", interval)
        return
    log.info("alerts started: interval=%ss", interval)
    while True:
        await check_once()
        await asyncio.sleep(interval)


def start_alerts() -> asyncio.Task:
    """Запускает run_alerts() как фоновую задачу и возвращает её."""
    return asyncio.create_task(run_alerts())


async def stop_alerts(task: asyncio.Task | None) -> None:
    """Отменяет фоновую задачу алертов и дожидается завершения."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
