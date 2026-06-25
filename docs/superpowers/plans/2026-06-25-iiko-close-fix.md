# iiko Close Flow Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Заказ реально закрывается в iiko (и на доске Saya) при доставке; курьер выбирает РЕАЛЬНЫЙ тип оплаты филиала; тип оплаты виден на доске.

**Architecture:** Исправляем поток закрытия по докам iiko: `update_order_payments`(правильный тип, `isProcessedExternally=false`) → `update_order_delivery_status=Delivered` → `deliveries/close`. Тип оплаты заказа вытаскиваем из вебхука (`order.payments[].paymentType`) на доску. Правильный эндпоинт статуса — `update_order_delivery_status` (РАЗРЕШЁН ключу; `change_delivery_status` — нет, его не трогаем).

**Tech Stack:** FastAPI, SQLAlchemy async, httpx, pytest+respx; React/Vite.

## Global Constraints (проверено доком iiko + живым пробником 2026-06-25)
- Доставочный заказ закрывается в iiko ТОЛЬКО если оплачен И в статусе `Delivered`. Поток строго: payments → `update_order_delivery_status=Delivered` → `close`.
- Эндпоинт смены статуса = `deliveries/update_order_delivery_status` (body `{organizationId, orderId, deliveryStatus}`), РАЗРЕШЁН ключу. НЕ использовать `change_delivery_status` (запрещён).
- Оплата: слать КОНКРЕТНЫЙ `paymentTypeId` (не «первый по kind»), `isProcessedExternally=false` (чтобы iiko фискализировал — чек). Реальный тип заказа уже есть на заказе (preliminary payment) и может НЕ быть в `/payment_types`.
- Доска НЕ зависит от iiko: эндпоинт всегда 200; ошибки iiko → `iiko_sync_error` + `order_events`.
- Только Cloud-заказы (непустой `org_id`). YAGNI, TDD, частые коммиты.

---

### Task 1: iiko-write — set_delivery_status, payment_item по id, list_payment_types

**Files:** Modify `backend/app/iiko_cloud_write.py`; Test `backend/tests/test_iiko_cloud_write.py`

**Interfaces produced:**
- `async set_delivery_status(org_id, order_id, status: str) -> tuple[bool, dict]` → POST `deliveries/update_order_delivery_status`.
- `async list_payment_types(org_id) -> list[dict]` — `[{id,name,kind}]` не удалённые.
- `payment_item_by_id(org_id, payment_type_id, kind, total) -> dict` — `{paymentTypeId, paymentTypeKind, sum, isProcessedExternally: False}` (kind можно передать; если пуст — резолвить из `/payment_types` по id, иначе "Card").

- [ ] **Step 1: Failing tests** — в `test_iiko_cloud_write.py`:
```python
@respx.mock
async def test_set_delivery_status_ok():
    _token_route()
    route = respx.post(f"{BASE}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={}))
    ok, _ = await w.set_delivery_status("org-1", "ord-1", "Delivered")
    assert ok is True
    body = route.calls.last.request.read()
    assert b'"deliveryStatus"' in body and b'"Delivered"' in body

@respx.mock
async def test_payment_item_by_id_externally_false():
    item = w.payment_item_by_id("org-x", "pt-1", "Card", 6990)
    assert item == {"paymentTypeId": "pt-1", "paymentTypeKind": "Card",
                    "sum": 6990, "isProcessedExternally": False}
```
- [ ] **Step 2: Run, expect FAIL** — `cd backend && .venv/bin/pytest tests/test_iiko_cloud_write.py -k "delivery_status or by_id" -v`
- [ ] **Step 3: Implement** — добавить в `iiko_cloud_write.py`:
```python
async def set_delivery_status(org_id: str, order_id: str, status: str) -> tuple[bool, dict]:
    return await _post("deliveries/update_order_delivery_status",
                       {"organizationId": org_id, "orderId": order_id,
                        "deliveryStatus": status})


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
```
(Старые `payment_item`/`_payment_types` оставить — их использование уберём в Task 3.)
- [ ] **Step 4: Run, expect PASS**; затем весь файл `tests/test_iiko_cloud_write.py`.
- [ ] **Step 5: Commit** — `git commit -m "feat(iiko-write): update_order_delivery_status + payment по id (externally=false)"`

---

### Task 2: Вытащить тип оплаты заказа из вебхука на доску

**Files:** Modify `backend/app/schemas.py` (NormalizedOrder), `backend/app/iiko_cloud.py` (обе normalize-функции), `backend/app/models.py` (Order), `backend/app/main.py` (_ADDED_COLUMNS), `backend/app/ingest.py`, `backend/app/realtime.py` (order_payload). Test `backend/tests/test_iiko_cloud.py` или `test_ingest.py`.

