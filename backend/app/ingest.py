from __future__ import annotations
from datetime import datetime, timedelta
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Order, OrderItem
from app.schemas import NormalizedOrder
from app.stations import build_resolver
from app.config import settings


# Зал и вынос приходят из iiko одинаково — Common / «Обычный заказ». Различает
# их только служебная позиция «Вынос», которую кассир добавляет в заказ: на
# проде за четыре дня она стоит в 35 заказах из 132.
TAKEAWAY_MARKER = "вынос"

# Типы заказов, которые в iiko живут как заказы зала (табличные). ToGo — наш
# кассовый вынос: iiko отдаёт его тем же Common, отличаем по позиции «Вынос».
# Список общий, чтобы не разъезжался по файлам: стоит забыть про ToGo в одном
# месте — и такой заказ, например, некому будет закрыть после рестарта.
HALL_SERVICE_TYPES = ("Common", "DineIn", "ToGo")


def is_takeaway(items) -> bool:
    """Заказ с кассы на вынос? Ищем ровно позицию «Вынос», а не слово в названии:
    «Коробка для выноса» — это упаковка, а не признак выноса."""
    return any((getattr(i, "name", "") or "").strip().lower() == TAKEAWAY_MARKER
               for i in items)


def is_held(complete_before, now: datetime, lead_minutes: int) -> bool:
    return complete_before is not None and complete_before > now + timedelta(minutes=lead_minutes)


async def _rms_code_by_employee_id(employee_id: str | None) -> str | None:
    """Код (PIN) сотрудника RMS по его UUID — из кэша сотрудников (~60с).
    Сбой RMS не должен ронять ingest: тогда просто без кода (имя всё равно покажем)."""
    if not employee_id:
        return None
    try:
        from app import rms
        employees, _ = await rms._employees_and_departments_cached()
    except Exception:  # noqa: BLE001
        return None
    for e in employees:
        if e["id"] == employee_id:
            return e["code"] or None
    return None


def _item_key(product_id: str | None, combo_id: str | None,
              name: str | None, comment: str | None) -> tuple:
    """Стабильный ключ позиции для переноса прогресса готовки между вебхуками."""
    return (product_id or "", combo_id or "", name or "", comment or "")


async def ingest_order(session: AsyncSession,
                       data: NormalizedOrder) -> Order | None:
    """Идемпотентно по iiko_order_id: создаёт или обновляет заказ.

    None означает «заводить было нечего» (пустышка без позиций и номера).
    Вызывающие ОБЯЗАНЫ это проверить: 23.08.2026 None ушёл в рассылку, упал на
    order.id и оборвал проход поллера на середине пачки.

    iiko шлёт по одному заказу несколько вебхуков (New -> Bill -> Closed). Раньше
    позиции пересоздавались с нуля на каждый вебхук, и прогресс готовки
    (accepted/ready/checked) терялся — уже готовый заказ «воскресал» на кухне после
    закрытия. Теперь прогресс переносится со старых позиций по ключу.
    """
    resolve = await build_resolver(session)

    existing = (await session.execute(
        select(Order).where(Order.iiko_order_id == data.iiko_order_id))).scalar_one_or_none()

    # Пустышку не заводим: ни позиций, ни номера — показывать нечего, а на
    # планшете такая карточка оседает навсегда, её не закрыть и не убрать. На
    # проде так набралось пять штук за два дня.
    #
    # Проверяем И номер тоже: заказ без позиций, но с номером — нормальный
    # случай (позиции доедут следующим событием), такой заводим.
    if existing is None and not data.items and not (data.number or "").strip():
        return None

    # Снимаем прогресс готовки со старых позиций ДО удаления (плоскими значениями,
    # чтобы не читать атрибуты с уже удалённых ORM-объектов).
    prev: dict[tuple, dict] = {}
    if existing is None:
        order = Order(iiko_order_id=data.iiko_order_id)
        session.add(order)
    else:
        order = existing
        for oi in order.items:
            prev[_item_key(oi.product_id, oi.combo_id, oi.name, oi.comment)] = {
                "is_checked": oi.is_checked,
                "station_accepted_at": oi.station_accepted_at,
                "station_ready_at": oi.station_ready_at,
                "checked_at": oi.checked_at,
                "checked_by": oi.checked_by,
            }
        await session.execute(delete(OrderItem).where(OrderItem.order_id == order.id))

    order.org_id = data.org_id
    order.number = data.number
    order.status = data.status
    order.customer_name = data.customer
    order.phone = data.phone
    order.address = data.address
    order.address_lat = data.lat
    order.address_lon = data.lon
    order.total = data.total
    # Зал или вынос: в iiko оба Common, различает только служебная позиция.
    # Доставку не трогаем — там «Вынос» в составе ничего не значит.
    order.service_type = data.service_type
    if data.service_type in ("Common", "DineIn") and is_takeaway(data.items):
        order.service_type = "ToGo"
    order.comment = data.comment
    order.payment_type_id = data.payment_type_id
    order.payment_type_kind = data.payment_type_kind
    order.payment_type_name = data.payment_type_name
    order.complete_before = data.complete_before
    order.preorder_held = is_held(data.complete_before, datetime.utcnow(),
                                  settings.preorder_lead_minutes)

    # Тайм-штампы жизненного цикла из iiko. Заполняем ТОЛЬКО пустые поля, чтобы
    # ручной прогресс в KDS (нажатия «Забрал»/«Доставлен») имел приоритет, а iiko
    # лишь добивал то, что курьер не отметил — иначе этапы аналитики были бы по нулям.
    if existing is None and data.when_created is not None:
        order.created_at = data.when_created
    if order.assembled_at is None and data.when_cooking_completed is not None:
        order.assembled_at = data.when_cooking_completed
    if order.on_way_at is None and data.when_sended is not None:
        order.on_way_at = data.when_sended
    if order.delivered_at is None and data.when_delivered is not None:
        order.delivered_at = data.when_delivered

    # Курьер, назначенный в iiko (диспетчером/кассиром). Непустой курьер из iiko —
    # источник истины (наши назначения сами пушатся в iiko); пустой — не трогаем
    # назначение сборщика. Код RMS нужен, чтобы заказ появился у курьера в TG.
    if data.courier_name:
        order.assigned_courier_name = data.courier_name
        code = await _rms_code_by_employee_id(data.courier_iiko_id)
        if code:
            order.assigned_courier_code = code

    await session.flush()  # гарантирует order.id

    for it in data.items:
        carry = prev.get(_item_key(it.product_id, it.combo_id, it.name, it.comment), {})
        session.add(OrderItem(
            order_id=order.id,
            product_id=it.product_id,
            name=it.name,
            amount=it.amount,
            combo_id=it.combo_id,
            station=resolve(it),
            kitchen_zone=resolve.raw(it),
            comment=it.comment,
            modifiers=[m.model_dump() for m in it.modifiers],
            is_checked=carry.get("is_checked", False),
            station_accepted_at=carry.get("station_accepted_at"),
            station_ready_at=carry.get("station_ready_at"),
            checked_at=carry.get("checked_at"),
            checked_by=carry.get("checked_by"),
        ))
    await session.commit()
    await session.refresh(order)
    return order
