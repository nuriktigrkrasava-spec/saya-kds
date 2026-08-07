from __future__ import annotations
from fastapi import FastAPI, Depends, Query, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_session
from datetime import datetime as _dt
from sqlalchemy import delete as _sl_delete
from app import iiko_cloud, ingest, realtime
from app import iiko_cloud_write as _iiko_write
from app import analytics_stages as _stages
from app.models import Order, OrderEvent as _OrderEvent
from app.models import StopListItem as _StopList


def _parse_stoplist_entries(event: dict) -> list[dict]:
    """Достаёт записи стоп-листа из StopListUpdate-события максимально защитно.

    Точная форма payload не задокументирована, поэтому ищем список под
    распространёнными ключами в eventInfo, а из каждой записи аккуратно
    вытаскиваем product_id / name / balance, не падая на отсутствующих ключах.
    Возвращает список dict {product_id, name, balance}.
    """
    info = event.get("eventInfo")
    raw = None
    if isinstance(info, list):
        raw = info
    elif isinstance(info, dict):
        for key in ("items", "stopList", "products"):
            val = info.get(key)
            if isinstance(val, list):
                raw = val
                break
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        product = entry.get("product") if isinstance(entry.get("product"), dict) else {}
        product_id = entry.get("productId") or product.get("id") or entry.get("id")
        name = entry.get("name") or product.get("name")
        balance = entry.get("balance")
        # Если имени нет — храним id как имя, чтобы экран кухни что-то показал.
        if name is None and product_id is not None:
            name = str(product_id)
        try:
            balance = float(balance) if balance is not None else None
        except (TypeError, ValueError):
            balance = None
        out.append({
            "product_id": str(product_id) if product_id is not None else None,
            "name": name,
            "balance": balance,
        })
    return out

app = FastAPI(title="Saya KDS backend")

# Локальная разработка: фронт на Vite (5173) ходит на бэк (8000).
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/orders")
async def list_orders(org_id: str, session: AsyncSession = Depends(get_session)):
    """Активные заказы филиала для начальной загрузки экрана (live-обновления идут по Socket.IO)."""
    rows = (await session.execute(
        select(Order).where(
            Order.org_id == org_id,
            Order.status.notin_(["Closed", "Deleted", "Cancelled"]),
            Order.preorder_held == False)  # noqa: E712
        .order_by(Order.created_at))).scalars().all()
    return [realtime.order_payload(o) for o in rows]


@app.post("/webhooks/iiko/cloud")
async def iiko_webhook(events: list[dict], key: str = Query(default=""),
                       session: AsyncSession = Depends(get_session)):
    if settings.webhook_secret and key != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad key")

    import logging as _logging
    _wlog = _logging.getLogger("saya.webhook")
    handled = 0
    for event in events or []:
        _wlog.info("event: type=%s org=%s", event.get("eventType"),
                   event.get("organizationId"))
        if event.get("eventType") == "StopListUpdate":
            org_id = event.get("organizationId")
            if not org_id:
                continue
            entries = _parse_stoplist_entries(event)
            # Стратегия replace-on-update: полностью заменяем строки филиала.
            # Пустой список = в стоп-листе ничего нет -> очищаем филиал.
            await session.execute(
                _sl_delete(_StopList).where(_StopList.org_id == org_id))
            now = _dt.utcnow()
            for e in entries:
                session.add(_StopList(
                    org_id=org_id, product_id=e["product_id"],
                    name=e["name"], balance=e["balance"], updated_at=now))
            await session.commit()
            items = [
                {"product_id": e["product_id"], "name": e["name"],
                 "balance": e["balance"], "updated_at": now.isoformat() + "Z"}
                for e in entries
            ]
            await realtime.broadcast_stoplist(org_id, items)
            handled += 1
            continue
        if event.get("eventType") == "TableOrderUpdate":
            # Заказы зала/выноса. В отличие от доставки, by_id-фетча для них может
            # не быть — ингестим прямо из payload события. Никогда не падаем.
            org_id = event.get("organizationId")
            if not org_id:
                continue
            try:
                normalized = iiko_cloud.normalize_table_order(event, org_id)
            except Exception:
                _wlog.exception("table order normalize failed")
                continue
            _wlog.info("table order: number=%s status=%s items=%d",
                       normalized.number, normalized.status, len(normalized.items))
            if not normalized.items:
                _wlog.warning("table order SKIPPED (no items) number=%s", normalized.number)
                continue
            order = await ingest.ingest_order(session, normalized)
            if not order.preorder_held:
                await realtime.broadcast_order(order)
            handled += 1
            continue
        if event.get("eventType") != "DeliveryOrderUpdate":
            continue
        org_id = event.get("organizationId")
        order_id = (event.get("eventInfo") or {}).get("id")
        if not org_id or not order_id:
            continue
        wrapper = await iiko_cloud.fetch_delivery_by_id(org_id, order_id)
        if not wrapper:
            continue
        normalized = iiko_cloud.normalize_full_order(wrapper, org_id)
        order = await ingest.ingest_order(session, normalized)
        if not order.preorder_held:
            await realtime.broadcast_order(order)
        handled += 1
    return {"ok": True, "handled": handled}


@app.get("/stoplist")
async def get_stoplist(org_id: str, session: AsyncSession = Depends(get_session)):
    """Текущий стоп-лист филиала (что нет в наличии)."""
    rows = (await session.execute(
        select(_StopList).where(_StopList.org_id == org_id)
        .order_by(_StopList.id))).scalars().all()
    return [
        {
            "product_id": r.product_id,
            "name": r.name,
            "balance": r.balance,
            "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None,
        }
        for r in rows
    ]


@app.post("/webhooks/register")
async def webhooks_register(key: str = Query(default="")):
    """Пере-подписать iiko на вебхуки (доставка + зал/вынос). Ручной триггер для
    проверки/восстановления; защищён тем же секретом, что и приём вебхуков."""
    if settings.webhook_secret and key != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad key")
    return await iiko_cloud.register_all_webhooks()


class FrontItemIn(BaseModel):
    name: str = ""
    category: str = ""      # категория блюда (Роллы/Пиццы/Горячее) — для зоны
    amount: float = 1
    comment: str = ""
    product_id: str = ""


class FrontOrderIn(BaseModel):
    id: str                 # id заказа на терминале (для идемпотентности)
    org_id: str             # Cloud org_id филиала (куда слать на планшет)
    number: str = ""
    status: str = "New"
    service_type: str = "DineIn"   # DineIn=зал, ToGo/PickUp=вынос
    customer: str = ""
    phone: str = ""
    comment: str = ""
    items: list[FrontItemIn] = []


@app.post("/ingest/front")
async def ingest_front(body: FrontOrderIn, key: str = Query(default=""),
                       session: AsyncSession = Depends(get_session)):
    """Приём заказов ЗАЛА/ВЫНОСА с терминала iikoFront.

    Заказы зала не уходят в Cloud-вебхуки, поэтому их форвардит плагин с кассы
    прямо сюда. Дальше — тот же конвейер: ingest -> разбор по зонам (по категории)
    -> рассылка на планшет. Идемпотентно по id (front-<id>).
    Защищён тем же секретом, что и вебхуки.
    """
    if settings.webhook_secret and key != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad key")
    from app.schemas import NormalizedOrder as _NO, WebhookItem as _WI
    normalized = _NO(
        iiko_order_id=f"front-{body.id}",
        org_id=body.org_id,
        number=body.number,
        status=body.status,
        customer=body.customer,
        phone=body.phone,
        comment=body.comment,
        service_type=body.service_type,
        items=[_WI(product_id=(it.product_id or f"front-{body.id}-{i}"),
                   category_name=it.category, name=it.name,
                   amount=it.amount, comment=it.comment)
               for i, it in enumerate(body.items)],
    )
    order = await ingest.ingest_order(session, normalized)
    await realtime.broadcast_order(order)
    return realtime.order_payload(order)


@app.get("/debug/orgs")
async def debug_orgs(key: str = Query(default="")):
    """Карта Cloud org_id -> название филиала (из iiko organizations). Для
    сопоставления GUID из логов с филиалами. Защищён тем же секретом."""
    if settings.webhook_secret and key != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad key")
    orgs = await iiko_cloud.fetch_organizations()
    return [{"id": o.get("id"), "name": o.get("name")} for o in orgs]


@app.get("/debug/by_table")
async def debug_by_table(org_id: str, key: str = Query(default="")):
    """Диагностика зала: отдаёт ли филиал dine-in в Cloud (order/by_table).
    Read-only, защищён тем же секретом, что /debug/orgs."""
    if settings.webhook_secret and key != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad key")
    import httpx as _httpx
    try:
        tgs = await iiko_cloud.fetch_terminal_groups(org_id)
        tables = await iiko_cloud.fetch_sections(tgs)
        orders = await iiko_cloud.order_by_table(
            [org_id], tables, iiko_cloud.TABLE_ORDER_STATUSES) if tables else []
    except _httpx.HTTPStatusError as e:
        return {"error": True, "iiko_status": e.response.status_code,
                "iiko_body": e.response.text[:500]}
    out = []
    for w in orders:
        o = w.get("order") or {}
        out.append({
            "id": w.get("id"),
            "creation_status": w.get("creationStatus"),
            "status": o.get("status"),
            "number": o.get("number"),
            "when_created": o.get("whenCreated"),
            "items": len(o.get("items") or []),
            "table": ((o.get("tableIds") or [None])[0]),
        })
    return {"count": len(out), "orders": out}


@app.post("/debug/pull")
async def debug_pull(org_id: str, order_id: str,
                     session: AsyncSession = Depends(get_session)):
    """Локальная отладка без ожидания вебхука: дёрнуть by_id и заингестить."""
    wrapper = await iiko_cloud.fetch_delivery_by_id(org_id, order_id)
    if not wrapper:
        raise HTTPException(status_code=404, detail="order not found")
    normalized = iiko_cloud.normalize_full_order(wrapper, org_id)
    order = await ingest.ingest_order(session, normalized)
    await realtime.broadcast_order(order)
    return realtime.order_payload(order)


