from __future__ import annotations
import httpx
from app.config import settings
from app.schemas import NormalizedOrder, WebhookItem

BASE = "https://api-ru.iiko.services"

DELIVERY_ORDER_STATUSES = [
    "WaitCooking", "ReadyForCooking", "CookingStarted", "CookingCompleted",
    "Waiting", "OnWay", "Delivered", "Closed", "Cancelled",
]

# Статусы заказов зала/выноса (TableOrderUpdate). Без них iiko НЕ шлёт зал/вынос/
# самовывоз — а значит они не появляются на планшете сборщика/кухни.
TABLE_ORDER_STATUSES = ["New", "Bill", "Closed", "Deleted"]

ITEM_STATUSES = ["Added", "CookingStarted", "CookingCompleted", "Served"]


async def get_token() -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        # ВАЖНО: путь без /auth/ — именно так ключ работает.
        r = await client.post(f"{BASE}/api/1/access_token",
                              json={"apiLogin": settings.cloud_api_login})
        r.raise_for_status()
        return r.json()["token"]


async def fetch_organizations() -> list[dict]:
    """Список организаций Cloud (для связки филиал RMS ↔ Cloud-ID). [{id, name}]"""
    token = await get_token()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{BASE}/api/1/organizations",
                              headers={"Authorization": f"Bearer {token}"},
                              json={"returnAdditionalInfo": False})
        r.raise_for_status()
        return r.json().get("organizations", [])


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


def _format_address(dp: dict) -> str:
    """Собирает читаемый адрес доставки из deliveryPoint.

    iiko Cloud кладёт адрес по частям (улица, дом, квартира, подъезд, этаж),
    а раньше мы брали только название улицы — курьер видел «Абая» без дома.
    Склеиваем всё, что есть, в одну строку.
    """
    addr = (dp.get("address") or {})
    street = (addr.get("street") or {}).get("name") or addr.get("line1") or ""
    house = addr.get("house") or ""
    building = addr.get("building") or ""

    # Первая часть: «Улица, дом[ к.корпус]».
    head_parts = [p for p in (street, house) if p]
    head = ", ".join(head_parts)
    if building:
        head = f"{head} к.{building}" if head else f"к.{building}"

    # Уточнения: квартира / подъезд / этаж — добавляем подписи, чтобы было понятно.
    extras = []
    if addr.get("flat"):
        extras.append(f"кв. {addr['flat']}")
    if addr.get("entrance"):
        extras.append(f"подъезд {addr['entrance']}")
    if addr.get("floor"):
        extras.append(f"этаж {addr['floor']}")
    if addr.get("doorphone"):
        extras.append(f"домофон {addr['doorphone']}")

    parts = [p for p in (head, *extras) if p]
    address = ", ".join(parts)

    # Комментарий курьеру («во дворе», «не звонить») — отдельной припиской.
    comment = (dp.get("comment") or "").strip()
    if comment:
        address = f"{address} ({comment})" if address else comment
    return address


def normalize_full_order(wrapper: dict, organization_id: str) -> NormalizedOrder:
    o = wrapper.get("order") or {}
    customer = (o.get("customer") or {}).get("name", "")
    dp = o.get("deliveryPoint") or {}
    address = _format_address(dp)
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


def build_webhooks_filter() -> dict:
    """Фильтр подписки на вебхуки: доставка + зал/вынос/самовывоз.

    Вынесено отдельно, чтобы покрыть тестом наличие обоих фильтров — без
    tableOrderFilter iiko не присылает зал/вынос и они не видны на планшете.
    """
    return {
        "deliveryOrderFilter": {
            "orderStatuses": DELIVERY_ORDER_STATUSES,
            "itemStatuses": ITEM_STATUSES,
            "errors": True,
        },
        "tableOrderFilter": {
            "orderStatuses": TABLE_ORDER_STATUSES,
            "itemStatuses": ITEM_STATUSES,
            "errors": True,
        },
    }


