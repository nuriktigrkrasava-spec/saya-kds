# iiko Write-back Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сборщик назначает курьера, а курьер закрывает заказ с типом оплаты — и оба действия пишутся обратно в iiko Cloud (назначение курьера + оплата + закрытие).

**Architecture:** Новый модуль `app/iiko_cloud_write.py` делает POST-запросы в iiko Transport API (переиспользуя `iiko_cloud.get_token()`). Эндпоинты `assign-courier` (п.2) и `delivered` (п.3) после локальной записи дёргают эти функции; результат идёт в `order_events` (аудит) и в новое поле `Order.iiko_sync_error`. Локальная доска не зависит от iiko: локальный статус ставится всегда, ошибка iiko только помечается.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, httpx, pytest-asyncio, respx (мок HTTP).

## Global Constraints

- Работаем **только с Cloud-заказами доставки**: вызовы iiko делаем лишь когда у заказа есть `org_id`. Заказы вне Cloud не трогаем.
- Имена полей iiko (проверены спайком): курьер — **`employeeId`** (не `courierId`); оплата — **`paymentItems`** (не `payments`).
- Метод `change_delivery_status` ключу **запрещён** — не использовать. Статусы готов/в пути/доставлен в iiko НЕ зеркалим.
- Локальная доска **никогда** не должна падать из-за ошибки iiko: эндпоинт всегда возвращает 200 и пишет локальный статус; ошибка iiko уходит только в `iiko_sync_error` + `order_events`.
- Base URL и токен берём из существующего `app/iiko_cloud.py` (`BASE`, `get_token`).
- Тесты — `respx.mock`, мокаем и `POST /api/1/access_token`, и целевой метод. Каждый юнит-тест использует **уникальный `org_id`** (кэши модульного уровня), либо вызывает `iiko_cloud_write.reset_caches()`.

---

### Task 1: Поле `Order.iiko_sync_error` + миграция + вывод в payload

**Files:**
- Modify: `backend/app/models.py:27-49` (класс `Order`)
- Modify: `backend/app/main.py:21-24` (`_ADDED_COLUMNS`)
- Modify: `backend/app/realtime.py:19-36` (`order_payload`)
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces: `Order.iiko_sync_error: str | None` — текст последней ошибки записи в iiko (NULL = синхронно). `order_payload(order)["iiko_sync_error"]`.

- [ ] **Step 1: Написать падающий тест**

В `backend/tests/test_models.py` добавить:

```python
async def test_order_has_iiko_sync_error_default_none(session):
    from app.models import Order
    o = Order(iiko_order_id="x1", org_id="org-1", number="1")
    session.add(o); await session.commit()
    assert o.iiko_sync_error is None
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_models.py::test_order_has_iiko_sync_error_default_none -v`
Expected: FAIL — `TypeError`/`AttributeError` (нет атрибута `iiko_sync_error`).

- [ ] **Step 3: Добавить поле в модель**

В `backend/app/models.py`, в класс `Order` после строки `assigned_courier_name` (≈:47) добавить:

```python
    iiko_sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_models.py::test_order_has_iiko_sync_error_default_none -v`
Expected: PASS

- [ ] **Step 5: Прод-миграция и вывод в payload**

В `backend/app/main.py` в список `_ADDED_COLUMNS` добавить строку:

```python
    ("orders", "iiko_sync_error", "VARCHAR"),
```

В `backend/app/realtime.py` в `order_payload`, после `"delivered": order.delivered_at is not None,` (≈:36) добавить:

```python
        "iiko_sync_error": order.iiko_sync_error,
```

- [ ] **Step 6: Прогнать затронутые тесты**

Run: `cd backend && .venv/bin/pytest tests/test_models.py tests/test_order_actions.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/app/main.py backend/app/realtime.py backend/tests/test_models.py
git commit -m "feat(orders): поле iiko_sync_error + миграция + вывод в payload"
```

---

### Task 2: Модуль записи в iiko — assign_courier / set_payments / close_order

**Files:**
- Create: `backend/app/iiko_cloud_write.py`
- Test: `backend/tests/test_iiko_cloud_write.py`