from datetime import datetime, timedelta
import jwt  # PyJWT
from pydantic import BaseModel


class LoginIn(BaseModel):
    pin: str


@app.post("/auth/login")
async def login(body: LoginIn):
    from app import rms
    emp = await rms.find_employee_by_pin(body.pin.strip())
    if not emp:
        raise HTTPException(status_code=401, detail="Неверный PIN")
    token = jwt.encode({"name": emp["name"], "role": emp["role"],
                        "branch_code": emp["branch_code"], "branch_name": emp["branch_name"],
                        "org_id": emp["org_id"],
                        "exp": datetime.utcnow() + timedelta(hours=16)},
                       settings.jwt_secret, algorithm="HS256")
    return {"token": token, **emp}


from datetime import datetime as _dt, timedelta as _td
from sqlalchemy import select as _select
from app.models import Order as _Order, OrderItem as _OrderItem
from app.models import DeliveryZone as _DeliveryZone


class ActionIn(BaseModel):
    by: str = ""
    payment_type_id: str | None = None  # UUID типа оплаты из iiko — закрытие доставки
    payment_kind: str | None = None    # "Cash"|"Card" — для тела платежа


async def _load_order(session: AsyncSession, order_id: int) -> _Order:
    order = (await session.execute(_select(_Order).where(_Order.id == order_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return order


async def _item_and_broadcast(session: AsyncSession, item_id: int) -> dict:
    item = (await session.execute(
        _select(_OrderItem).where(_OrderItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    await session.commit()
    order = await _load_order(session, item.order_id)
    await realtime.broadcast_order(order)
    return realtime.order_payload(order)


@app.post("/items/{item_id}/accept")
async def item_accept(item_id: int, body: ActionIn,
                      session: AsyncSession = Depends(get_session)):
    item = (await session.execute(
        _select(_OrderItem).where(_OrderItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    item.station_accepted_at = _dt.utcnow()
    item.checked_by = body.by or item.checked_by
    order = await _load_order(session, item.order_id)
    await _log_action(session, order, "item_accept", actor_name=body.by,
                      item_id=item.id, payload={"name": item.name, "station": item.station})
    return await _item_and_broadcast(session, item_id)


@app.post("/items/{item_id}/ready")
async def item_ready(item_id: int, body: ActionIn,
                     session: AsyncSession = Depends(get_session)):
    item = (await session.execute(
        _select(_OrderItem).where(_OrderItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    item.station_ready_at = _dt.utcnow()
    item.checked_by = body.by or item.checked_by
    # Шага «Принял» больше нет — за «время приготовления» берём от появления заказа,
    # чтобы аналитика/анти-обман работали (иначе accepted_at пустой).
    if item.station_accepted_at is None:
        order = await _load_order(session, item.order_id)
        item.station_accepted_at = order.created_at
    else:
        order = await _load_order(session, item.order_id)
    await _log_action(session, order, "item_ready", actor_name=body.by,
                      item_id=item.id, payload={"name": item.name, "station": item.station})
    return await _item_and_broadcast(session, item_id)


@app.post("/items/{item_id}/check")
async def item_check(item_id: int, body: ActionIn,
                     session: AsyncSession = Depends(get_session)):
    item = (await session.execute(
        _select(_OrderItem).where(_OrderItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    item.is_checked = True
    item.checked_at = _dt.utcnow()
    item.checked_by = body.by or item.checked_by
    order = await _load_order(session, item.order_id)
    await _log_action(session, order, "item_check", actor_name=body.by,
                      item_id=item.id, payload={"name": item.name})
    return await _item_and_broadcast(session, item_id)


@app.post("/items/{item_id}/revert")
async def item_revert(item_id: int, body: ActionIn,
                      session: AsyncSession = Depends(get_session)):
    """Сборщик возвращает позицию назад повару: снимаем «готово» и галочку,
    позиция снова попадает на кухню (таймер с момента «Принял» продолжает идти —
    чтобы сушист не мог накрутить KPI преждевременным «Готово»)."""
    item = (await session.execute(
        _select(_OrderItem).where(_OrderItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    item.station_ready_at = None
    item.is_checked = False
    item.checked_at = None
    order = await _load_order(session, item.order_id)
    await _log_action(session, order, "item_revert", actor_name=body.by,
                      item_id=item.id, payload={"name": item.name})
    return await _item_and_broadcast(session, item_id)


async def _order_action_and_broadcast(session: AsyncSession, order_id: int) -> dict:
    await session.commit()
    order = await _load_order(session, order_id)
    await realtime.broadcast_order(order)
    return realtime.order_payload(order)


async def _log_action(session, order, type: str, *, actor_name: str = "",
                      actor_code: str = "", item_id: int | None = None,
                      payload: dict | None = None) -> None:
    """Событие действия в order_events (журнал «кто что когда» + материал аналитики)."""
    session.add(_OrderEvent(order_id=order.id, org_id=order.org_id, type=type,
                            actor_name=actor_name or None, actor_code=actor_code or None,
                            item_id=item_id, payload=payload or {}))
    await session.commit()


async def _log_iiko_event(session, order, method: str, ok: bool, detail: dict) -> None:
    """Аудит вызова записи в iiko (обёртка над _log_action)."""
    await _log_action(session, order, "iiko_write",
                      payload={"method": method, "ok": ok, "detail": detail})


@app.post("/orders/{order_id}/assemble")
async def order_assemble(order_id: int, body: ActionIn,
                         session: AsyncSession = Depends(get_session)):
    """Сборщик: «передать курьеру»."""
    order = await _load_order(session, order_id)
    order.assembled_at = _dt.utcnow()
    await _log_action(session, order, "order_assemble", actor_name=body.by)
    return await _order_action_and_broadcast(session, order_id)


@app.post("/orders/{order_id}/take")
async def order_take(order_id: int, body: ActionIn,
                     session: AsyncSession = Depends(get_session)):
    """Курьер забрал заказ."""
    order = await _load_order(session, order_id)
    order.on_way_at = _dt.utcnow()
    await _log_action(session, order, "take", actor_name=body.by)
    return await _order_action_and_broadcast(session, order_id)


@app.post("/orders/{order_id}/delivered")
async def order_delivered(order_id: int, body: ActionIn,
                          session: AsyncSession = Depends(get_session)):
    """Курьер доставил заказ. Поток iiko: set_payments → set_delivery_status(Delivered) → close."""
    order = await _load_order(session, order_id)
    order.delivered_at = _dt.utcnow()
    await _log_action(session, order, "delivered", actor_name=body.by)
    pt_id = body.payment_type_id or order.payment_type_id
    if pt_id and order.org_id:
        kind = body.payment_kind or order.payment_type_kind
        item = _iiko_write.payment_item_by_id(order.org_id, pt_id, kind, order.total)
        ok, detail = await _iiko_write.set_payments(order.org_id, order.iiko_order_id, [item])
        if ok:
            ok, detail = await _iiko_write.set_delivery_status(
                order.org_id, order.iiko_order_id, "Delivered")
        if ok:
            ok, detail = await _iiko_write.close_order(order.org_id, order.iiko_order_id)
        order.iiko_sync_error = None if ok else str(detail)[:500]
        await session.commit()
        await _log_iiko_event(session, order, "close", ok, detail)
    return await _order_action_and_broadcast(session, order_id)


from app.models import ProductStationMap as _PSM
from app.schemas import NormalizedOrder as _NormalizedOrder, WebhookItem as _WebhookItem

_SEED_CATEGORIES = {"cat-rolls": "rolls", "cat-pizza": "pizza", "cat-hot": "hot"}

_SEED_ORDERS = [
    _NormalizedOrder(
        iiko_order_id="seed-1001", org_id="", number="1001", status="New",
        customer="Иван", phone="+7 701 111 11 11",
        address="Астана, пр. Кабанбай батыра 1",
        lat=51.16, lon=71.47, total=8500,
        items=[
            _WebhookItem(product_id="seed-fila", category_id="cat-rolls",
                         name="Филадельфия", amount=2),
            _WebhookItem(product_id="seed-marg", category_id="cat-pizza",
                         name="Пицца Маргарита", amount=1),
        ],
    ),
    _NormalizedOrder(
        iiko_order_id="seed-1002", org_id="", number="1002", status="New",
        customer="Айгерим", phone="+7 701 222 22 22",
        address="Астана, ул. Сыганак 18",
        lat=51.165, lon=71.475, total=6200,
        items=[
            _WebhookItem(product_id="seed-cali", category_id="cat-rolls",
                         name="Калифорния", amount=1),
            _WebhookItem(product_id="seed-tomyam", category_id="cat-hot",
                         name="Том Ям", amount=1),
        ],
    ),
    _NormalizedOrder(
        iiko_order_id="seed-1003", org_id="", number="1003", status="New",
        customer="Данияр", phone="+7 701 333 33 33",
        address="Астана, пр. Туран 37",
        lat=51.155, lon=71.465, total=9100,
        items=[
            _WebhookItem(product_id="seed-pep", category_id="cat-pizza",
                         name="Пепперони", amount=1),
            _WebhookItem(product_id="seed-dragon", category_id="cat-rolls",
                         name="Дракон ролл", amount=3),
        ],
    ),
]


# Завершённые «исторические» заказы — отдельно от 3 активных, чтобы аналитика
# (этапы Очередь→Доставка, SLA, доставка, сотрудники, зоны спроса) сразу была не
# по нулям. Координаты разнесены по Астане, время — за последние ~2 дня.
# Поля смещений — минуты от создания заказа. d/h/m складываются в created_ago.
_SEED_DONE = [
    {"id": "done-1", "num": "9001", "lat": 51.10, "lon": 71.30,
     "cook": "Ермек", "courier": "Бауыржан", "ago_min": 90,
     "accept": 2, "ready": 12, "assembled": 15, "on_way": 19, "delivered": 35,
     "items": [("Филадельфия", "cat-rolls", 2), ("Том Ям", "cat-hot", 1)]},
    {"id": "done-2", "num": "9002", "lat": 51.27, "lon": 71.62,
     "cook": "Сауле", "courier": "Нурлан", "ago_min": 210,
     "accept": 1, "ready": 19, "assembled": 23, "on_way": 26, "delivered": 52,
     "items": [("Пепперони", "cat-pizza", 1), ("Калифорния", "cat-rolls", 1)]},
    {"id": "done-3", "num": "9003", "lat": 51.18, "lon": 71.55,
     "cook": "Дамир", "courier": "Аскар", "ago_min": 350,
     "accept": 3, "ready": 10, "assembled": 13, "on_way": 15, "delivered": 28,
     "items": [("Маргарита", "cat-pizza", 1), ("Дракон ролл", "cat-rolls", 2)]},
    {"id": "done-4", "num": "9004", "lat": 51.06, "lon": 71.60,
     "cook": "Ермек", "courier": "Нурлан", "ago_min": 1500,
     "accept": 2, "ready": 14, "assembled": 17, "on_way": 28, "delivered": 60,
     "items": [("Том Ям", "cat-hot", 2)]},
    {"id": "done-5", "num": "9005", "lat": 51.24, "lon": 71.28,
     "cook": "Сауле", "courier": "Бауыржан", "ago_min": 1650,
     "accept": 1, "ready": 9, "assembled": 11, "on_way": 13, "delivered": 24,
     "items": [("Калифорния", "cat-rolls", 3)]},
    {"id": "done-6", "num": "9006", "lat": 51.15, "lon": 71.40,
     "cook": "Дамир", "courier": "Аскар", "ago_min": 2880,
     "accept": 4, "ready": 16, "assembled": 20, "on_way": 22, "delivered": 41,
     "items": [("Пепперони", "cat-pizza", 1), ("Том Ям", "cat-hot", 1)]},
]


async def _seed_done_order(session, org_id: str, spec: dict, now):
    """Создаёт один завершённый исторический заказ с полным жизненным циклом."""
    data = _NormalizedOrder(
        iiko_order_id=spec["id"], org_id=org_id, number=spec["num"], status="Closed",
        customer="Демо", address="Астана", lat=spec["lat"], lon=spec["lon"],
        items=[_WebhookItem(product_id=f'{spec["id"]}-{i}', category_id=cat,
                            name=name, amount=amt)
               for i, (name, cat, amt) in enumerate(spec["items"])],
    )
    order = await ingest.ingest_order(session, data)
    base = now - _td(minutes=spec["ago_min"])
    order.created_at = base
    for it in order.items:
        it.station_accepted_at = base + _td(minutes=spec["accept"])
        it.station_ready_at = base + _td(minutes=spec["ready"])
        it.checked_by = spec["cook"]
        it.checked_at = it.station_ready_at
        it.is_checked = True
    order.assembled_at = base + _td(minutes=spec["assembled"])
    order.on_way_at = base + _td(minutes=spec["on_way"])
    order.delivered_at = base + _td(minutes=spec["delivered"])
    order.assigned_courier_name = spec["courier"]
    await session.commit()


@app.post("/debug/seed")
async def debug_seed(org_id: str = "demo",
                     session: AsyncSession = Depends(get_session)):
    """Локальный просмотр: демо-маппинг категорий + 3 активных заказа на доске +
    6 завершённых исторических заказов (наполняют аналитику и карту спроса)."""
    existing = (await session.execute(_select(_PSM))).scalars().all()
    have = {(r.product_id, r.category_id) for r in existing}
    for category_id, station in _SEED_CATEGORIES.items():
        if (None, category_id) not in have:
            session.add(_PSM(product_id=None, category_id=category_id, station=station))
    await session.commit()

    # 3 активных заказа на доске (как раньше) — их и возвращаем.
    payloads = []
    for tmpl in _SEED_ORDERS:
        data = tmpl.model_copy(update={"org_id": org_id})
        order = await ingest.ingest_order(session, data)
        await realtime.broadcast_order(order)
        payloads.append(realtime.order_payload(order))

    # Завершённые исторические — для аналитики/зон (на доску не шлём).
    now = _dt.utcnow()
    for spec in _SEED_DONE:
        await _seed_done_order(session, org_id, spec, now)

    return payloads


def _prep_seconds(item) -> float | None:
    """Время готовки позиции в секундах, если оба тайм-штампа проставлены."""
    if item.station_accepted_at and item.station_ready_at:
        return (item.station_ready_at - item.station_accepted_at).total_seconds()
    return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _parse_period(from_: str | None, to_: str | None):
    """ISO YYYY-MM-DD -> (dt_from, dt_to) в UTC, но ГРАНИЦЫ суток — по ЛОКАЛЬНОМУ
    дню (iiko_tz_offset_hours), как и почасовые тепловые карты/SLA. created_at
    хранится в UTC, поэтому локальную полночь переводим в UTC вычитанием оффсета.
    Раньше границы были по UTC-дате и расходились с локальным почасовым биннингом.
    Дефолт: последние 7 локальных дней."""
    off = _td(hours=settings.iiko_tz_offset_hours)
    today = (_dt.utcnow() + off).date()
    d_to = _dt.fromisoformat(to_).date() if to_ else today
    d_from = _dt.fromisoformat(from_).date() if from_ else (d_to - _td(days=6))
    # локальная полночь d_from .. (d_to+1), переведённая в UTC
    dt_from = _dt(d_from.year, d_from.month, d_from.day) - off
    dt_to = _dt(d_to.year, d_to.month, d_to.day) + _td(days=1) - off  # эксклюзивная верхняя
    return dt_from, dt_to


async def _orders_in_period(session, org_id: str, dt_from, dt_to):
    """Когорта аналитики: заказы филиала, созданные в периоде. Отменённые и
    удалённые в iiko — не заказы, в цифры не входят."""
    rows = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id,
                              _Order.status.notin_(["Cancelled", "Deleted"]),
                              _Order.created_at >= dt_from,
                              _Order.created_at < dt_to))).scalars().all()
    return rows


def _is_done(order) -> bool:
    """Заказ завершён: курьер отметил «Доставлен» (delivered_at) ЛИБО заказ закрыт
    в iiko (самовывоз / курьер забыл нажать). Иначе Closed-заказы вечно висели бы
    «в работе»."""
    return order.delivered_at is not None or order.status == "Closed"


def _local_today():
    """Сегодняшняя дата в ЛОКАЛЬНОМ времени филиала (iiko_tz_offset_hours)."""
    return (_dt.utcnow() + _td(hours=settings.iiko_tz_offset_hours)).date()


def _local_date(dt):
    """Локальная дата UTC-таймстемпа (created_at/ready_at и пр. хранятся в UTC)."""
    return (dt + _td(hours=settings.iiko_tz_offset_hours)).date()


async def _org_branch_codes(session, org_id: str) -> set[str] | None:
    """Branch-коды (RMS) филиала org_id — для фильтрации данных без org_id
    (курьерские локации). Приоритет: явная настройка settings.branch_code; иначе
    коды из telegram_links этого org (линк хранит и org_id, и branch_code); иначе
    единственный код в courier_locations (однофилиальный случай). None = определить
    не удалось — не фильтруем, чтобы не спрятать данные."""
    if settings.branch_code:
        return {settings.branch_code}
    from app.models import TelegramLink as _TL
    codes = {c for c in (await session.execute(
        _select(_TL.branch_code).where(_TL.org_id == org_id)
        .distinct())).scalars().all() if c}
    if codes:
        return codes
    single = [c for c in (await session.execute(
        _select(_CourierLocation.branch_code).distinct())).scalars().all() if c]
    return {single[0]} if len(single) == 1 else None


@app.get("/admin/stage-stats")
async def admin_stage_stats(org_id: str, from_: str | None = Query(None, alias="from"),
                            to: str | None = None, sla_minutes: int = 45,
                            session: AsyncSession = Depends(get_session)):
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    agg = _stages.stage_aggregates(orders)
    return {
        "stage_aggregates": {"stages": agg["stages"]},
        "bottleneck_distribution": agg["bottleneck_distribution"],
        "sla": _stages.sla_breaches(orders, sla_minutes * 60, tz_offset_hours=settings.iiko_tz_offset_hours),
        "pickup_lag": _stages.pickup_lag(orders),
    }


@app.get("/admin/kitchen-zones")
async def admin_kitchen_zones(org_id: str, from_: str | None = Query(None, alias="from"),
                              to: str | None = None,
                              session: AsyncSession = Depends(get_session)):
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    return _stages.kitchen_zones(orders)


@app.get("/admin/zone-load")
async def admin_zone_load(org_id: str, from_: str | None = Query(None, alias="from"),
                          to: str | None = None,
                          session: AsyncSession = Depends(get_session)):
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    return _stages.zone_load(orders, tz_offset_hours=settings.iiko_tz_offset_hours)


@app.get("/admin/courier-zone-load")
async def admin_courier_zone_load(org_id: str, from_: str | None = Query(None, alias="from"),
                                  to: str | None = None,
                                  session: AsyncSession = Depends(get_session)):
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    zrows = (await session.execute(
        _select(_DeliveryZone).where(_DeliveryZone.org_id == org_id))).scalars().all()
    zones = [{"id": z.id, "name": z.name, "color": z.color, "polygon": z.polygon or []}
             for z in zrows]
    return _stages.courier_zone_load(orders, zones,
                                     tz_offset_hours=settings.iiko_tz_offset_hours)


def _close_at(events) -> "datetime | None":
    for e in events:
        if e.type == "iiko_write" and (e.payload or {}).get("method") == "close" \
                and (e.payload or {}).get("ok"):
            return e.at
    return None


@app.get("/admin/orders/{order_id}/timeline")
async def admin_order_timeline(order_id: int, org_id: str,
                               session: AsyncSession = Depends(get_session)):
    order = await _load_order(session, order_id)
    if order.org_id != org_id:  # изоляция: чужой филиал заказ не видит
        raise HTTPException(status_code=404, detail="order not found")
    events = (await session.execute(
        _select(_OrderEvent).where(_OrderEvent.order_id == order_id)
        .order_by(_OrderEvent.at))).scalars().all()
    stages = _stages.order_stages(order, _close_at(events))
    # «настоящие» lifecycle-события (исключая iiko_write) или синтез
    real = [e for e in events if e.type != "iiko_write"]
    if real:
        ev_out = [{"at": _iso(e.at), "type": e.type, "actor_name": e.actor_name or "",
                   "item_id": e.item_id, "payload": e.payload or {}} for e in real]
    else:
        ev_out = [{"at": _iso(e["at"]), "type": e["type"], "actor_name": e["actor_name"],
                   "item_id": e["item_id"], "payload": {"item_name": e["item_name"]}}
                  for e in _stages.synth_events(order)]
    return {"order": {"id": order.id, "number": order.number, "org_id": order.org_id},
            "stages": stages, "events": ev_out}


def _summary_metrics(orders) -> dict:
    """Метрики для карточек по набору заказов периода. delivered = завершённые
    (_is_done). Среднее время доставки — только заказы с обеими отметками
    («Забрал» on_way_at и «Доставлен» delivered_at); avg_delivery_orders говорит,
    по скольким заказам оно посчитано (остальные выпали — не молчим об этом)."""
    delivered = sum(1 for o in orders if _is_done(o))
    secs = [s for o in orders if (s := _stages.delivery_seconds(o)) is not None]
    return {"orders": len(orders), "delivered": delivered,
            "avg_delivery_seconds": _mean(secs),
            "avg_delivery_orders": len(secs)}


@app.get("/admin/summary")
async def admin_summary(org_id: str, from_: str | None = Query(None, alias="from"),
                        to: str | None = None,
                        session: AsyncSession = Depends(get_session)):
    """Карточки за ВЫБРАННЫЙ период + сравнение с предыдущим равным периодом
    (чтобы видеть рост/падение). Сходящаяся когорта «создано за период»:
    Заказов = Доставлено + in_progress (точно сходится). in_progress — заказы
    периода, ещё не доставленные (не живой остаток за всё время). Период по
    created_at с границами по локальному дню (см. _parse_period)."""
    dt_from, dt_to = _parse_period(from_, to)
    span = dt_to - dt_from
    cur_orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    prev_orders = await _orders_in_period(session, org_id, dt_from - span, dt_from)
    in_progress = sum(1 for o in cur_orders if not _is_done(o))
    return {
        "period": {"from": _iso(dt_from), "to": _iso(dt_to)},
        "current": _summary_metrics(cur_orders),
        "previous": _summary_metrics(prev_orders),
        "in_progress": in_progress,
    }


@app.get("/admin/analytics")
async def admin_analytics(org_id: str, from_: str | None = Query(None, alias="from"),
                          to: str | None = None,
                          session: AsyncSession = Depends(get_session)):
    """Сводная аналитика по филиалу ЗА ВЫБРАННЫЙ ПЕРИОД (по created_at, как и
    карточки/этапы/зоны): заказы, сотрудники, зоны, доставка. Раньше блок считался
    за всё время и не двигался при смене дат — цифры не стыковались с карточками."""
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)

    total_orders = len(orders)
    delivered_orders = sum(1 for o in orders if _is_done(o))
    in_progress = total_orders - delivered_orders

    # Сотрудники: группируем позиции по checked_by.
    emp_done: dict[str, int] = {}
    emp_preps: dict[str, list[float]] = {}
    # Зоны: группируем позиции по station.
    st_total: dict[str, int] = {}
    st_ready: dict[str, int] = {}
    st_preps: dict[str, list[float]] = {}

    for o in orders:
        for it in o.items:
            station = it.station or "other"
            st_total[station] = st_total.get(station, 0) + 1
            if it.station_ready_at is not None:
                st_ready[station] = st_ready.get(station, 0) + 1
            prep = _prep_seconds(it)
            if prep is not None:
                st_preps.setdefault(station, []).append(prep)

            name = (it.checked_by or "").strip()
            if name:
                if it.station_ready_at is not None:
                    emp_done[name] = emp_done.get(name, 0) + 1
                else:
                    emp_done.setdefault(name, 0)
                if prep is not None:
                    emp_preps.setdefault(name, []).append(prep)

    employees = [
        {
            "name": name,
            "items_done": emp_done.get(name, 0),
            "avg_prep_seconds": _mean(emp_preps.get(name, [])),
        }
        for name in sorted(emp_done.keys())
    ]

    stations = [
        {
            "station": station,
            "items_total": st_total.get(station, 0),
            "items_ready": st_ready.get(station, 0),
            "avg_prep_seconds": _mean(st_preps.get(station, [])),
        }
        for station in sorted(st_total.keys())
    ]

    # Доставка: только реальные измерения (обе отметки, без артефакта закрытия).
    delivery_secs = [
        s for o in orders if (s := _stages.delivery_seconds(o)) is not None
    ]

    return {
        "org_id": org_id,
        "totals": {
            "orders": total_orders,
            "delivered": delivered_orders,
            "in_progress": in_progress,
        },
        "employees": employees,
        "stations": stations,
        "delivery": {
            "delivered_count": len(delivery_secs),
            "avg_delivery_seconds": _mean(delivery_secs),
        },
    }


# ──────────────────────────────────────────────────────────────────────────
# История сотрудников за сегодня + детектор аномалий по времени (анти-фрод).
# «Готово» менее чем за минуту после «принял» — физически невозможно.
# ──────────────────────────────────────────────────────────────────────────
_SUSPICIOUS_PREP_SECONDS = 60  # порог: меньше минуты = подозрительно


def _worked_items_today(orders: list[_Order]) -> list[tuple[str, _Order, "_OrderItem"]]:
    """Позиции, принятые сегодня (по ЛОКАЛЬНОМУ дню филиала), с непустым исполнителем.

    Возвращает кортежи (имя_исполнителя, заказ, позиция).
    """
    today = _local_today()
    out: list[tuple[str, _Order, _OrderItem]] = []
    for o in orders:
        for it in o.items:
            if it.station_accepted_at is None:
                continue
            if _local_date(it.station_accepted_at) != today:
                continue
            name = (it.checked_by or "").strip()
            if not name:
                continue
            out.append((name, o, it))
    return out


@app.get("/admin/employee-history")
async def admin_employee_history(org_id: str,
                                 session: AsyncSession = Depends(get_session)):
    """Что каждый сотрудник делал сегодня: позиции, состав, времена + подозрения.

    Группируем сегодняшние принятые позиции филиала по checked_by (исполнителю).
    suspicious = время готовки задано И меньше минуты (физически невозможно).
    """
    orders = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id))).scalars().all()

    by_emp: dict[str, list[dict]] = {}
    preps: dict[str, list[float]] = {}
    suspicious: dict[str, int] = {}

    for name, order, it in _worked_items_today(orders):
        prep = _prep_seconds(it)
        is_susp = prep is not None and prep < _SUSPICIOUS_PREP_SECONDS
        by_emp.setdefault(name, []).append({
            "order_number": order.number,
            "item_name": it.name,
            "station": it.station,
            "accepted_at": _iso(it.station_accepted_at),
            "ready_at": _iso(it.station_ready_at),
            "prep_seconds": int(prep) if prep is not None else None,
            "suspicious": is_susp,
        })
        if prep is not None:
            preps.setdefault(name, []).append(prep)
        if is_susp:
            suspicious[name] = suspicious.get(name, 0) + 1

    return [
        {
            "name": name,
            "items": by_emp[name],
            "count": len(by_emp[name]),
            "avg_prep_seconds": _mean(preps.get(name, [])),
            "suspicious_count": suspicious.get(name, 0),
        }
        for name in sorted(by_emp.keys())
    ]


@app.get("/admin/anomalies")
async def admin_anomalies(org_id: str,
                          session: AsyncSession = Depends(get_session)):
    """Быстрые аномалии + возвраты (item_revert ≥2) за сегодня.

    «Готово» менее чем за минуту после «принял» — сортировка по времени готовки.
    """
    orders = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id))).scalars().all()

    fast: list[dict] = []
    for name, order, it in _worked_items_today(orders):
        prep = _prep_seconds(it)
        if prep is None or prep >= _SUSPICIOUS_PREP_SECONDS:
            continue
        fast.append({
            "name": name,
            "order_number": order.number,
            "item_name": it.name,
            "prep_seconds": int(prep),
            "accepted_at": _iso(it.station_accepted_at),
            "ready_at": _iso(it.station_ready_at),
        })

    fast.sort(key=lambda a: a["prep_seconds"])

    # Возвраты: считаем события item_revert по заказам филиала
    revert_rows = (await session.execute(
        _select(_OrderEvent.order_id).where(
            _OrderEvent.org_id == org_id,
            _OrderEvent.type == "item_revert"))).scalars().all()
    revert_counts: dict[int, int] = {}
    for oid in revert_rows:
        revert_counts[oid] = revert_counts.get(oid, 0) + 1
    extra = _stages.anomalies(orders, revert_counts)
    reverts = [
        {"order_number": a["number"], "count": revert_counts.get(a["order_id"], 0)}
        for a in extra if a["kind"] == "reverts"
    ]

    return {"fast": fast, "reverts": reverts}


@app.get("/admin/journal")
async def admin_journal(org_id: str, from_: str | None = Query(None, alias="from"),
                        to: str | None = None, actor: str | None = None,
                        type: str | None = None, order_number: str | None = None,
                        limit: int = 100, offset: int = 0,
                        session: AsyncSession = Depends(get_session)):
    """Журнал событий по заказам периода (реальные + синтезированные).

    Исключает iiko_write (технический аудит, не lifecycle).
    """
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    by_id = {o.id: o for o in orders}

    have_events: set[int] = set()
    rows: list[dict] = []
    if by_id:
        evs = (await session.execute(
            _select(_OrderEvent).where(
                _OrderEvent.order_id.in_(list(by_id)),
                _OrderEvent.type != "iiko_write"))).scalars().all()
        for e in evs:
            have_events.add(e.order_id)
            rows.append({
                "at": e.at,
                "order_id": e.order_id,
                "order_number": by_id[e.order_id].number,
                "type": e.type,
                "actor_name": e.actor_name or "",
                "item_name": (e.payload or {}).get("name"),
                "detail": (e.payload or {}).get("station", ""),
            })

    # Синтез для заказов периода без реальных событий
    for o in orders:
        if o.id in have_events:
            continue
        for se in _stages.synth_events(o):
            rows.append({
                "at": se["at"],
                "order_id": o.id,
                "order_number": o.number,
                "type": se["type"],
                "actor_name": se["actor_name"],
                "item_name": se["item_name"],
                "detail": "",
            })

    # Фильтры
    if actor:
        rows = [r for r in rows if actor.lower() in (r["actor_name"] or "").lower()]
    if type:
        rows = [r for r in rows if r["type"] == type]
    if order_number:
        rows = [r for r in rows if r["order_number"] == order_number]

    rows.sort(key=lambda r: r["at"], reverse=True)
    total = len(rows)
    page = rows[offset:offset + limit]
    return {"total": total, "rows": [{**r, "at": _iso(r["at"])} for r in page]}


@app.get("/payment-types")
async def payment_types(org_id: str):
    return await _iiko_write.list_payment_types(org_id)


@app.get("/rms/branches")
async def rms_branches():
    """Филиалы и сотрудники из RMS (живой вызов: логин/логаут на стороне rms)."""
    from app import rms
    return await rms.branches_with_employees()


# ──────────────────────────────────────────────────────────────────────────
# Разметка зон (станций) для блюд по группам меню iiko.
# ──────────────────────────────────────────────────────────────────────────
from app.stations import build_resolver as _build_resolver, classify_group as _classify_group, classify_name as _classify_name, classify_product as _classify_product, VALID_STATIONS as _VALID_STATIONS


def _group_id_to_name(nomenclature: dict) -> dict[str, str]:
    """groupId -> чистое имя группы меню iiko."""
    return {
        str(g.get("id")): (g.get("name") or "")
        for g in (nomenclature.get("groups") or [])
        if g.get("id")
    }


def _products_with_group(nomenclature: dict) -> list[dict]:
    """Список продуктов с присоединённым именем группы меню."""
    g2n = _group_id_to_name(nomenclature)
    out: list[dict] = []
    for p in (nomenclature.get("products") or []):
        pid = p.get("id")
        if not pid:
            continue
        group_id = str(p.get("groupId")) if p.get("groupId") else None
        out.append({
            "product_id": str(pid),
            "name": p.get("name", "") or "",
            "group_id": group_id,
            "group_name": g2n.get(group_id, "") if group_id else "",
        })
    return out


async def _reresolve_active_orders(session: AsyncSession, org_id: str) -> int:
    """Пересчитать station у позиций активных заказов филиала и разослать обновления.

    Возвращает число изменённых позиций. Вызывать ПОСЛЕ commit изменений маппинга,
    чтобы build_resolver увидел свежие правила.
    """
    resolve = await _build_resolver(session)
    orders = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id, _Order.status != "Closed"))
    ).scalars().all()

    changed = 0
    touched: list[_Order] = []
    for order in orders:
        order_changed = False
        for it in order.items:
            new_station = resolve(_WebhookItem(
                product_id=it.product_id or "",
                category_id=None,
                name=it.name or "",
                amount=it.amount,
                combo_id=it.combo_id,
            ))
            if new_station != it.station:
                it.station = new_station
                changed += 1
                order_changed = True
        if order_changed:
            touched.append(order)

    if changed:
        await session.commit()
        for order in touched:
            await session.refresh(order)
    for order in touched:
        await realtime.broadcast_order(order)
    return changed


async def _upsert_station(session: AsyncSession, product_id: str, station: str) -> None:
    """UPSERT строки маппинга по product_id (category_id=None)."""
    row = (await session.execute(
        _select(_PSM).where(_PSM.product_id == product_id, _PSM.category_id.is_(None)))
    ).scalar_one_or_none()
    if row is None:
        session.add(_PSM(product_id=product_id, category_id=None, station=station))
    else:
        row.station = station


@app.post("/admin/stations/auto")
async def admin_stations_auto(org_id: str, overwrite: bool = False,
                              session: AsyncSession = Depends(get_session)):
    """Авторазметка: тянем ПОЛНОЕ меню из RMS, классифицируем блюда по названию.
    По умолчанию НЕ затираем уже размеченные блюда (ручные правки сохраняются);
    overwrite=true — перезаписать всё.
    """
    from app import rms
    products = await rms.fetch_products()

    # Уже размеченные product_id (ручные/прошлые) — их не трогаем, если не overwrite.
    existing = {r.product_id for r in (await session.execute(
        _select(_PSM).where(_PSM.product_id.is_not(None)))).scalars().all()}

    mapped = 0
    for p in products:
        pid = p["id"]
        if not overwrite and pid in existing:
            continue  # сохраняем ручную разметку
        zone = _classify_product(p["name"], p.get("group_path"), p.get("group_root"))
        await _upsert_station(session, pid, zone)
        mapped += 1
    await session.commit()

    reclassified = await _reresolve_active_orders(session, org_id)
    return {"mapped_products": mapped, "skipped_existing": len(existing) if not overwrite else 0,
            "reclassified_items": reclassified}


@app.get("/admin/stations")
async def admin_stations_get(org_id: str,
                             session: AsyncSession = Depends(get_session)):
    """Текущая разметка для админ-UI: ПОЛНОЕ меню из RMS, сгруппированное по
    первой букве названия, с текущей зоной (маппинг по product_id, иначе
    override по названию, иначе классификация по названию)."""
    from app import rms
    from app.stations import NAME_PREFIX as _NP, norm_name as _norm
    products = await rms.fetch_products()

    rows = (await session.execute(_select(_PSM))).scalars().all()
    by_product = {r.product_id: r.station for r in rows
                  if r.product_id and not r.product_id.startswith(_NP)}
    by_name = {r.product_id[len(_NP):]: r.station for r in rows
               if r.product_id and r.product_id.startswith(_NP)}

    def _station_for(p: dict) -> str:
        pid, name = p["id"], p["name"]
        # ручная разметка по названию — наивысший приоритет (см. build_resolver)
        nm = _norm(name)
        if nm in by_name:
            return by_name[nm]
        if pid in by_product:
            return by_product[pid]
        return _classify_product(name, p.get("group_path"), p.get("group_root"))

    groups: dict[str, list[dict]] = {}
    for p in products:
        name = p["name"]
        letter = name[:1].upper() if name else "#"
        groups.setdefault(letter, []).append({
            "product_id": p["id"],
            "name": name,
            "station": _station_for(p),
        })

    out = []
    for letter in sorted(groups):
        items = sorted(groups[letter], key=lambda x: x["name"].lower())
        out.append({"group": letter, "products": items})
    return out


class _StationItemIn(BaseModel):
    product_id: str
    station: str
    name: str = ""


class _StationsIn(BaseModel):
    items: list[_StationItemIn] = []


@app.post("/admin/stations")
async def admin_stations_post(org_id: str, body: _StationsIn,
                              session: AsyncSession = Depends(get_session)):
    """Ручные правки разметки: UPSERT по product_id И по названию (надёжнее, т.к. в
    заказе product_id может отличаться от меню), затем пересчёт активных заказов."""
    from app.stations import NAME_PREFIX as _NP, norm_name as _norm
    updated = 0
    for item in body.items:
        if item.station not in _VALID_STATIONS:
            raise HTTPException(status_code=400, detail=f"bad station: {item.station}")
        if not item.product_id:
            continue
        await _upsert_station(session, item.product_id, item.station)
        if item.name:  # дублируем как override по названию
            await _upsert_station(session, _NP + _norm(item.name), item.station)
        updated += 1
    await session.commit()

    await _reresolve_active_orders(session, org_id)
    return {"updated": updated}


# ──────────────────────────────────────────────────────────────────────────
# Курьеры: назначение заказа сборщиком и Telegram Mini App.
# ──────────────────────────────────────────────────────────────────────────
from app import rms as _rms, telegram as _telegram
from app.models import TelegramLink as _TelegramLink

_COURIER_ROLES = {"КР", "КЭКСПО20"}


# «На смене» = у курьера есть свежая геопозиция из Telegram-аппа за это окно.
_ACTIVE_WINDOW_MIN = 30


@app.get("/branches/couriers")
async def branches_couriers(branch_code: str = "",
                            session: AsyncSession = Depends(get_session)):
    """Курьеры конкретного филиала RMS (роли КР/КЭКСПО20).

    «На смене» (active) = открытая явка iiko, начатая сегодня, ИЛИ свежая
    геопозиция из Telegram-аппа (за _ACTIVE_WINDOW_MIN минут). Явка — главный
    признак: курьер виден сборщику, даже если не открывал апп; гео — фолбэк,
    если RMS недоступен. Активные идут первыми, далее по имени.
    """
    if not branch_code:
        return []

    # Открытые сегодня явки курьеров этого филиала. Сбой RMS не критичен —
    # остаётся гео-признак.
    shift_codes: set[str] = set()
    try:
        shift = await _rms.shift_employees_today()
        shift_codes = {e["code"] for e in shift
                       if e["role"] in _COURIER_ROLES
                       and e["branch_code"] == branch_code and e["code"]}
    except Exception:  # noqa: BLE001
        pass
    branches = await _rms.branches_with_employees()
    branch = next((b for b in branches if b["code"] == branch_code), None)
    if not branch:
        return []

    couriers = [
        {"code": e["code"], "name": e["name"]}
        for e in branch["employees"]
        if e.get("role") in _COURIER_ROLES
    ]
    if not couriers:
        return []

    codes = [c["code"] for c in couriers]
    locs = (await session.execute(
        _select(_CourierLocation).where(_CourierLocation.courier_code.in_(codes)))
    ).scalars().all()
    by_code = {loc.courier_code: loc for loc in locs}

    now = _dt.utcnow()
    out = []
    for c in couriers:
        loc = by_code.get(c["code"])
        minutes_ago = None
        geo_fresh = False
        if loc is not None and loc.updated_at is not None:
            minutes_ago = int((now - loc.updated_at).total_seconds() // 60)
            geo_fresh = minutes_ago < _ACTIVE_WINDOW_MIN
        out.append({"code": c["code"], "name": c["name"],
                    "active": c["code"] in shift_codes or geo_fresh,
                    "on_shift": c["code"] in shift_codes,
                    "minutes_ago": minutes_ago})

    # Активные первыми, затем по имени.
    out.sort(key=lambda c: (not c["active"], c["name"]))
    return out


class AssignCourierIn(BaseModel):
    courier_code: str
    courier_name: str
    by: str = ""


@app.post("/orders/{order_id}/assign-courier")
async def order_assign_courier(order_id: int, body: AssignCourierIn,
                               session: AsyncSession = Depends(get_session)):
    """Сборщик «Передать курьеру» с выбором конкретного курьера."""
    order = await _load_order(session, order_id)
    if order.assembled_at is None:
        order.assembled_at = _dt.utcnow()
    order.assigned_courier_code = body.courier_code
    order.assigned_courier_name = body.courier_name

    ok, detail, method = True, {}, "update_order_courier"
    if order.org_id:
        emp_id = await _iiko_write.cloud_courier_id(order.org_id, body.courier_name)
        if emp_id:
            ok, detail = await _iiko_write.assign_courier(
                order.org_id, order.iiko_order_id, emp_id)
            order.iiko_sync_error = None if ok else str(detail)[:500]
        else:
            ok, detail = False, {"error": "courier_not_matched"}
            order.iiko_sync_error = "courier_not_matched"

    await session.commit()
    if order.org_id:
        await _log_iiko_event(session, order, method, ok, detail)
    order = await _load_order(session, order_id)
    await realtime.broadcast_order(order)
    return realtime.order_payload(order)


class TgAuthIn(BaseModel):
    initData: str


# Человеческие подписи ролей в списке смены (код RMS -> подпись).
_ROLE_LABELS = {
    "КР": "Курьер", "КЭКСПО20": "Курьер",
    "MN0": "Управляющий", "УПРЭКСПО": "Управляющий", "Администратор": "Управляющий",
    "ADM": "Сисадмин", "ADM2": "Сисадмин",
    "DW1": "Тех персонал", "WR1": "Официант", "opeCC": "Оператор КЦ",
    "Бух": "Бухгалтер", "GBUH": "Бухгалтер", "МАРКЕТ": "Маркетолог",
}


def _tg_user_id(init_data: str) -> str:
    """Проверенный telegram user id из initData; HTTPException(401) если подпись бита."""
    pairs = _telegram.parse_and_validate(init_data)
    if pairs is None:
        raise HTTPException(status_code=401, detail="Invalid initData")
    telegram_id = str(_telegram.extract_user(pairs).get("id") or "")
    if not telegram_id:
        raise HTTPException(status_code=401, detail="Invalid initData")
    return telegram_id


@app.get("/tg/shift-employees")
async def tg_shift_employees(session: AsyncSession = Depends(get_session)):
    """Кто сегодня на смене (открытая явка iiko, начатая сегодня) — для входа
    без PIN. busy = имя уже выбрано другим Telegram-аккаунтом сегодня."""
    try:
        shift = await _rms.shift_employees_today()
    except Exception as e:  # noqa: BLE001 — RMS недоступен/не настроен
        raise HTTPException(status_code=503, detail=f"iiko недоступен: {e}")
    today = _local_today().isoformat()
    claimed = (await session.execute(
        _select(_TelegramLink.employee_id)
        .where(_TelegramLink.claimed_on == today,
               _TelegramLink.employee_id.is_not(None)))).scalars().all()
    busy_ids = set(claimed)
    return [{
        "employee_id": e["employee_id"],
        "name": e["name"],
        "role": e["role"],
        "role_label": _ROLE_LABELS.get(e["role"], e["role"]),
        "busy": e["employee_id"] in busy_ids,
    } for e in shift]


class TgClaimIn(BaseModel):
    initData: str
    employee_id: str


@app.post("/tg/claim")
async def tg_claim(body: TgClaimIn, session: AsyncSession = Depends(get_session)):
    """Вход по явке: тап по своему имени вместо PIN. Требует, чтобы явка
    сотрудника всё ещё была открыта (список кэширован ~60с)."""
    telegram_id = _tg_user_id(body.initData)
    try:
        shift = await _rms.shift_employees_today()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"iiko недоступен: {e}")
    emp = next((x for x in shift if x["employee_id"] == body.employee_id), None)
    if emp is None:
        raise HTTPException(status_code=409,
                            detail="Явка закрыта или смена не открыта — обнови список")

    link = (await session.execute(
        _select(_TelegramLink).where(_TelegramLink.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if link is None:
        link = _TelegramLink(telegram_id=telegram_id)
        session.add(link)
    link.employee_code = emp["code"]
    link.name = emp["name"]
    link.branch_code = emp["branch_code"]
    link.org_id = emp["org_id"]
    link.role = emp["role"]
    link.employee_id = emp["employee_id"]
    link.claimed_on = _local_today().isoformat()
    await session.commit()

    return {
        "linked": True,
        "name": emp["name"],
        "branch_code": emp["branch_code"],
        "org_id": emp["org_id"],
        "role": emp["role"],
        "courier_code": emp["code"],
    }


@app.post("/tg/auth")
async def tg_auth(body: TgAuthIn, session: AsyncSession = Depends(get_session)):
    """Проверка initData и привязки. Привязка живёт, пока явка открыта:
    вчерашняя/чужая эпоха (старый PIN-вход) или закрытая явка -> linked=False."""
    telegram_id = _tg_user_id(body.initData)
    link = (await session.execute(
        _select(_TelegramLink).where(_TelegramLink.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if not link:
        return {"linked": False}
    if link.claimed_on != _local_today().isoformat():
        return {"linked": False}
    try:
        shift = await _rms.shift_employees_today()
        if all(x["employee_id"] != link.employee_id for x in shift):
            return {"linked": False}
    except Exception:  # noqa: BLE001 — RMS лёг: не выкидываем курьера среди смены
        pass
    return {
        "linked": True,
        "name": link.name,
        "branch_code": link.branch_code,
        "org_id": link.org_id,
        "role": link.role,
        "courier_code": link.employee_code,
    }


# Заказы этих агрегаторов возят их собственные курьеры — в списке доставок
# для наших курьеров они шум (та же логика, что orderChannel на фронте).
_AGGREGATOR_KEYS = ("wolt", "яндекс", "yandex", "glovo", "choco")


@app.get("/tg/deliveries")
async def tg_deliveries(org_id: str, session: AsyncSession = Depends(get_session)):
    """Доставки филиала, ждущие курьера (только просмотр): собранные,
    не доставленные и БЕЗ назначенного курьера. Плюс общий телефон колл-центра."""
    rows = (await session.execute(
        _select(_Order)
        .where(_Order.org_id == org_id,
               _Order.service_type == "DeliveryByCourier",
               _Order.assembled_at.is_not(None),
               _Order.delivered_at.is_(None),
               # У кого курьер уже есть — те в «Моих» у своего курьера. Признак
               # «занят» — именно КОД: «Мои» ищут по assigned_courier_code, и
               # только он адресует заказ конкретному курьеру. По имени НЕ
               # фильтруем: ingest ставит имя из iiko всегда, а код — лишь когда
               # сотрудник нашёлся в RMS, поэтому заказ с одним именем не попадёт
               # ни в чьи «Мои» и обязан остаться здесь, а не стать невидимкой.
               _Order.assigned_courier_code.is_(None),
               # Закрытые/отменённые в iiko, у которых delivered_at не успел
               # проставиться (артефакт закрытия) — курьеру не показываем.
               _Order.status.not_in(("Closed", "Cancelled", "Delivered")))
        .order_by(_Order.assembled_at))).scalars().all()
    orders = [o for o in rows
              if not any(k in (o.customer_name or "").lower()
                         for k in _AGGREGATOR_KEYS)]
    return {"call_center_phone": settings.call_center_phone,
            "orders": [realtime.order_payload(o) for o in orders]}


@app.get("/tg/my-orders")
async def tg_my_orders(courier_code: str,
                       session: AsyncSession = Depends(get_session)):
    """Заказы, назначенные курьеру и ещё не доставленные."""
    rows = (await session.execute(
        _select(_Order)
        .where(_Order.assigned_courier_code == courier_code,
               _Order.delivered_at.is_(None))
        .order_by(_Order.created_at))).scalars().all()
    return [realtime.order_payload(o) for o in rows]


@app.post("/tg/unlink")
async def tg_unlink(body: TgAuthIn, session: AsyncSession = Depends(get_session)):
    """Выход курьера: удаляем привязку Telegram-аккаунта, чтобы можно было войти другим PIN."""
    pairs = _telegram.parse_and_validate(body.initData)
    if pairs is None:
        raise HTTPException(status_code=401, detail="Invalid initData")
    telegram_id = str(_telegram.extract_user(pairs).get("id") or "")
    if telegram_id:
        from app.models import TelegramLink as _TL
        from sqlalchemy import delete as _del
        await session.execute(_del(_TL).where(_TL.telegram_id == telegram_id))
        await session.commit()
    return {"ok": True}


# ── GPS-трекинг курьеров ────────────────────────────────────────────────────
from app.models import CourierLocation as _CourierLocation


class TgLocationIn(BaseModel):
    initData: str
    lat: float
    lon: float


async def _save_courier_location(session: AsyncSession, telegram_id: str,
                                 lat: float, lon: float) -> bool:
    """Пишет последнюю позицию курьера по его telegram_id. False — если не привязан."""
    if not telegram_id or lat is None or lon is None:
        return False
    link = (await session.execute(
        _select(_TelegramLink).where(_TelegramLink.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if not link:
        return False
    loc = (await session.execute(
        _select(_CourierLocation).where(
            _CourierLocation.courier_code == link.employee_code))
    ).scalar_one_or_none()
    if loc is None:
        loc = _CourierLocation(courier_code=link.employee_code)
        session.add(loc)
    loc.name = link.name
    loc.branch_code = link.branch_code
    loc.lat = lat
    loc.lon = lon
    loc.updated_at = _dt.utcnow()
    await session.commit()
    return True


@app.post("/tg/location")
async def tg_location(body: TgLocationIn,
                      session: AsyncSession = Depends(get_session)):
    """Курьер из Telegram-аппа шлёт свою геопозицию (раз в ~20с, пока апп открыт)."""
    pairs = _telegram.parse_and_validate(body.initData)
    if pairs is None:
        raise HTTPException(status_code=401, detail="Invalid initData")
    telegram_id = str(_telegram.extract_user(pairs).get("id") or "")
    if not await _save_courier_location(session, telegram_id, body.lat, body.lon):
        raise HTTPException(status_code=401, detail="not linked")
    return {"ok": True}


@app.post("/tg/bot-webhook")
async def tg_bot_webhook(request: Request,
                         session: AsyncSession = Depends(get_session)):
    """Webhook бота: принимает Live Location курьера в ФОНЕ (даже когда апп закрыт).
    Живая геопозиция приходит как edited_message с обновляемым location."""
    if settings.webhook_secret:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != settings.webhook_secret:
            raise HTTPException(status_code=403, detail="bad secret")
    try:
        update = await request.json()
    except Exception:
        return {"ok": True}
    msg = update.get("edited_message") or update.get("message") or {}
    loc = msg.get("location") or {}
    telegram_id = str((msg.get("from") or {}).get("id") or "")
    if loc and telegram_id:
        await _save_courier_location(session, telegram_id,
                                     loc.get("latitude"), loc.get("longitude"))
    return {"ok": True}


@app.post("/tg/set-bot-webhook")
async def tg_set_bot_webhook(key: str = Query(default="")):
    """Разово: зарегистрировать webhook бота на наш /tg/bot-webhook (для Live Location)."""
    if settings.webhook_secret and key != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad key")
    base = iiko_cloud._public_base_url()
    if not base:
        raise HTTPException(status_code=400, detail="no public base url")
    return await _telegram.set_webhook(f"{base}/tg/bot-webhook",
                                       secret_token=settings.webhook_secret)


@app.get("/admin/couriers-locations")
async def couriers_locations(branch_code: str | None = None,
                             session: AsyncSession = Depends(get_session)):
    """Последние позиции курьеров (для карты управляющего)."""
    q = _select(_CourierLocation)
    if branch_code:
        q = q.where(_CourierLocation.branch_code == branch_code)
    rows = (await session.execute(q)).scalars().all()
    now = _dt.utcnow()
    out = []
    for r in rows:
        mins = int((now - r.updated_at).total_seconds() // 60) if r.updated_at else None
        out.append({
            "courier_code": r.courier_code, "name": r.name,
            "lat": r.lat, "lon": r.lon,
            "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None,
            "minutes_ago": mins,
        })
    return out


# ── Публичное отслеживание заказа (для клиента, без авторизации) ─────────────
def _iso(value) -> str | None:
    """datetime -> ISO-строка с суффиксом Z, либо None."""
    return value.isoformat() + "Z" if value is not None else None


@app.get("/api/track/{iiko_order_id}")
async def track_order(iiko_order_id: str,
                      session: AsyncSession = Depends(get_session)):
    """ПУБЛИЧНО: статус заказа для клиента по ссылке (как у Domino's).

    Не требует авторизации и НЕ отдаёт телефон/имя клиента — только номер,
    адрес, таймлайн стадий и (пока курьер в пути) живую точку курьера.
    """
    order = (await session.execute(
        _select(_Order).where(_Order.iiko_order_id == iiko_order_id))
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="order not found")

    accepted_times = [it.station_accepted_at for it in order.items
                      if it.station_accepted_at is not None]
    ready_times = [it.station_ready_at for it in order.items
                   if it.station_ready_at is not None]

    cooking_at = min(accepted_times) if accepted_times else None
    if ready_times:
        assembling_at = min(ready_times)
    else:
        assembling_at = order.assembled_at
    assembling_done = bool(ready_times) or order.assembled_at is not None

    stages = [
        {"key": "accepted", "label": "Принят",
         "done": order.created_at is not None, "at": _iso(order.created_at)},
        {"key": "cooking", "label": "Готовится",
         "done": bool(accepted_times), "at": _iso(cooking_at)},
        {"key": "assembling", "label": "Собирается",
         "done": assembling_done, "at": _iso(assembling_at)},
        {"key": "on_way", "label": "В пути",
         "done": order.on_way_at is not None, "at": _iso(order.on_way_at)},
        {"key": "delivered", "label": "Доставлен",
         "done": order.delivered_at is not None, "at": _iso(order.delivered_at)},
    ]

    # Живая точка курьера видна ТОЛЬКО пока заказ в пути (взят, но не доставлен)
    # и для назначенного курьера есть свежая геопозиция.
    courier = None
    if (order.on_way_at is not None and order.delivered_at is None
            and order.assigned_courier_code):
        loc = (await session.execute(
            _select(_CourierLocation).where(
                _CourierLocation.courier_code == order.assigned_courier_code))
        ).scalar_one_or_none()
        if loc is not None:
            minutes_ago = (int((_dt.utcnow() - loc.updated_at).total_seconds() // 60)
                           if loc.updated_at else None)
            courier = {"lat": loc.lat, "lon": loc.lon, "minutes_ago": minutes_ago}

    return {
        "number": order.number,
        "status": order.status,
        "address": order.address,
        "eta": None,
        "stages": stages,
        "courier": courier,
    }


# ── Карта спроса: тепловая карта заказов по адресам (для управляющего) ───────
@app.get("/admin/demand")
async def admin_demand(org_id: str, session: AsyncSession = Depends(get_session)):
    """Агрегирует заказы филиала с координатами в кластеры (~100 м).

    Группируем по округлённым до 3 знаков (lat, lon). Заказы без координат
    пропускаем. Возвращаем точки [{lat, lon, count, sample_address}],
    отсортированные по убыванию count — управляющий видит, где заказывают чаще
    всего (куда открыть филиал №14 / куда дать промо).
    """
    orders = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id))).scalars().all()

    clusters: dict[tuple[float, float], dict] = {}
    for o in orders:
        if o.address_lat is None or o.address_lon is None:
            continue
        key = (round(o.address_lat, 3), round(o.address_lon, 3))
        c = clusters.get(key)
        if c is None:
            clusters[key] = {
                "lat": key[0],
                "lon": key[1],
                "count": 1,
                "sample_address": o.address or "",
            }
        else:
            c["count"] += 1
            if not c["sample_address"] and o.address:
                c["sample_address"] = o.address

    points = sorted(clusters.values(), key=lambda p: p["count"], reverse=True)
    return points


# ──────────────────────────────────────────────────────────────────────────
# Аналитика для управляющего: live-сводка, рейтинг поваров, спрос по часам,
# выгрузка CSV. «Сегодня» = серверная UTC-дата.
# ──────────────────────────────────────────────────────────────────────────
import csv as _csv
import io as _io
from fastapi.responses import Response as _Response


@app.get("/admin/dashboard")
async def admin_dashboard(org_id: str, session: AsyncSession = Depends(get_session)):
    """Live-снимок «что сейчас»: активные заказы, готовка/доставка, зоны, курьеры."""
    today = _local_today()
    orders = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id))).scalars().all()

    active = [o for o in orders if o.status != "Closed" and o.delivered_at is None]
    active_orders = len(active)

    # Доставлено сегодня.
    delivered_today = sum(
        1 for o in orders
        if o.delivered_at is not None and _local_date(o.delivered_at) == today)

    # Среднее время готовки сегодня (по принятым сегодня позициям).
    prep_secs: list[float] = []
    for o in orders:
        for it in o.items:
            if it.station_accepted_at is None:
                continue
            if _local_date(it.station_accepted_at) != today:
                continue
            prep = _prep_seconds(it)
            if prep is not None:
                prep_secs.append(prep)
    avg_prep = _mean(prep_secs)

    # Среднее время доставки сегодня (по доставленным сегодня, без артефактов закрытия).
    delivery_secs = [
        s for o in orders
        if o.delivered_at is not None and _local_date(o.delivered_at) == today
        and (s := _stages.delivery_seconds(o)) is not None
    ]
    avg_delivery = _mean(delivery_secs)

    # По зонам: не готовые позиции сегодняшних активных заказов.
    by_zone = {"rolls": 0, "pizza": 0, "hot": 0, "other": 0}
    for o in active:
        for it in o.items:
            if it.station_ready_at is not None:
                continue
            zone = it.station if it.station in by_zone else "other"
            by_zone[zone] += 1

    # Курьеры на смене: уникальные courier_code со свежей геопозицией (<30 мин).
    # Только филиал org_id (через его branch-коды) — CourierLocation без org_id,
    # иначе считались бы курьеры всех филиалов.
    now = _dt.utcnow()
    loc_q = _select(_CourierLocation)
    bcs = await _org_branch_codes(session, org_id)
    if bcs:
        loc_q = loc_q.where(_CourierLocation.branch_code.in_(bcs))
    locs = (await session.execute(loc_q)).scalars().all()
    on_shift = {
        loc.courier_code for loc in locs
        if loc.updated_at is not None
        and (now - loc.updated_at).total_seconds() < _ACTIVE_WINDOW_MIN * 60
    }

    return {
        "active_orders": active_orders,
        "delivered_today": delivered_today,
        "avg_prep_today_seconds": avg_prep,
        "avg_delivery_today_seconds": avg_delivery,
        "by_zone": by_zone,
        "on_shift_couriers": len(on_shift),
    }


@app.get("/admin/leaderboard")
async def admin_leaderboard(org_id: str, session: AsyncSession = Depends(get_session)):
    """Рейтинг поваров за сегодня: группируем готовые сегодня позиции по checked_by."""
    today = _local_today()
    orders = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id))).scalars().all()

    done: dict[str, int] = {}
    preps: dict[str, list[float]] = {}
    for o in orders:
        for it in o.items:
            if it.station_ready_at is None:
                continue
            if _local_date(it.station_ready_at) != today:
                continue
            name = (it.checked_by or "").strip()
            if not name:
                continue
            done[name] = done.get(name, 0) + 1
            prep = _prep_seconds(it)
            if prep is not None:
                preps.setdefault(name, []).append(prep)

    rows = [
        {
            "name": name,
            "items_done": done[name],
            "avg_prep_seconds": _mean(preps.get(name, [])),
        }
        for name in done
    ]
    rows.sort(key=lambda r: r["items_done"], reverse=True)
    return rows


@app.get("/admin/forecast")
async def admin_forecast(org_id: str, session: AsyncSession = Depends(get_session)):
    """Спрос по часам суток: число заказов филиала по created_at.hour (24 ячейки)."""
    orders = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id))).scalars().all()
    counts = [0] * 24
    for o in orders:
        if o.created_at is not None:
            counts[(o.created_at + _td(hours=settings.iiko_tz_offset_hours)).hour] += 1
    return [{"hour": h, "count": counts[h]} for h in range(24)]


@app.get("/admin/report.csv")
async def admin_report_csv(org_id: str, session: AsyncSession = Depends(get_session)):
    """CSV-выгрузка: по строке на каждую сегодняшнюю позицию заказа.

    UTF-8 BOM в начале, чтобы Excel корректно открывал кириллицу.
    """
    today = _local_today()
    orders = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id))).scalars().all()

    buf = _io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow([
        "order_number", "created_at", "status", "zone", "item_name",
        "accepted_at", "ready_at", "prep_seconds", "checked_by",
    ])
    for o in orders:
        for it in o.items:
            if it.station_accepted_at is None or _local_date(it.station_accepted_at) != today:
                continue
            prep = _prep_seconds(it)
            writer.writerow([
                o.number or "",
                _iso(o.created_at) or "",
                o.status or "",
                it.station or "other",
                it.name or "",
                _iso(it.station_accepted_at) or "",
                _iso(it.station_ready_at) or "",
                int(prep) if prep is not None else "",
                (it.checked_by or "").strip(),
            ])

    body = "﻿" + buf.getvalue()
    return _Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="report.csv"'},
    )


