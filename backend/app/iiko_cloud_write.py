from __future__ import annotations
import httpx
from app.iiko_cloud import BASE, get_token


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


def reset_caches() -> None:
    """Сбросить модульные кэши (для тестов). Заполняется в Task 3/4."""
    _COURIER_CACHE.clear()
    _PAY_CACHE.clear()


_COURIER_CACHE: dict = {}
_PAY_CACHE: dict = {}