**Interfaces:**
- Consumes: `app.iiko_cloud.BASE`, `app.iiko_cloud.get_token`
- Produces:
  - `async assign_courier(org_id: str, order_id: str, employee_id: str) -> tuple[bool, dict]`
  - `async set_payments(org_id: str, order_id: str, payment_items: list[dict]) -> tuple[bool, dict]`
  - `async close_order(org_id: str, order_id: str) -> tuple[bool, dict]`
  - `reset_caches() -> None`

- [ ] **Step 1: Написать падающий тест**

Создать `backend/tests/test_iiko_cloud_write.py`:

```python
import httpx, respx, pytest
from app import iiko_cloud_write as w

BASE = "https://api-ru.iiko.services"


def _token_route():
    return respx.post(f"{BASE}/api/1/access_token").mock(
        return_value=httpx.Response(200, json={"token": "T"}))


@respx.mock
async def test_assign_courier_ok_sends_employee_id():
    _token_route()
    route = respx.post(f"{BASE}/api/1/deliveries/update_order_courier").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    ok, detail = await w.assign_courier("org-1", "ord-1", "emp-1")
    assert ok is True
    body = route.calls.last.request.read()
    assert b'"employeeId"' in body and b'"emp-1"' in body


@respx.mock
async def test_close_order_error_returns_false_with_detail():
    _token_route()
    respx.post(f"{BASE}/api/1/deliveries/close").mock(
        return_value=httpx.Response(500, json={"error": "DELIVERY_DOESNT_EXIST"}))
    ok, detail = await w.close_order("org-1", "ord-1")
    assert ok is False
    assert detail.get("error") == "DELIVERY_DOESNT_EXIST"


@respx.mock
async def test_set_payments_sends_payment_items_key():
    _token_route()
    route = respx.post(f"{BASE}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    ok, _ = await w.set_payments("org-1", "ord-1", [{"paymentTypeKind": "Cash"}])
    assert ok is True
    assert b'"paymentItems"' in route.calls.last.request.read()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_iiko_cloud_write.py -v`
Expected: FAIL — `ModuleNotFoundError: app.iiko_cloud_write`.

- [ ] **Step 3: Написать модуль**

Создать `backend/app/iiko_cloud_write.py`:

```python
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
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_iiko_cloud_write.py -v`
Expected: PASS (3 теста).

- [ ] **Step 5: Commit**

```bash
git add backend/app/iiko_cloud_write.py backend/tests/test_iiko_cloud_write.py
git commit -m "feat(iiko-write): assign_courier/set_payments/close_order + (ok, detail)"
```

---

### Task 3: Связка курьеров RMS↔Cloud по имени (`cloud_courier_id`)

**Files:**
- Modify: `backend/app/iiko_cloud_write.py`
- Test: `backend/tests/test_iiko_cloud_write.py`

**Interfaces:**
- Consumes: `app.rms._norm_name`, `get_token`, `BASE`, `_COURIER_CACHE`
- Produces: `async cloud_courier_id(org_id: str, courier_name: str) -> str | None`

- [ ] **Step 1: Написать падающий тест**

Добавить в `backend/tests/test_iiko_cloud_write.py`:

```python
import time

def _couriers_route():
    return respx.post(f"{BASE}/api/1/employees/couriers").mock(
        return_value=httpx.Response(200, json={"employees": [
            {"organizationId": "org-c", "items": [
                {"id": "emp-42", "displayName": "Адильхан Курьер"}]}]}))


@respx.mock
async def test_cloud_courier_id_matches_by_normalized_name():
    w.reset_caches()
    _token_route(); _couriers_route()
    got = await w.cloud_courier_id("org-c", "адильхан   курьер")
    assert got == "emp-42"


@respx.mock
async def test_cloud_courier_id_none_when_no_match():
    w.reset_caches()
    _token_route(); _couriers_route()
    assert await w.cloud_courier_id("org-c", "Кто-то Другой") is None


@respx.mock
async def test_cloud_courier_id_uses_cache_second_call():
    w.reset_caches()
    _token_route()
    route = _couriers_route()
    await w.cloud_courier_id("org-cache", "Адильхан Курьер")
    await w.cloud_courier_id("org-cache", "Адильхан Курьер")
    assert route.call_count == 1  # второй раз — из кэша
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_iiko_cloud_write.py -k courier -v`
Expected: FAIL — `AttributeError: module 'app.iiko_cloud_write' has no attribute 'cloud_courier_id'`.