def _zone_out(z: "_DeliveryZone") -> dict:
    return {"id": z.id, "name": z.name, "polygon": z.polygon or [], "color": z.color}


@app.get("/admin/delivery-zones")
async def list_delivery_zones(org_id: str, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        _select(_DeliveryZone).where(_DeliveryZone.org_id == org_id)
        .order_by(_DeliveryZone.id))).scalars().all()
    return [_zone_out(z) for z in rows]


@app.post("/admin/delivery-zones")
async def create_delivery_zone(body: dict, session: AsyncSession = Depends(get_session)):
    z = _DeliveryZone(
        org_id=body.get("org_id", ""), name=body.get("name", ""),
        polygon=body.get("polygon") or [], color=body.get("color") or "#2563eb")
    session.add(z)
    await session.commit()
    await session.refresh(z)
    return _zone_out(z)


@app.put("/admin/delivery-zones/{zone_id}")
async def update_delivery_zone(zone_id: int, body: dict, org_id: str,
                               session: AsyncSession = Depends(get_session)):
    z = (await session.execute(
        _select(_DeliveryZone).where(_DeliveryZone.id == zone_id,
                                     _DeliveryZone.org_id == org_id))).scalar_one_or_none()
    if z is None:
        raise HTTPException(status_code=404, detail="zone not found")
    if "name" in body: z.name = body["name"]
    if "polygon" in body: z.polygon = body["polygon"] or []
    if "color" in body: z.color = body["color"]
    z.updated_at = _dt.utcnow()
    await session.commit()
    await session.refresh(z)
    return _zone_out(z)


