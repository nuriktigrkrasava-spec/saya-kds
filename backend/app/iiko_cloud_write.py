from __future__ import annotations
import time
import httpx
from app.iiko_cloud import BASE, get_token
from app.rms import _norm_name


async def _post(path: str, body: dict) -> tuple[bool, dict]:
    """POST в iiko Transport API. Возвращает (ok, detail). НЕ бросает на ошибку iiko."""
    token = await get_token()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/api/1/{path}",
                         headers={"Authorization": f"Bearer {token}"},
                         json=body)
    try:
        detail = r.json()
    except Exception:  # noqa: BLE001 — не-JSON ответ
        detail = {"raw": r.text}
    return r.is_success, detail


async def assign_courier(org_id: str, order_id: str, employee_id: str) -> tuple[bool, dict]:
    return await _post("deliveries/update_order_courier",
                       {"organizationId": org_id, "orderId": order_id,
                        "employeeId": employee_id})


async def set_payments(org_id: str, order_id: str, payment_items: list[dict]) -> tuple[bool, dict]:
    return await _post("deliveries/update_order_payments",
                       {"organizationId": org_id, "orderId": order_id,
                        "paymentItems": payment_items})


async def close_order(org_id: str, order_id: str) -> tuple[bool, dict]:
    return await _post("deliveries/close",
                       {"organizationId": org_id, "orderId": order_id})


_COURIER_TTL = 300.0  # сек

_COURIER_CACHE: dict = {}
_PAY_CACHE: dict = {}


def reset_caches() -> None:
    """Сбросить модульные кэши (для тестов)."""
    _COURIER_CACHE.clear()
    _PAY_CACHE.clear()


async def _fetch_courier_index(org_id: str) -> dict[str, str]:
    token = await get_token()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/api/1/employees/couriers",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"organizationIds": [org_id]})
    if not r.is_success:
        return {}
    index: dict[str, str] = {}
    for blk in (r.json().get("employees") or []):
        for it in (blk.get("items") or []):
            name = (it.get("displayName") or
                    f"{it.get('firstName', '')} {it.get('lastName', '')}").strip()
            if name and it.get("id"):
                index[_norm_name(name)] = it["id"]
    return index


async def cloud_courier_id(org_id: str, courier_name: str) -> str | None:
    if not org_id or not courier_name:
        return None
    now = time.monotonic()
    cached = _COURIER_CACHE.get(org_id)
    if cached and (now - cached[0]) < _COURIER_TTL:
        index = cached[1]
    else:
        index = await _fetch_courier_index(org_id)
        _COURIER_CACHE[org_id] = (now, index)
    return index.get(_norm_name(courier_name))