- [ ] **Step 3: Реализовать резолвер с кэшем**

В `backend/app/iiko_cloud_write.py` заменить заголовок импорта и добавить функции. Импорт сверху:

```python
import time
from app.rms import _norm_name
```

Заменить `_COURIER_TTL` и добавить:

```python
_COURIER_TTL = 300.0  # сек


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
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_iiko_cloud_write.py -k courier -v`
Expected: PASS (3 теста).

- [ ] **Step 5: Commit**

```bash
git add backend/app/iiko_cloud_write.py backend/tests/test_iiko_cloud_write.py
git commit -m "feat(iiko-write): связка курьеров RMS<->Cloud по имени с кэшем"
```

---

### Task 4: Резолвер типа оплаты (`payment_item`)

**Files:**
- Modify: `backend/app/iiko_cloud_write.py`
- Test: `backend/tests/test_iiko_cloud_write.py`

**Interfaces:**
- Consumes: `get_token`, `BASE`, `_PAY_CACHE`
- Produces: `async payment_item(org_id: str, kind: str, total: float | None) -> dict | None` — элемент для `paymentItems`: `{paymentTypeKind, sum, paymentTypeId, isProcessedExternally}`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `backend/tests/test_iiko_cloud_write.py`:

```python
def _paytypes_route():
    return respx.post(f"{BASE}/api/1/payment_types").mock(
        return_value=httpx.Response(200, json={"paymentTypes": [
            {"id": "pt-cash", "name": "Наличные", "paymentTypeKind": "Cash"},
            {"id": "pt-card", "name": "Карта", "paymentTypeKind": "Card"}]}))


@respx.mock
async def test_payment_item_builds_cash_item():
    w.reset_caches()
    _token_route(); _paytypes_route()
    item = await w.payment_item("org-p", "Cash", 8500)
    assert item == {"paymentTypeKind": "Cash", "sum": 8500,
                    "paymentTypeId": "pt-cash", "isProcessedExternally": True}


@respx.mock
async def test_payment_item_none_for_unknown_kind():
    w.reset_caches()
    _token_route(); _paytypes_route()
    assert await w.payment_item("org-p2", "Bonus", 100) is None
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_iiko_cloud_write.py -k payment -v`
Expected: FAIL — нет атрибута `payment_item`.

- [ ] **Step 3: Реализовать резолвер**

В `backend/app/iiko_cloud_write.py` добавить:

```python
_PAY_TTL = 1800.0  # сек


async def _payment_types(org_id: str) -> dict[str, dict]:
    now = time.monotonic()
    cached = _PAY_CACHE.get(org_id)
    if cached and (now - cached[0]) < _PAY_TTL:
        return cached[1]
    token = await get_token()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/api/1/payment_types",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"organizationIds": [org_id]})
    out: dict[str, dict] = {}
    if r.is_success:
        for pt in (r.json().get("paymentTypes") or []):
            kind = pt.get("paymentTypeKind")
            if kind and kind not in out:
                out[kind] = {"id": pt.get("id"), "name": pt.get("name")}
    _PAY_CACHE[org_id] = (now, out)
    return out


async def payment_item(org_id: str, kind: str, total: float | None) -> dict | None:
    pt = (await _payment_types(org_id)).get(kind)
    if not pt:
        return None
    return {"paymentTypeKind": kind, "sum": total or 0,
            "paymentTypeId": pt["id"], "isProcessedExternally": True}
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_iiko_cloud_write.py -k payment -v`
Expected: PASS (2 теста).

- [ ] **Step 5: Commit**

```bash
git add backend/app/iiko_cloud_write.py backend/tests/test_iiko_cloud_write.py
git commit -m "feat(iiko-write): резолвер типа оплаты (payment_types -> paymentItems)"
```

---

### Task 5: Хелпер аудита `_log_iiko_event`

**Files:**
- Modify: `backend/app/api.py` (рядом с `_order_action_and_broadcast`, ≈:363)
- Test: `backend/tests/test_iiko_write_events.py`

**Interfaces:**
- Consumes: `app.models.OrderEvent`
- Produces: `async _log_iiko_event(session, order_id: int, method: str, ok: bool, detail: dict) -> None` — пишет строку `OrderEvent(type="iiko_write", payload={method, ok, detail})`.

- [ ] **Step 1: Написать падающий тест**

