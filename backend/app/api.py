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
    org_id: str
    role: str
    pin: str


@app.post("/auth/login")
async def login(body: LoginIn):
    # MVP: PIN сверяем со списком сотрудников RMS по полю code.
    from app import rms
    employees = await rms.fetch_employees()
    match = next((e for e in employees if e["code"] and e["code"] == body.pin), None)
    if not match:
        raise HTTPException(status_code=401, detail="bad pin")
    if body.role not in ("rolls", "pizza", "hot", "assembler", "courier"):
        raise HTTPException(status_code=400, detail="bad role")
    token = jwt.encode(
        {"org_id": body.org_id, "role": body.role,
         "emp_id": match["id"], "name": match["name"],
         "exp": datetime.utcnow() + timedelta(hours=12)},
        settings.jwt_secret, algorithm="HS256")
    return {"token": token, "name": match["name"], "role": body.role, "org_id": body.org_id}


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