**Interfaces produced:** `Order.payment_type_id/payment_type_kind/payment_type_name` (nullable); `order_payload(...)["payment_type_name"]`.

- [ ] **Step 1: Failing test** — нормализация delivery с `payments` извлекает тип:
```python
def test_normalize_extracts_payment_type():
    from app.iiko_cloud import normalize_full_order
    wrapper = {"id": "o1", "order": {"number": "5", "sum": 6990, "payments": [
        {"paymentType": {"id": "pt-1", "name": "Терминал Kaspi", "kind": "Card"}, "sum": 6990}]}}
    n = normalize_full_order(wrapper, "org-1")
    assert n.payment_type_name == "Терминал Kaspi"
    assert n.payment_type_id == "pt-1" and n.payment_type_kind == "Card"
```
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement:**
  - `schemas.py` NormalizedOrder: добавить `payment_type_id: str|None=None`, `payment_type_kind: str|None=None`, `payment_type_name: str|None=None`.
  - В `normalize_full_order` и `normalize_table_order` перед `return`:
```python
    pays = o.get("payments") or []
    pt = (pays[0].get("paymentType") or {}) if pays else {}
```
    и в `NormalizedOrder(...)` добавить `payment_type_id=(str(pt["id"]) if pt.get("id") else None), payment_type_kind=pt.get("kind"), payment_type_name=(pt.get("name") or None)`.
  - `models.py` Order: `payment_type_id/payment_type_kind/payment_type_name` Mapped[str|None] nullable.
  - `main.py` `_ADDED_COLUMNS`: три `("orders","payment_type_id","VARCHAR")` и т.д.
  - `ingest.py`: при создании/обновлении Order проставлять три поля из NormalizedOrder (найти, где маппятся поля заказа, и добавить по аналогии).
  - `realtime.py` order_payload: добавить `"payment_type_name": order.payment_type_name`.
- [ ] **Step 4: Run, expect PASS**; затем `tests/test_iiko_cloud.py tests/test_ingest.py tests/test_order_actions.py -q`.
- [ ] **Step 5: Commit** — `git commit -m "feat(orders): тип оплаты заказа из вебхука -> модель + payload (на доску)"`

---

### Task 3: delivered — payment → Delivered → close (правильным потоком)

**Files:** Modify `backend/app/api.py` (`ActionIn`, `order_delivered`); Test `backend/tests/test_order_actions.py`

**Interfaces:** `ActionIn.payment_type_id: str|None`, `ActionIn.payment_kind: str|None` (kind для тела). Поток: payment(by id) → set_delivery_status(Delivered) → close; `iiko_sync_error` = текст первой ошибки или None.

- [ ] **Step 1: Failing tests** — в `test_order_actions.py`:
```python
@respx.mock
async def test_delivered_full_close_flow(client):
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(return_value=httpx.Response(200, json={}))
    st = respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(return_value=httpx.Response(200, json={}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()
    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_type_id": "pt-1", "payment_kind": "Card"})
    assert r.status_code == 200 and r.json()["delivered"] is True
    assert pay.called and st.called and cl.called
    body = st.calls.last.request.read()
    assert b'"Delivered"' in body
    assert r.json()["iiko_sync_error"] is None

@respx.mock
async def test_delivered_status_fail_sets_error_no_close(client):
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(return_value=httpx.Response(500, json={"error":"BOOM"}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()
    r = await c.post(f"/orders/{oid}/delivered", json={"by":"Курьер","payment_type_id":"pt-1","payment_kind":"Card"})
    assert r.status_code == 200 and r.json()["delivered"] is True
    assert not cl.called  # close не зовём, если Delivered не проставился
    assert r.json()["iiko_sync_error"]
```
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — `ActionIn` добавить `payment_type_id: str | None = None` (оставить `payment_kind`). Тело `order_delivered` (i/o блока iiko) переписать:
```python
    order = await _load_order(session, order_id)
    order.delivered_at = _dt.utcnow()
    await _log_action(session, order, "delivered", actor_name=body.by)
    pt_id = body.payment_type_id or order.payment_type_id
    if pt_id and order.org_id:
        kind = body.payment_kind or order.payment_type_kind
        item = _iiko_write.payment_item_by_id(order.org_id, pt_id, kind, order.total)
        ok, detail = await _iiko_write.set_payments(order.org_id, order.iiko_order_id, [item])
        if ok:
            ok, detail = await _iiko_write.set_delivery_status(order.org_id, order.iiko_order_id, "Delivered")
        if ok:
            ok, detail = await _iiko_write.close_order(order.org_id, order.iiko_order_id)
        order.iiko_sync_error = None if ok else str(detail)[:500]
        await session.commit()
        await _log_iiko_event(session, order, "close", ok, detail)
    return await _order_action_and_broadcast(session, order_id)
```
- [ ] **Step 4: Run, expect PASS**; затем ВЕСЬ backend `.venv/bin/pytest -q` (0 failed; учти, что старые тесты delivered с `payment_kind` могли полагаться на прежний поток — обнови их под новый поток payment→Delivered→close, НЕ ослабляя; payment_kind без payment_type_id и без order.payment_type_id → iiko не зовём).
- [ ] **Step 5: Commit** — `git commit -m "fix(orders): закрытие доставки payment->Delivered->close (баг: чек был, заказ не закрывался)"`