Создать `backend/tests/test_iiko_write_events.py`:

```python
from sqlalchemy import select
from app.models import Order, OrderEvent


async def test_log_iiko_event_writes_row(session):
    from app.api import _log_iiko_event
    o = Order(iiko_order_id="o1", org_id="org-1", number="1")
    session.add(o); await session.commit()
    await _log_iiko_event(session, o.id, "update_order_courier", True, {"correlationId": "c1"})
    ev = (await session.execute(select(OrderEvent))).scalars().all()
    assert len(ev) == 1
    assert ev[0].type == "iiko_write"
    assert ev[0].payload["method"] == "update_order_courier"
    assert ev[0].payload["ok"] is True
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_iiko_write_events.py -v`
Expected: FAIL — `ImportError: cannot import name '_log_iiko_event'`.

- [ ] **Step 3: Реализовать хелпер**

В `backend/app/api.py` найти импорты моделей (вверху файла рядом с `from app.models import ...`) и убедиться, что `OrderEvent` импортирован; если нет — добавить:

```python
from app.models import OrderEvent as _OrderEvent
```

После функции `_order_action_and_broadcast` (≈:367) добавить:

```python
async def _log_iiko_event(session, order_id: int, method: str,
                          ok: bool, detail: dict) -> None:
    """Аудит вызова записи в iiko в order_events (материал и для аналитики п.4)."""
    session.add(_OrderEvent(order_id=order_id, type="iiko_write",
                            payload={"method": method, "ok": ok, "detail": detail}))
    await session.commit()
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_iiko_write_events.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api.py backend/tests/test_iiko_write_events.py
git commit -m "feat(iiko-write): хелпер аудита _log_iiko_event в order_events"
```

---

### Task 6: п.2 — `assign-courier` пишет курьера в iiko

**Files:**
- Modify: `backend/app/api.py:911-923` (`order_assign_courier`)
- Test: `backend/tests/test_order_actions.py`

**Interfaces:**
- Consumes: `iiko_cloud_write.cloud_courier_id`, `iiko_cloud_write.assign_courier`, `_log_iiko_event`
- Produces: при успехе `order.iiko_sync_error is None`; при несовпадении курьера — `"courier_not_matched"`; при ошибке iiko — текст ошибки. Эндпоинт всегда 200.

- [ ] **Step 1: Написать падающие тесты**

В `backend/tests/test_order_actions.py` добавить (вверху файла уже есть `import respx, httpx`; если `httpx` не импортирован — добавить):

```python
import httpx
from app.models import Order

_IIKO = "https://api-ru.iiko.services"


def _token():
    respx.post(f"{_IIKO}/api/1/access_token").mock(
        return_value=httpx.Response(200, json={"token": "T"}))


@respx.mock
async def test_assign_courier_success_syncs_to_iiko(client):
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/employees/couriers").mock(
        return_value=httpx.Response(200, json={"employees": [
            {"organizationId": "org-1", "items": [{"id": "emp-9", "displayName": "Иван Курьер"}]}]}))
    upd = respx.post(f"{_IIKO}/api/1/deliveries/update_order_courier").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/assign-courier",
                     json={"courier_code": "K1", "courier_name": "Иван Курьер"})
    assert r.status_code == 200
    assert upd.called
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_assign_courier_unmatched_sets_sync_error(client):
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/employees/couriers").mock(
        return_value=httpx.Response(200, json={"employees": []}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/assign-courier",
                     json={"courier_code": "K1", "courier_name": "Нет В Cloud"})
    assert r.status_code == 200
    assert r.json()["iiko_sync_error"] == "courier_not_matched"
    # локальное назначение всё равно сохранено
    async with maker() as s:
        o = (await s.execute(select(Order).where(Order.id == oid))).scalar_one()
        assert o.assigned_courier_name == "Нет В Cloud"
```