@app.delete("/admin/delivery-zones/{zone_id}")
async def delete_delivery_zone(zone_id: int, org_id: str,
                               session: AsyncSession = Depends(get_session)):
    z = (await session.execute(
        _select(_DeliveryZone).where(_DeliveryZone.id == zone_id,
                                     _DeliveryZone.org_id == org_id))).scalar_one_or_none()
    if z is None:  # чужой филиал (или нет зоны) — не удаляем
        raise HTTPException(status_code=404, detail="zone not found")
    await session.execute(_sl_delete(_DeliveryZone).where(_DeliveryZone.id == zone_id))
    await session.commit()
    return {"ok": True}


from app import geo as _geo


@app.post("/admin/zones/auto-grid")
async def auto_grid_zones(org_id: str, n: int = 6,
                          session: AsyncSession = Depends(get_session)):
    """Авто-делит карту Астаны на сетку n×n зон. Границы — фиксированный bbox
    города (НЕ по координатам заказов: одна кривая точка растягивала сетку на
    пол-планеты). Старые авто-зоны заменяются; ручные зоны не трогаются."""
    bbox = _geo.ASTANA_BBOX
    cells = _geo.build_grid_zones(bbox, n)

    # сносим прежнюю авто-сетку этого org, ручные зоны оставляем
    await session.execute(_sl_delete(_DeliveryZone).where(
        _DeliveryZone.org_id == org_id, _DeliveryZone.auto_generated == True))  # noqa: E712
    for c in cells:
        session.add(_DeliveryZone(org_id=org_id, name=c["name"],
                                  polygon=c["polygon"], color="#94a3b8",
                                  auto_generated=True))
    await session.commit()
    return {"ok": True, "n": n, "zones": len(cells), "bbox": list(bbox)}


