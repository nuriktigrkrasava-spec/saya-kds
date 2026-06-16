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