async def update_webhook_settings(organization_id: str, web_hooks_uri: str,
                                  auth_token: str | None = None,
                                  token: str | None = None) -> dict:
    token = token or await get_token()
    body = {
        "organizationId": organization_id,
        "webHooksUri": web_hooks_uri,
        "webHooksFilter": build_webhooks_filter(),
    }
    if auth_token:
        body["authToken"] = auth_token
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{BASE}/api/1/webhooks/update_settings",
                              headers={"Authorization": f"Bearer {token}"}, json=body)
        r.raise_for_status()
        return r.json() if r.text.strip() else {"ok": True}


def _public_base_url() -> str:
    """Публичный адрес сервиса для приёма вебхуков.

    На Render автоматически доступен RENDER_EXTERNAL_URL; локально/иначе можно
    задать PUBLIC_BASE_URL. Без него авто-регистрация пропускается.
    """
    import os
    return (os.environ.get("PUBLIC_BASE_URL")
            or os.environ.get("RENDER_EXTERNAL_URL")
            or "").rstrip("/")


async def _register_one(org_id: str, uri: str, token: str) -> str | None:
    """Подписывает одну орг РОВНО одним вызовом update_settings.
    Без retry-бурстов: iiko жёстко рейт-лимитит этот endpoint, а повторы в
    пределах короткого окна только продлевают кулдаун 429. Возвращает None при
    успехе или короткое описание ошибки."""
    try:
        await update_webhook_settings(org_id, uri, token=token)
        return None
    except httpx.HTTPStatusError as e:
        return f"HTTP {e.response.status_code}: {e.response.text[:120]}"
    except Exception as e:  # noqa: BLE001
        return str(e)[:120]


async def register_all_webhooks() -> dict:
    """Пере-подписывает iiko на вебхуки (доставка + зал/вынос) для всех орг.

    Идемпотентно: update_settings перезаписывает фильтры. Вызывается на старте
    сервиса, чтобы новый tableOrderFilter (зал/вынос/самовывоз) применился без
    ручных действий. iiko рейт-лимитит update_settings (429), поэтому между
    вызовами делаем паузу и ретраим с backoff. Один access_token на все вызовы.
    Ошибки по орг возвращаем в ответе — иначе их не видно (app-логи на Render
    не выводятся).
    """
    import asyncio
    import logging
    log = logging.getLogger("saya.webhooks")

    base = _public_base_url()
    if not (base and settings.cloud_api_login and settings.webhook_secret):
        return {"registered": 0, "skipped": True,
                "reason": f"base={bool(base)} login={bool(settings.cloud_api_login)} "
                          f"secret={bool(settings.webhook_secret)}"}

    uri = f"{base}/webhooks/iiko/cloud?key={settings.webhook_secret}"
    token = await get_token()
    orgs = await fetch_organizations()

    # Allowlist: подписываем только нужные филиалы (WEBHOOK_ORG_IDS через запятую).
    # Это и убирает 429 (iiko рейт-лимитит update_settings), и фокусирует на
    # рабочем филиале. Пусто -> подписываем все орг.
    import os
    allow = {x.strip() for x in (os.environ.get("WEBHOOK_ORG_IDS") or "").split(",") if x.strip()}
    if allow:
        orgs = [o for o in orgs if o.get("id") in allow]

    ok = 0
    errors: dict[str, str] = {}
    for i, org in enumerate(orgs):
        org_id = org.get("id")
        if not org_id:
            continue
        if i:
            await asyncio.sleep(1.0)  # троттлинг, чтобы не ловить 429
        err = await _register_one(org_id, uri, token)
        if err is None:
            ok += 1
        else:
            errors[org.get("name") or org_id] = err
    log.info("webhook auto-register: %s/%s orgs", ok, len(orgs))
    result: dict = {"registered": ok, "total": len(orgs)}
    if errors:
        result["errors"] = errors
    return result
