from __future__ import annotations
from fastapi import FastAPI, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_session
from app import iiko_cloud, ingest, realtime

app = FastAPI(title="Saya KDS backend")


@app.get("/health")
async def health():
    return {"ok": True}


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
