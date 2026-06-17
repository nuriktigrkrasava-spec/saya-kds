from __future__ import annotations
import httpx
from app.config import settings
from app.schemas import NormalizedOrder, WebhookItem

BASE = "https://api-ru.iiko.services"

DELIVERY_ORDER_STATUSES = [
    "WaitCooking", "ReadyForCooking", "CookingStarted", "CookingCompleted",
    "Waiting", "OnWay", "Delivered", "Closed", "Cancelled",
]


async def get_token() -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        # ВАЖНО: путь без /auth/ — именно так ключ работает.
        r = await client.post(f"{BASE}/api/1/access_token",
                              json={"apiLogin": settings.cloud_api_login})
        r.raise_for_status()
        return r.json()["token"]


async def fetch_delivery_by_id(organization_id: str, order_id: str) -> dict | None:
    token = await get_token()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE}/api/1/deliveries/by_id",
            headers={"Authorization": f"Bearer {token}"},
            json={"organizationId": organization_id, "orderIds": [order_id]})
        r.raise_for_status()
        orders = r.json().get("orders", [])
        return orders[0] if orders else None


async def fetch_deliveries_by_revision(organization_id: str, start_revision: int) -> dict:
    """Инкрементально: заказы, изменённые после start_revision. Возвращает {orders, maxRevision}."""
    token = await get_token()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/api/1/deliveries/by_revision",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"organizationId": organization_id, "startRevision": start_revision})
        r.raise_for_status()
        return r.json()


async def fetch_nomenclature(organization_id: str) -> dict:
    token = await get_token()
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(f"{BASE}/api/1/nomenclature",
                              headers={"Authorization": f"Bearer {token}"},
                              json={"organizationId": organization_id})
        r.raise_for_status()
        return r.json()


def normalize_full_order(wrapper: dict, organization_id: str) -> NormalizedOrder:
    o = wrapper.get("order") or {}
    customer = (o.get("customer") or {}).get("name", "")
    dp = o.get("deliveryPoint") or {}
    addr_block = (dp.get("address") or {})
    address = ((addr_block.get("street") or {}).get("name")) or addr_block.get("line1", "") or ""
    coords = dp.get("coordinates") or {}
    items: list[WebhookItem] = []
    for it in o.get("items", []) or []:
        product = it.get("product") or {}
        if not product.get("id"):
            continue
        combo = it.get("comboInformation") or {}
        items.append(WebhookItem(
            product_id=str(product["id"]),
            category_id=(str((product.get("category") or {}).get("id"))
                         if (product.get("category") or {}).get("id") else None),
            name=product.get("name", ""),
            amount=float(it.get("amount", 1) or 1),
            combo_id=(str(combo.get("comboId")) if combo.get("comboId") else None),
        ))
    return NormalizedOrder(
        iiko_order_id=str(wrapper.get("id", "")),
        org_id=organization_id,
        number=str(o.get("number") or ""),
        status=str(o.get("status") or ""),
        customer=customer,
        phone=o.get("phone", "") or "",
        address=address,
        lat=coords.get("latitude"),
        lon=coords.get("longitude"),
        total=o.get("sum"),
        service_type=(o.get("orderType") or {}).get("orderServiceType"),
        items=items,
    )


def normalize_table_order(event_or_wrapper: dict, organization_id: str) -> NormalizedOrder:
    """Нормализует TableOrderUpdate (зал/вынос) в NormalizedOrder.

    Форма payload до конца не задокументирована, поэтому работаем максимально
    защитно. Принимаем либо «сырое» событие (тогда данные в
    eventInfo.order, а id — в eventInfo.id), либо уже распакованный wrapper
    вида {id, order}. Для зала адрес/координаты не нужны.

    Логика извлечения позиций повторяет normalize_full_order: берём product
    {id, name, category}, amount и combo. Тип обслуживания
    (orderType.orderServiceType) определяет зал/вынос; при отсутствии — "DineIn".
    """
    src = event_or_wrapper or {}
    # Если это сырое событие — спускаемся в eventInfo.
    info = src.get("eventInfo")
    if isinstance(info, dict):
        wrapper = info
    else:
        wrapper = src

    o = wrapper.get("order") or {}

    items: list[WebhookItem] = []
    for it in (o.get("items") or []):
        if not isinstance(it, dict):
            continue
        product = it.get("product") or {}
        if not product.get("id"):
            continue
        combo = it.get("comboInformation") or {}
        category = product.get("category") or {}
        items.append(WebhookItem(
            product_id=str(product["id"]),
            category_id=(str(category.get("id")) if category.get("id") else None),
            name=product.get("name", "") or "",
            amount=float(it.get("amount", 1) or 1),
            combo_id=(str(combo.get("comboId")) if combo.get("comboId") else None),
        ))

    # iiko_order_id: eventInfo.id -> order.id -> order.number (что есть первым).
    iiko_order_id = wrapper.get("id") or o.get("id") or o.get("number") or ""

    customer = (o.get("customer") or {}).get("name", "") or ""
    service_type = (o.get("orderType") or {}).get("orderServiceType") or "DineIn"

    return NormalizedOrder(
        iiko_order_id=str(iiko_order_id),
        org_id=organization_id,
        number=str(o.get("number") or ""),
        status=str(o.get("status") or ""),
        customer=customer,
        phone=o.get("phone", "") or "",
        address="",
        lat=None,
        lon=None,
        total=o.get("sum"),
        service_type=service_type,
        items=items,
    )


async def update_webhook_settings(organization_id: str, web_hooks_uri: str,
                                  auth_token: str | None = None) -> dict:
    token = await get_token()
    body = {
        "organizationId": organization_id,
        "webHooksUri": web_hooks_uri,
        "webHooksFilter": {
            "deliveryOrderFilter": {
                "orderStatuses": DELIVERY_ORDER_STATUSES,
                "itemStatuses": ["Added", "CookingStarted", "CookingCompleted", "Served"],
                "errors": True,
            }
        },
    }
    if auth_token:
        body["authToken"] = auth_token
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{BASE}/api/1/webhooks/update_settings",
                              headers={"Authorization": f"Bearer {token}"}, json=body)
        r.raise_for_status()
        return r.json() if r.text.strip() else {"ok": True}