Примечание: в фикстуре `client` (test_order_actions.py:11) у seed-заказа `org_id="org-1"` — он непустой, значит ветка записи в iiko сработает.

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_order_actions.py -k assign_courier -v`
Expected: FAIL — `upd.called` ложно / ключа `iiko_sync_error` нет (логика iiko ещё не подключена).

- [ ] **Step 3: Подключить запись в эндпоинте**

В `backend/app/api.py` добавить импорт модуля (рядом с другими `from app import ...`):

```python
from app import iiko_cloud_write as _iiko_write
```

Заменить тело `order_assign_courier` (строки 914-923) на:

```python
    order = await _load_order(session, order_id)
    if order.assembled_at is None:
        order.assembled_at = _dt.utcnow()
    order.assigned_courier_code = body.courier_code
    order.assigned_courier_name = body.courier_name

    ok, detail, method = True, {}, "update_order_courier"
    if order.org_id:
        emp_id = await _iiko_write.cloud_courier_id(order.org_id, body.courier_name)
        if emp_id:
            ok, detail = await _iiko_write.assign_courier(
                order.org_id, order.iiko_order_id, emp_id)
            order.iiko_sync_error = None if ok else str(detail)[:500]
        else:
            ok, detail = False, {"error": "courier_not_matched"}
            order.iiko_sync_error = "courier_not_matched"

    await session.commit()
    if order.org_id:
        await _log_iiko_event(session, order_id, method, ok, detail)
    order = await _load_order(session, order_id)
    await realtime.broadcast_order(order)
    return realtime.order_payload(order)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_order_actions.py -k assign_courier -v`
Expected: PASS (2 теста).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api.py backend/tests/test_order_actions.py
git commit -m "feat(orders): assign-courier зеркалит курьера в iiko (п.2)"
```

---

### Task 7: п.3 — `delivered` закрывает заказ в iiko с типом оплаты

**Files:**
- Modify: `backend/app/api.py:283-285` (`ActionIn`) и `:388-394` (`order_delivered`)
- Test: `backend/tests/test_order_actions.py`

**Interfaces:**
- Consumes: `iiko_cloud_write.payment_item`, `iiko_cloud_write.set_payments`, `iiko_cloud_write.close_order`, `_log_iiko_event`
- Produces: `ActionIn.payment_kind: str | None`. При `payment_kind` и успехе `iiko_sync_error is None`; при отсутствии типа оплаты — `"payment_type_not_found"`. Эндпоинт всегда 200; без `payment_kind` поведение прежнее.

- [ ] **Step 1: Написать падающие тесты**

В `backend/tests/test_order_actions.py` добавить:

```python
@respx.mock
async def test_delivered_with_payment_closes_in_iiko(client):
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/payment_types").mock(
        return_value=httpx.Response(200, json={"paymentTypes": [
            {"id": "pt-cash", "name": "Наличные", "paymentTypeKind": "Cash"}]}))
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    close = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_kind": "Cash"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True
    assert pay.called and close.called
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_delivered_unknown_payment_sets_sync_error(client):
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/payment_types").mock(
        return_value=httpx.Response(200, json={"paymentTypes": []}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_kind": "Cash"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True
    assert r.json()["iiko_sync_error"] == "payment_type_not_found"


@respx.mock
async def test_delivered_without_payment_kind_unchanged(client):
    c, maker = client
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()
    r = await c.post(f"/orders/{oid}/delivered", json={"by": "Курьер"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True  # без iiko-вызовов
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_order_actions.py -k delivered -v`
Expected: FAIL — `payment_kind` не принимается / `pay.called` ложно.

- [ ] **Step 3: Расширить `ActionIn` и `order_delivered`**

В `backend/app/api.py` заменить класс `ActionIn` (строки 283-284) на:

```python
class ActionIn(BaseModel):
    by: str = ""
    payment_kind: str | None = None  # "Cash"|"Card" — закрытие заказа в iiko (курьер)
```

Заменить тело `order_delivered` (строки 391-394) на:

```python
    order = await _load_order(session, order_id)
    order.delivered_at = _dt.utcnow()
    if body.payment_kind and order.org_id:
        item = await _iiko_write.payment_item(order.org_id, body.payment_kind, order.total)
        if item:
            ok, detail = await _iiko_write.set_payments(
                order.org_id, order.iiko_order_id, [item])
            if ok:
                ok, detail = await _iiko_write.close_order(
                    order.org_id, order.iiko_order_id)
        else:
            ok, detail = False, {"error": "payment_type_not_found"}
        order.iiko_sync_error = None if ok else (
            "payment_type_not_found" if detail.get("error") == "payment_type_not_found"
            else str(detail)[:500])
        await session.commit()
        await _log_iiko_event(session, order_id, "close", ok, detail)
    return await _order_action_and_broadcast(session, order_id)
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_order_actions.py -k delivered -v`
Expected: PASS (3 теста).