@app.get("/admin/demand-zones")
async def demand_zones(org_id: str, session: AsyncSession = Depends(get_session)):
    """Зоны + плотность заказов (count) для «Карты спроса». Цвет считает фронт."""
    zrows = (await session.execute(
        _select(_DeliveryZone).where(_DeliveryZone.org_id == org_id)
        .order_by(_DeliveryZone.id))).scalars().all()
    zones = [{"id": z.id, "name": z.name, "polygon": z.polygon or []} for z in zrows]

    orders = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id))).scalars().all()
    counts: dict[int, int] = {z["id"]: 0 for z in zones}
    for o in orders:
        zid = _geo.assign_zone(o.address_lat, o.address_lon, zones)
        if zid is not None:
            counts[zid] = counts.get(zid, 0) + 1
    return [{**z, "count": counts.get(z["id"], 0)} for z in zones]


# ──────────────────────────────────────────────────────────────────────────
# Единая выгрузка аналитики для внешнего сервиса. Защита — заголовок X-API-Key.
# ──────────────────────────────────────────────────────────────────────────
def require_analytics_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Пропускает только при совпадении X-API-Key с settings.analytics_api_key.
    Если ключ не задан в окружении — выгрузка выключена (fail-closed)."""
    key = settings.analytics_api_key
    if not key:
        raise HTTPException(status_code=503,
                            detail="analytics export disabled: set ANALYTICS_API_KEY")
    if x_api_key != key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    return True


@app.get("/admin/analytics/export")
async def admin_analytics_export(org_id: str,
                                 from_: str | None = Query(None, alias="from"),
                                 to: str | None = None,
                                 sla_minutes: int = 45,
                                 _auth: bool = Depends(require_analytics_key),
                                 session: AsyncSession = Depends(get_session)):
    """Вся аналитика филиала одним JSON (для внешнего сервиса). Защищено X-API-Key.

    Период (from/to, YYYY-MM-DD) влияет на analytics/stage_stats/kitchen_zones/
    zone_load/journal; anomalies/demand — за сегодня (как в исходных эндпоинтах).
    Переиспользует те же функции, что и отдельные эндпоинты.
    """
    dt_from, dt_to = _parse_period(from_, to)
    return {
        "org_id": org_id,
        "period": {"from": _iso(dt_from), "to": _iso(dt_to)},
        "analytics": await admin_analytics(org_id, from_, to, session),
        "stage_stats": await admin_stage_stats(org_id, from_, to, sla_minutes, session),
        "kitchen_zones": await admin_kitchen_zones(org_id, from_, to, session),
        "zone_load": await admin_zone_load(org_id, from_, to, session),
        "demand": await admin_demand(org_id, session),
        "anomalies": await admin_anomalies(org_id, session),
        "journal": await admin_journal(org_id, from_, to, None, None, None, 1000, 0, session),
    }


@app.delete("/admin/orders/by-number/{number}")
async def admin_delete_order_by_number(number: str, org_id: str,
                                       session: AsyncSession = Depends(get_session)):
    """Удаляет заказ (вместе с позициями и событиями) ТОЛЬКО из нашей базы — чистка
    зависших заказов, которые курьер не закрыл. iiko НЕ трогаем. Сверяем org_id."""
    order = (await session.execute(
        _select(_Order).where(_Order.number == number,
                              _Order.org_id == org_id))).scalars().first()
    if order is None:
        raise HTTPException(status_code=404, detail=f"order #{number} not found for org")
    oid = order.id
    await session.execute(_sl_delete(_OrderEvent).where(_OrderEvent.order_id == oid))
    await session.execute(_sl_delete(_OrderItem).where(_OrderItem.order_id == oid))
    await session.execute(_sl_delete(_Order).where(_Order.id == oid))
    await session.commit()
    return {"ok": True, "deleted_number": number, "deleted_id": oid}
