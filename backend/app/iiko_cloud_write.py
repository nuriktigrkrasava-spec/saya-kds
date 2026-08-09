from __future__ import annotations
import asyncio
import time
import httpx
from app import iiko_cloud
from app.iiko_cloud import BASE, get_token
from app.rms import _norm_name

# Команды deliveries/* в iiko АСИНХРОННЫЕ: 200 приходит сразу и означает лишь
# «принято в очередь», а выполняется команда позже. Раньше мы слали оплату,
# статус и close подряд, не дожидаясь исполнения, — close успевал обогнать
# оплату, и iiko по своему правилу печатал чек, но оставлял доставку ОТКРЫТОЙ.
# Ошибок при этом не было нигде: все три вызова вернули 200.
COMMAND_TIMEOUT_SECONDS = 15.0
COMMAND_POLL_SECONDS = 0.4

# Ответы commands/status, после которых ждать бессмысленно: команды нет в
# хранилище iiko (410), значит она уже не выполнится. Раньше мы такой ответ не
# распознавали и висели весь таймаут, заставляя курьера ждать впустую.
COMMAND_TERMINAL_ERRORS = {"PASSED_COMMAND_NOT_FOUND"}


async def _post(path: str, body: dict) -> tuple[bool, dict]:
    """POST в iiko Transport API. Возвращает (ok, detail). НЕ бросает на ошибку iiko."""
    try:
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
    except Exception as e:  # noqa: BLE001 — сеть / токен
        return False, {"error": str(e)[:500]}


async def _await_command(org_id: str, ok: bool, detail: dict) -> tuple[bool, dict]:
    """Дожидается фактического выполнения асинхронной команды iiko.

    Успех — только состояние Success из commands/status. Ответ без
    correlationId считаем синхронным и возвращаем как есть."""
    if not ok:
        return ok, detail
    correlation_id = (detail or {}).get("correlationId")
    if not correlation_id:
        return ok, detail

    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    last: dict = {}
    while True:
        got, status = await _post("commands/status",
                                  {"organizationId": org_id,
                                   "correlationId": correlation_id})
        if got:
            last = status
            state = (status or {}).get("state") or ""
            if state == "Success":
                return True, status
            if state == "Error":
                return False, status
        elif (status or {}).get("error") in COMMAND_TERMINAL_ERRORS:
            # Команды нет в хранилище — она уже не выполнится, ждать нечего.
            return False, status
        if time.monotonic() >= deadline:
            return False, {"error": "command_timeout",
                           "correlationId": correlation_id, "last": last}
        await asyncio.sleep(COMMAND_POLL_SECONDS)


async def assign_courier(org_id: str, order_id: str, employee_id: str) -> tuple[bool, dict]:
    return await _await_command(org_id, *(await _post(
        "deliveries/update_order_courier",
        {"organizationId": org_id, "orderId": order_id,
         "employeeId": employee_id})))


async def set_payments(org_id: str, order_id: str, payment_items: list[dict]) -> tuple[bool, dict]:
    return await _await_command(org_id, *(await _post(
        "deliveries/update_order_payments",
        {"organizationId": org_id, "orderId": order_id,
         "paymentItems": payment_items})))


async def close_order(org_id: str, order_id: str) -> tuple[bool, dict]:
    return await _await_command(org_id, *(await _post(
        "deliveries/close",
        {"organizationId": org_id, "orderId": order_id})))


async def set_delivery_status(org_id: str, order_id: str, status: str) -> tuple[bool, dict]:
    return await _await_command(org_id, *(await _post(
        "deliveries/update_order_delivery_status",
        {"organizationId": org_id, "orderId": order_id,
         "deliveryStatus": status})))


async def read_order_state(org_id: str, order_id: str) -> dict | None:
    """Что iiko думает о заказе прямо сейчас: сумма, оплачен ли, тип доставки.

    Нужно перед записью, потому что:
    * `update_order_payments` НЕ заменяет оплаты, а добавляет — на уже оплаченном
      заказе получается двойной чек (прод, заказ №91 от 07.08.2026);
    * `update_order_delivery_status` у заказов `DeliveryByClient` (Яндекс/Wolt,
      самовывоз) всегда падает с ошибкой 173 — этапов доставки у них нет.

    Предварительная оплата (`isPreliminary`) — это уже выбранный в заказе тип
    оплаты (Kaspi, наличные), который касса фискализирует при закрытии. Своей
    оплатой поверх неё платить НЕЛЬЗЯ: iiko отбивает такой заказ («Тип „Kaspi
    для QR“ некомбинируемый», прод 09.08.2026, заказ №23) и close не проходит —
    заказ навсегда остаётся в «В пути». Поэтому отдаём её отдельным полем
    `prepaid` вместе с типом оплаты самого заказа: подтверждать предоплату, если
    придётся, можно только ТЕМ ЖЕ типом.

    None — прочитать не удалось, `is_delivery=False` — заказ зала/выноса,
    доставки для него не существует.
    """
    try:
        wrapper = await iiko_cloud.fetch_delivery_by_id(org_id, order_id)
    except Exception:  # noqa: BLE001 — сеть / токен / 5xx
        return None
    if wrapper is None:
        return {"is_delivery": False}
    order = wrapper.get("order") or {}
    total = order.get("sum")
    if total is None:
        return None
    payments = order.get("payments") or []
    paid = sum((p.get("sum") or 0) for p in payments if not p.get("isPreliminary"))
    prepaid = sum((p.get("sum") or 0) for p in payments if p.get("isPreliminary"))
    own = (payments[0].get("paymentType") or {}) if payments else {}
    return {"is_delivery": True,
            "total": total,
            "paid": paid + 0.01 >= total,
            "prepaid": prepaid + 0.01 >= total,
            "payment_type_id": own.get("id"),
            "payment_type_kind": own.get("kind"),
            "service_type": (order.get("orderType") or {}).get("orderServiceType")}


async def list_payment_types(org_id: str) -> list[dict]:
    try:
        token = await get_token()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{BASE}/api/1/payment_types",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"organizationIds": [org_id]})
        if not r.is_success:
            return []
        return [{"id": p.get("id"), "name": p.get("name"), "kind": p.get("paymentTypeKind")}
                for p in (r.json().get("paymentTypes") or []) if not p.get("isDeleted")]
    except Exception:  # noqa: BLE001
        return []


def payment_item_by_id(org_id: str, payment_type_id: str, kind: str | None,
                       total: float | None) -> dict:
    return {"paymentTypeId": payment_type_id, "paymentTypeKind": kind or "Card",
            "sum": total or 0, "isProcessedExternally": False}


_COURIER_TTL = 300.0  # сек

_COURIER_CACHE: dict = {}
_PAY_CACHE: dict = {}


def reset_caches() -> None:
    """Сбросить модульные кэши (для тестов)."""
    _COURIER_CACHE.clear()
    _PAY_CACHE.clear()


async def _fetch_courier_index(org_id: str) -> dict[str, str]:
    try:
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
    except Exception:  # noqa: BLE001 — сеть / токен
        return {}


async def cloud_courier_id(org_id: str, courier_name: str) -> str | None:
    if not org_id or not courier_name:
        return None
    now = time.monotonic()
    cached = _COURIER_CACHE.get(org_id)
    if cached and (now - cached[0]) < _COURIER_TTL:
        index = cached[1]
    else:
        index = await _fetch_courier_index(org_id)
        if index:  # кэшируем только непустой результат — провал не замораживает TTL
            _COURIER_CACHE[org_id] = (now, index)
    return index.get(_norm_name(courier_name))


_PAY_TTL = 1800.0  # сек