- [ ] **Step 5: Прогнать весь backend**

Run: `cd backend && .venv/bin/pytest -q`
Expected: PASS (вся сюита зелёная).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api.py backend/tests/test_order_actions.py
git commit -m "feat(orders): delivered закрывает заказ в iiko с типом оплаты (п.3)"
```

---

### Task 8: Фронт — выбор типа оплаты у курьера + бейдж рассинхрона

**Files:**
- Modify: фронт мини-аппа курьера (компонент кнопки «Доставлен») — отправлять `payment_kind`
- Modify: фронт доски/админки — показывать бейдж при `iiko_sync_error`

**Interfaces:**
- Consumes: ответ эндпоинтов теперь содержит `iiko_sync_error`; `POST /orders/{id}/delivered` принимает `{by, payment_kind}`.

> Точные пути фронта определяет исполнитель по структуре `frontend/` (найти текущий вызов `delivered` и список заказов). Логика: на «Доставлен» показать выбор «Наличные / Карта» → слать `payment_kind: "Cash"|"Card"`; в списке заказов при `iiko_sync_error != null` показать значок «не ушло в iiko».

- [ ] **Step 1: Найти текущий вызов `delivered` во фронте**

Run: `cd frontend && grep -rn "delivered\|assign-courier\|iiko_sync_error" src`
Expected: найден компонент курьера, дёргающий `/orders/{id}/delivered`.

- [ ] **Step 2: Добавить выбор типа оплаты**

В компоненте курьера заменить прямой вызов «Доставлен» на выбор оплаты перед отправкой; в теле запроса добавить `payment_kind`. (Код — по месту, следуя стилю существующих вызовов fetch/axios в `frontend/src`.)

- [ ] **Step 3: Добавить бейдж рассинхрона в списке заказов**

Там, где рендерится карточка заказа, при `order.iiko_sync_error` показать значок-предупреждение «не ушло в iiko».

- [ ] **Step 4: Проверить вручную в браузере**

Run: запустить фронт (`/run`) и пройти курьерский путь: «Доставлен → Наличные».
Expected: запрос уходит с `payment_kind`, заказ помечается доставленным; при ошибке iiko — виден бейдж.

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat(front): выбор типа оплаты у курьера + бейдж рассинхрона с iiko"
```

---

### Task 9: Ручная проверка риска §5 на живом заказе (перед выкаткой)

**Не код.** На одном **реальном тестовом delivery-заказе** в Cloud:

- [ ] Назначить курьера через приложение → проверить, что курьер появился в iiko.
- [ ] Закрыть заказ с типом оплаты → проверить, что `deliveries/close` прошёл **напрямую** из текущего статуса iiko (заказ не пришлось вручную переводить в Delivered).
- [ ] Если `close` отбивается из-за статуса — зафиксировать ошибку и поднять вопрос включения права `change_delivery_status` в iikoWeb (тогда добавляем шаг смены статуса перед close).

---

## Self-Review

**Spec coverage:**
- §3.1 модуль записи → Task 2. §3.2 связка курьеров → Task 3. §3.3 типы оплаты → Task 4. §3.4 аудит/устойчивость → Task 5 + флаг в Task 1, обработка ошибок в Task 6/7. §4.1 поле модели → Task 1. §4.2 п.2 → Task 6. §4.3 п.3 → Task 7. §4.4 фронт → Task 8. §5 риск → Task 9. §7 тесты → распределены по задачам. Покрыто всё.

**Placeholder scan:** Код-шаги содержат полный код. Task 8 (фронт) намеренно описателен по путям, т.к. структура `frontend/` не зафиксирована в спеке — но действия и контракт заданы явно (поля `payment_kind`, `iiko_sync_error`).

**Type consistency:** `assign_courier/set_payments/close_order` возвращают `(bool, dict)` везде; `cloud_courier_id -> str|None`; `payment_item -> dict|None`; `_log_iiko_event(session, order_id, method, ok, detail)` — сигнатура совпадает в Task 5/6/7. Поля iiko `employeeId`/`paymentItems` едины. `reset_caches()` объявлена в Task 2, используется в тестах Task 3/4/6/7.
