from __future__ import annotations
from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_session
from app import iiko_cloud, ingest, realtime
from app.models import Order

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
        select(Order).where(Order.org_id == org_id, Order.status != "Closed")
        .order_by(Order.created_at))).scalars().all()
    return [realtime.order_payload(o) for o in rows]


@app.post("/webhooks/iiko/cloud")
async def iiko_webhook(events: list[dict], key: str = Query(default=""),
                       session: AsyncSession = Depends(get_session)):
    if settings.webhook_secret and key != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad key")

    handled = 0
    for event in events or []:
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
        await realtime.broadcast_order(order)
        handled += 1
    return {"ok": True, "handled": handled}


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


from datetime import datetime as _dt
from sqlalchemy import select as _select
from app.models import Order as _Order, OrderItem as _OrderItem


class ActionIn(BaseModel):
    by: str = ""


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
    return await _item_and_broadcast(session, item_id)


@app.post("/items/{item_id}/ready")
async def item_ready(item_id: int, body: ActionIn,
                     session: AsyncSession = Depends(get_session)):
    item = (await session.execute(
        _select(_OrderItem).where(_OrderItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    item.station_ready_at = _dt.utcnow()
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
    return await _item_and_broadcast(session, item_id)


async def _order_action_and_broadcast(session: AsyncSession, order_id: int) -> dict:
    await session.commit()
    order = await _load_order(session, order_id)
    await realtime.broadcast_order(order)
    return realtime.order_payload(order)


@app.post("/orders/{order_id}/assemble")
async def order_assemble(order_id: int, body: ActionIn,
                         session: AsyncSession = Depends(get_session)):
    """Сборщик: «передать курьеру»."""
    order = await _load_order(session, order_id)
    order.assembled_at = _dt.utcnow()
    return await _order_action_and_broadcast(session, order_id)


@app.post("/orders/{order_id}/take")
async def order_take(order_id: int, body: ActionIn,
                     session: AsyncSession = Depends(get_session)):
    """Курьер забрал заказ."""
    order = await _load_order(session, order_id)
    order.on_way_at = _dt.utcnow()
    return await _order_action_and_broadcast(session, order_id)


@app.post("/orders/{order_id}/delivered")
async def order_delivered(order_id: int, body: ActionIn,
                          session: AsyncSession = Depends(get_session)):
    """Курьер доставил заказ."""
    order = await _load_order(session, order_id)
    order.delivered_at = _dt.utcnow()
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


@app.post("/debug/seed")
async def debug_seed(org_id: str = "demo",
                     session: AsyncSession = Depends(get_session)):
    """Локальный просмотр: создаёт демо-маппинг категорий и 3 заказа для org_id."""
    existing = (await session.execute(_select(_PSM))).scalars().all()
    have = {(r.product_id, r.category_id) for r in existing}
    for category_id, station in _SEED_CATEGORIES.items():
        if (None, category_id) not in have:
            session.add(_PSM(product_id=None, category_id=category_id, station=station))
    await session.commit()

    payloads = []
    for tmpl in _SEED_ORDERS:
        data = tmpl.model_copy(update={"org_id": org_id})
        order = await ingest.ingest_order(session, data)
        await realtime.broadcast_order(order)
        payloads.append(realtime.order_payload(order))
    return payloads


def _prep_seconds(item) -> float | None:
    """Время готовки позиции в секундах, если оба тайм-штампа проставлены."""
    if item.station_accepted_at and item.station_ready_at:
        return (item.station_ready_at - item.station_accepted_at).total_seconds()
    return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@app.get("/admin/analytics")
async def admin_analytics(org_id: str, session: AsyncSession = Depends(get_session)):
    """Сводная аналитика по филиалу: заказы, сотрудники, зоны, доставка."""
    orders = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id))).scalars().all()

    total_orders = len(orders)
    delivered_orders = sum(1 for o in orders if o.delivered_at is not None)
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

    # Доставка: по заказам, где есть и on_way_at, и delivered_at.
    delivery_secs = [
        (o.delivered_at - o.on_way_at).total_seconds()
        for o in orders
        if o.on_way_at is not None and o.delivered_at is not None
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


@app.get("/rms/branches")
async def rms_branches():
    """Филиалы и сотрудники из RMS (живой вызов: логин/логаут на стороне rms)."""
    from app import rms
    return await rms.branches_with_employees()


# ──────────────────────────────────────────────────────────────────────────
# Разметка зон (станций) для блюд по группам меню iiko.
# ──────────────────────────────────────────────────────────────────────────
from app.stations import build_resolver as _build_resolver, classify_group as _classify_group, classify_name as _classify_name, VALID_STATIONS as _VALID_STATIONS


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
async def admin_stations_auto(org_id: str,
                              session: AsyncSession = Depends(get_session)):
    """Авторазметка: тянем номенклатуру iiko, классифицируем блюда по группам меню,
    сохраняем маппинг по product_id и пересчитываем активные заказы."""
    nomenclature = await iiko_cloud.fetch_nomenclature(org_id)
    products = _products_with_group(nomenclature)

    for p in products:
        # Классифицируем по НАЗВАНИЮ блюда (надёжнее), с откатом на группу меню.
        zone = _classify_name(p["name"])
        if zone == "other":
            zone = _classify_group(p["group_name"])
        await _upsert_station(session, p["product_id"], zone)
    await session.commit()

    reclassified = await _reresolve_active_orders(session, org_id)
    return {"mapped_products": len(products), "reclassified_items": reclassified}


@app.get("/admin/stations")
async def admin_stations_get(org_id: str,
                             session: AsyncSession = Depends(get_session)):
    """Текущая разметка для админ-UI: продукты, сгруппированные по группам меню iiko,
    с текущей зоной (из ProductStationMap, иначе 'other')."""
    nomenclature = await iiko_cloud.fetch_nomenclature(org_id)
    products = _products_with_group(nomenclature)

    rows = (await session.execute(_select(_PSM))).scalars().all()
    by_product = {r.product_id: r.station for r in rows if r.product_id}

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for p in products:
        group_name = p["group_name"] or "Без группы"
        if group_name not in groups:
            groups[group_name] = []
            order.append(group_name)
        groups[group_name].append({
            "product_id": p["product_id"],
            "name": p["name"],
            "station": by_product.get(p["product_id"], "other"),
        })

    return [{"group": g, "products": groups[g]} for g in order]


class _StationItemIn(BaseModel):
    product_id: str
    station: str


class _StationsIn(BaseModel):
    items: list[_StationItemIn] = []


@app.post("/admin/stations")
async def admin_stations_post(org_id: str, body: _StationsIn,
                              session: AsyncSession = Depends(get_session)):
    """Ручные правки разметки: UPSERT по product_id, затем пересчёт активных заказов."""
    updated = 0
    for item in body.items:
        if item.station not in _VALID_STATIONS:
            raise HTTPException(status_code=400, detail=f"bad station: {item.station}")
        if not item.product_id:
            continue
        await _upsert_station(session, item.product_id, item.station)
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


@app.get("/branches/couriers")
async def branches_couriers(branch_code: str = ""):
    """Курьеры конкретного филиала RMS (роли КР/КЭКСПО20)."""
    if not branch_code:
        return []
    branches = await _rms.branches_with_employees()
    branch = next((b for b in branches if b["code"] == branch_code), None)
    if not branch:
        return []
    return [
        {"code": e["code"], "name": e["name"]}
        for e in branch["employees"]
        if e.get("role") in _COURIER_ROLES
    ]


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
    await session.commit()
    order = await _load_order(session, order_id)
    await realtime.broadcast_order(order)
    return realtime.order_payload(order)


class TgAuthIn(BaseModel):
    initData: str


@app.post("/tg/auth")
async def tg_auth(body: TgAuthIn, session: AsyncSession = Depends(get_session)):
    """Проверка initData и поиск привязки Telegram-аккаунта к сотруднику."""
    pairs = _telegram.parse_and_validate(body.initData)
    if pairs is None:
        raise HTTPException(status_code=401, detail="Invalid initData")
    user = _telegram.extract_user(pairs)
    telegram_id = str(user.get("id") or "")
    if not telegram_id:
        raise HTTPException(status_code=401, detail="Invalid initData")
    link = (await session.execute(
        _select(_TelegramLink).where(_TelegramLink.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if not link:
        return {"linked": False}
    return {
        "linked": True,
        "name": link.name,
        "branch_code": link.branch_code,
        "org_id": link.org_id,
        "role": link.role,
        "courier_code": link.employee_code,
    }


class TgLinkIn(BaseModel):
    initData: str
    pin: str


@app.post("/tg/link")
async def tg_link(body: TgLinkIn, session: AsyncSession = Depends(get_session)):
    """Привязка Telegram-аккаунта к сотруднику по PIN (code)."""
    pairs = _telegram.parse_and_validate(body.initData)
    if pairs is None:
        raise HTTPException(status_code=401, detail="Invalid initData")
    user = _telegram.extract_user(pairs)
    telegram_id = str(user.get("id") or "")
    if not telegram_id:
        raise HTTPException(status_code=401, detail="Invalid initData")

    pin = body.pin.strip()
    emp = await _rms.find_employee_by_pin(pin)
    if not emp:
        raise HTTPException(status_code=401, detail="Неверный PIN")

    link = (await session.execute(
        _select(_TelegramLink).where(_TelegramLink.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if link is None:
        link = _TelegramLink(telegram_id=telegram_id)
        session.add(link)
    link.employee_code = pin
    link.name = emp["name"]
    link.branch_code = emp["branch_code"]
    link.org_id = emp["org_id"]
    link.role = emp["role"]
    await session.commit()

    return {
        "linked": True,
        "name": emp["name"],
        "branch_code": emp["branch_code"],
        "org_id": emp["org_id"],
        "role": emp["role"],
        "courier_code": pin,
    }


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


# ── GPS-трекинг курьеров ────────────────────────────────────────────────────
from app.models import CourierLocation as _CourierLocation


class TgLocationIn(BaseModel):
    initData: str
    lat: float
    lon: float


@app.post("/tg/location")
async def tg_location(body: TgLocationIn,
                      session: AsyncSession = Depends(get_session)):
    """Курьер из Telegram-аппа шлёт свою геопозицию (раз в ~20с)."""
    pairs = _telegram.parse_and_validate(body.initData)
    if pairs is None:
        raise HTTPException(status_code=401, detail="Invalid initData")
    user = _telegram.extract_user(pairs)
    telegram_id = str(user.get("id") or "")
    link = (await session.execute(
        _select(_TelegramLink).where(_TelegramLink.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=401, detail="not linked")

    loc = (await session.execute(
        _select(_CourierLocation).where(
            _CourierLocation.courier_code == link.employee_code))
    ).scalar_one_or_none()
    if loc is None:
        loc = _CourierLocation(courier_code=link.employee_code)
        session.add(loc)
    loc.name = link.name
    loc.branch_code = link.branch_code
    loc.lat = body.lat
    loc.lon = body.lon
    loc.updated_at = _dt.utcnow()
    await session.commit()
    return {"ok": True}


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