---

### Task 4: Эндпоинт списка типов оплаты для UI курьера

**Files:** Modify `backend/app/api.py`; Test `backend/tests/test_order_actions.py` (или новый)

**Interfaces:** `GET /payment-types?org_id` → `list_payment_types(org_id)`.

- [ ] **Step 1: Failing test:**
```python
@respx.mock
async def test_payment_types_endpoint(client):
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, _ = client; _token()
    respx.post(f"{_IIKO}/api/1/payment_types").mock(return_value=httpx.Response(200, json={"paymentTypes":[
        {"id":"pt-1","name":"Наличные","paymentTypeKind":"Cash","isDeleted":False},
        {"id":"pt-2","name":"Glovo","paymentTypeKind":"Card","isDeleted":True}]}))
    r = await c.get("/payment-types?org_id=org-1")
    assert r.status_code == 200
    rows = r.json()
    assert {"id":"pt-1","name":"Наличные","kind":"Cash"} in rows
    assert all(x["id"] != "pt-2" for x in rows)  # удалённые отфильтрованы
```
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement:**
```python
@app.get("/payment-types")
async def payment_types(org_id: str):
    return await _iiko_write.list_payment_types(org_id)
```
- [ ] **Step 4: Run, expect PASS**; весь backend зелёный.
- [ ] **Step 5: Commit** — `git commit -m "feat(api): /payment-types — реальные типы оплаты филиала для курьера"`

---

### Task 5: Фронт — тип оплаты на доске + реальный список у курьера

**Files:** Modify `frontend/src/lib/api.js`, `frontend/src/screens/TgCourier.jsx`, `frontend/src/screens/Assembler.jsx` (и Courier-карточки, где показываются заказы). Verify: `npm run build`.

**Interfaces consumed:** `GET /payment-types?org_id`; `order.payment_type_name` в payload; `POST /orders/{id}/delivered` body `{by, payment_type_id, payment_kind}`.

- [ ] **Step 1:** В `api.js` добавить `paymentTypes(orgId)` (через `get`), и `deliverOrder` — слать `{by, payment_type_id, payment_kind}`.
- [ ] **Step 2:** На карточке заказа (Assembler/Courier борд) показать `order.payment_type_name` (если есть) — бейдж «Оплата: …».
- [ ] **Step 3:** В `TgCourier.jsx` на «Доставлено»: загрузить `paymentTypes(orgId)` (orgId заказа), показать список РЕАЛЬНЫХ типов; дефолт — `order.payment_type_name`/id если есть; по выбору слать `payment_type_id` + `payment_kind` (kind выбранного типа). Сохранить прежнее поведение (имя курьера в `by`).
- [ ] **Step 4: Verify** — `cd frontend && npm run build` (0 ошибок). Ручная проверка: на доске виден тип оплаты; курьер выбирает из реального списка.
- [ ] **Step 5: Commit** — `git commit -m "feat(front): тип оплаты на доске + реальный список оплат у курьера"`

---

### Task 6: Ручная проверка на 1 живом заказе (с тобой)
**Не код.** На одном реальном delivery-заказе: курьер «Доставлено» → выбрать тип → проверить, что в iiko заказ перешёл в **Closed** (не висит OnWay), чек напечатался, и на доске Saya заказ ушёл. Свериться с тайм-лайном заказа (`/admin/orders/{id}/timeline`) — событие close ok=true.

## Self-Review
- Покрытие: п.2 (реальный тип) → Task 1/2/4/5; п.3 (закрытие) → Task 1/3; «видно на доске» → Task 2/5. §close-flow (Delivered перед close) → Task 3.
- Типы/сигнатуры: `set_delivery_status`, `payment_item_by_id`, `list_payment_types` едины в Task 1/3/4; `payment_type_*` поля едины Task 2/3/5.
- Плейсхолдеров нет; места «найти где маппятся поля» (ingest, Assembler-карточка) — явные указания сверить с реальным кодом.
