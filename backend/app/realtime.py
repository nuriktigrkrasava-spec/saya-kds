from __future__ import annotations
import socketio
from app.models import Order

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")

ZONES = ("rolls", "pizza", "hot", "other")


def room(org_id: str, zone: str) -> str:
    return f"org:{org_id}:{zone}"


def _iso(dt) -> str | None:
    """Наивный UTC -> ISO с суффиксом Z, чтобы фронт считал время одинаково на всех планшетах."""
    return dt.isoformat() + "Z" if dt is not None else None


def order_payload(order: Order) -> dict:
    return {
        "id": order.id,
        "iiko_order_id": order.iiko_order_id,
        "org_id": order.org_id,
        "number": order.number,
        "status": order.status,
        "customer": order.customer_name,
        "phone": order.phone,
        "address": order.address,
        "lat": order.address_lat,
        "lon": order.address_lon,
        "service_type": order.service_type,
        "comment": order.comment or "",
        "created_at": _iso(order.created_at),
        "assembled": order.assembled_at is not None,
        "on_way": order.on_way_at is not None,
        "delivered": order.delivered_at is not None,
        "iiko_sync_error": order.iiko_sync_error,
        "payment_type_name": order.payment_type_name,
        "items": [
            {
                "id": i.id, "product_id": i.product_id, "name": i.name,
                "amount": i.amount, "station": i.station,
                "comment": i.comment or "",
                "is_checked": i.is_checked,
                "accepted": i.station_accepted_at is not None,
                "ready": i.station_ready_at is not None,
                "accepted_at": _iso(i.station_accepted_at),
                "ready_at": _iso(i.station_ready_at),
            }
            for i in order.items
        ],
    }


async def broadcast_order(order: Order) -> None:
    """Рассылает заказ в комнаты всех зон его филиала, в которых есть позиции."""
    payload = order_payload(order)
    zones_present = {i.station for i in order.items} or {"other"}
    for zone in zones_present:
        await sio.emit("order:update", payload, room=room(order.org_id, zone))
    # сборщик видит весь заказ
    await sio.emit("order:update", payload, room=room(order.org_id, "assembler"))
    # курьер видит весь заказ
    await sio.emit("order:update", payload, room=room(order.org_id, "courier"))


async def broadcast_stoplist(org_id: str, items: list[dict]) -> None:
    """Рассылает текущий стоп-лист филиала во все рабочие комнаты (зоны + сборщик)."""
    payload = {"org_id": org_id, "items": items}
    for zone in (*ZONES, "assembler"):
        await sio.emit("stoplist:update", payload, room=room(org_id, zone))


@sio.event
async def join(sid, data):
    """Клиент просит вступить в комнату своего филиала+зоны. data={org_id, zone}."""
    org_id, zone = data.get("org_id"), data.get("zone")
    if not org_id or zone not in (*ZONES, "assembler", "courier"):
        return {"ok": False, "error": "bad room"}
    await sio.enter_room(sid, room(org_id, zone))
    return {"ok": True}
