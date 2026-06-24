# Analytics: Stages & Journal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Управляющий видит, на каком этапе застревает заказ (кухня/сборка/ожидание курьера/доставка) за любой период, и имеет журнал «кто что когда сделал» с drill-down по заказу.

**Architecture:** Гибрид. Числовая аналитика этапов (этапы, бутылочное горло, SLA, лаг готов→выехал, кухня-зоны, аномалии) считается из УЖЕ существующих таймстампов `Order`/`OrderItem` чистыми функциями в новом `analytics_stages.py` — работает на всю историю. Журнал «кто именно» строится из `order_events` (расширяем актором/org_id, логируем каждое действие вперёд) + синтез из таймстампов для старых заказов. Новые `/admin/*` эндпоинты отдают агрегаты; новый экран `/analytics` на Recharts их рисует.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, pytest-asyncio, respx; React + Vite, Recharts.

## Global Constraints

- Числовая аналитика этапов — ТОЛЬКО из таймстампов `Order`/`OrderItem` (работает для старых и новых заказов). Не вводить новое хранилище для этого.
- `order_events` — источник журнала «кто что»; новые колонки nullable (не ломать существующие строки `iiko_write`).
- Backend `/admin/*` эндпоинты идут без auth-зависимости (как все текущие): сигнатура `(org_id: str, ..., session: AsyncSession = Depends(get_session))`. Гейт менеджера — на фронте (`RequireManager` в `App.jsx`).
- Период: query `from`/`to` — ISO-дата `YYYY-MM-DD` включительно; дефолт — последние 7 дней. SLA-порог `sla_minutes` дефолт 45.
- Этап с `None` на любом конце → длительность `null` (в агрегатах пропускается). Отрицательную длительность клампить в `0`.
- Медиана/перцентили — на чистом Python (`statistics`), без новых backend-зависимостей.
- Фронт: единственная новая зависимость — `recharts`. Следовать существующим паттернам компонентов/CSS и обёрток `frontend/src/lib/api.js`.
- DRY, YAGNI, TDD, частые коммиты. Тесты — `asyncio_mode=auto` (без `@pytest.mark.asyncio`), как во всём репо.

---

### Task 1: Расширение `OrderEvent` (актор, org_id, item_id) + миграция

**Files:**
- Modify: `backend/app/models.py:80-86` (класс `OrderEvent`)
- Modify: `backend/app/main.py:21-24` (`_ADDED_COLUMNS`)
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces: `OrderEvent.org_id: str|None`, `.actor_code: str|None`, `.actor_name: str|None`, `.item_id: int|None` (все nullable). Индекс `(org_id, at)`.

- [ ] **Step 1: Написать падающий тест**

В `backend/tests/test_models.py` добавить:

```python
async def test_order_event_has_actor_and_org_fields(session):
    from app.models import Order, OrderEvent
    o = Order(iiko_order_id="oe1", org_id="org-1", number="1")
    session.add(o); await session.commit()
    ev = OrderEvent(order_id=o.id, org_id="org-1", type="item_accept",
                    actor_name="Коля", actor_code="K1", item_id=7, payload={})
    session.add(ev); await session.commit()
    assert ev.org_id == "org-1" and ev.actor_name == "Коля"
    assert ev.actor_code == "K1" and ev.item_id == 7
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_models.py::test_order_event_has_actor_and_org_fields -v`
Expected: FAIL — `TypeError: 'org_id' is an invalid keyword argument for OrderEvent`.

- [ ] **Step 3: Добавить поля и индекс**

В `backend/app/models.py`, класс `OrderEvent`, после строки `payload: Mapped[dict] = mapped_column(JSON, default=dict)` добавить:

```python
    org_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    actor_code: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String, nullable=True)
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

(`String`/`Integer` уже импортированы в файле.)

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_models.py::test_order_event_has_actor_and_org_fields -v`
Expected: PASS

- [ ] **Step 5: Прод-миграция**

В `backend/app/main.py` в `_ADDED_COLUMNS` добавить:

```python
    ("order_events", "org_id", "VARCHAR"),
    ("order_events", "actor_code", "VARCHAR"),
    ("order_events", "actor_name", "VARCHAR"),
    ("order_events", "item_id", "INTEGER"),
```

- [ ] **Step 6: Прогнать тесты**

Run: `cd backend && .venv/bin/pytest tests/test_models.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/app/main.py backend/tests/test_models.py
git commit -m "feat(events): OrderEvent — org_id/actor/item_id + миграция (фундамент аналитики)"
```

---

### Task 2: Универсальный логгер `_log_action` + рефактор `_log_iiko_event`

**Files:**
- Modify: `backend/app/api.py:372-377` (`_log_iiko_event`) и его 2 вызова (`:418`, `:959`)
- Test: `backend/tests/test_iiko_write_events.py`

**Interfaces:**
- Produces: `async _log_action(session, order, type: str, *, actor_name="", actor_code="", item_id=None, payload=None) -> None` — пишет `OrderEvent(order_id=order.id, org_id=order.org_id, type=type, actor_name=actor_name, actor_code=actor_code, item_id=item_id, payload=payload or {})` и коммитит.
- `_log_iiko_event(session, order, method: str, ok: bool, detail: dict)` теперь принимает `order` (не `order_id`) и делегирует в `_log_action`.

- [ ] **Step 1: Написать падающий тест**

В `backend/tests/test_iiko_write_events.py` добавить:

```python
async def test_log_action_writes_actor_and_org(session):
    from app.api import _log_action
    from app.models import Order, OrderEvent
    from sqlalchemy import select
    o = Order(iiko_order_id="a1", org_id="org-9", number="55")
    session.add(o); await session.commit()
    await _log_action(session, o, "item_ready", actor_name="Коля",
                      actor_code="K1", item_id=3, payload={"name": "Ролл"})
    ev = (await session.execute(select(OrderEvent))).scalar_one()
    assert ev.type == "item_ready" and ev.org_id == "org-9"
    assert ev.actor_name == "Коля" and ev.item_id == 3
    assert ev.payload["name"] == "Ролл"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_iiko_write_events.py::test_log_action_writes_actor_and_org -v`
Expected: FAIL — `ImportError: cannot import name '_log_action'`.

- [ ] **Step 3: Реализовать `_log_action` и переписать `_log_iiko_event`**

В `backend/app/api.py` заменить функцию `_log_iiko_event` (строки 372-377) на:

```python
async def _log_action(session, order, type: str, *, actor_name: str = "",
                      actor_code: str = "", item_id: int | None = None,
                      payload: dict | None = None) -> None:
    """Событие действия в order_events (журнал «кто что когда» + материал аналитики)."""
    session.add(_OrderEvent(order_id=order.id, org_id=order.org_id, type=type,
                            actor_name=actor_name or None, actor_code=actor_code or None,
                            item_id=item_id, payload=payload or {}))
    await session.commit()


async def _log_iiko_event(session, order, method: str, ok: bool, detail: dict) -> None:
    """Аудит вызова записи в iiko (обёртка над _log_action)."""
    await _log_action(session, order, "iiko_write",
                      payload={"method": method, "ok": ok, "detail": detail})
```

- [ ] **Step 4: Обновить 2 вызова `_log_iiko_event` (передавать `order`, не `order_id`)**

В `order_delivered` (≈:418) заменить `await _log_iiko_event(session, order_id, "close", ok, detail)` на:

```python
        await _log_iiko_event(session, order, "close", ok, detail)
```

В `order_assign_courier` (≈:959) заменить `await _log_iiko_event(session, order_id, method, ok, detail)` на:

```python
        await _log_iiko_event(session, order, method, ok, detail)
```

(В обоих местах переменная `order` уже в области видимости.)

- [ ] **Step 5: Запустить — убедиться, что проходит и нет регрессий**

Run: `cd backend && .venv/bin/pytest tests/test_iiko_write_events.py tests/test_order_actions.py -q`
Expected: PASS (включая существующие тесты iiko-записи).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api.py backend/tests/test_iiko_write_events.py
git commit -m "feat(events): универсальный _log_action; _log_iiko_event как обёртка"
```

---

### Task 3: Логировать каждое действие в lifecycle-эндпоинтах

**Files:**
- Modify: `backend/app/api.py` — `item_accept` (:305), `item_ready` (:317), `item_check` (:334), `item_revert` (:347), `order_assemble` (:370), `order_take` (:379) (assign-courier и delivered уже логируют iiko; добавим и lifecycle-событие)
- Test: `backend/tests/test_order_events_log.py` (new)

**Interfaces:**
- Consumes: `_log_action` (Task 2), `_load_order`.
- Produces: при действиях создаются `OrderEvent` типов `item_accept`/`item_ready`/`item_check`/`item_revert`/`order_assemble`/`take`/`delivered`. Эти строковые типы — стабильный контракт для Task 8/9.

- [ ] **Step 1: Написать падающие тесты**

Создать `backend/tests/test_order_events_log.py`:

```python
import respx
from httpx import ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from app.db import Base
import app.models  # noqa
from app.models import Order, OrderItem, OrderEvent


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        o = Order(iiko_order_id="o1", org_id="org-1", number="101")
        o.items = [OrderItem(product_id="p1", name="Ролл", station="rolls")]
        s.add(o); await s.commit()
    from app import api
    from app.db import get_session
    async def override():
        async with maker() as s:
            yield s
    api.app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as c:
        yield c, maker
    api.app.dependency_overrides.clear()
    await engine.dispose()


async def _ids(maker):
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()
        iid = (await s.execute(select(OrderItem.id))).scalar_one()
    return oid, iid


@respx.mock
async def test_item_ready_logs_event(client):
    c, maker = client
    _, iid = await _ids(maker)
    await c.post(f"/items/{iid}/ready", json={"by": "Коля"})
    async with maker() as s:
        ev = (await s.execute(select(OrderEvent).where(OrderEvent.type == "item_ready"))).scalars().all()
    assert len(ev) == 1
    assert ev[0].actor_name == "Коля" and ev[0].item_id == iid and ev[0].org_id == "org-1"


@respx.mock
async def test_assemble_logs_event(client):
    c, maker = client
    oid, _ = await _ids(maker)
    await c.post(f"/orders/{oid}/assemble", json={"by": "Сборщик"})
    async with maker() as s:
        ev = (await s.execute(select(OrderEvent).where(OrderEvent.type == "order_assemble"))).scalars().all()
    assert len(ev) == 1 and ev[0].actor_name == "Сборщик"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_order_events_log.py -v`
Expected: FAIL — событий нет (логирование не подключено).

- [ ] **Step 3: Подключить логирование в item-эндпоинтах**

В `backend/app/api.py`, в КАЖДОМ из `item_accept`, `item_ready`, `item_check`, `item_revert` — ПЕРЕД `return await _item_and_broadcast(session, item_id)` добавить загрузку заказа и лог. Конкретно:

`item_accept` (после `item.checked_by = body.by or item.checked_by`):
```python
    order = await _load_order(session, item.order_id)
    await _log_action(session, order, "item_accept", actor_name=body.by,
                      item_id=item.id, payload={"name": item.name, "station": item.station})
```

`item_ready` (после блока, где проставляется `station_accepted_at` фоллбэком):
```python
    order = await _load_order(session, item.order_id)
    await _log_action(session, order, "item_ready", actor_name=body.by,
                      item_id=item.id, payload={"name": item.name, "station": item.station})
```

`item_check` (после `item.checked_by = body.by or item.checked_by`):
```python
    order = await _load_order(session, item.order_id)
    await _log_action(session, order, "item_check", actor_name=body.by,
                      item_id=item.id, payload={"name": item.name})
```

`item_revert` (после `item.checked_at = None`):
```python
    order = await _load_order(session, item.order_id)
    await _log_action(session, order, "item_revert", actor_name=body.by,
                      item_id=item.id, payload={"name": item.name})
```

- [ ] **Step 4: Подключить логирование в order-эндпоинтах**

`order_assemble` — заменить тело на:
```python
    order = await _load_order(session, order_id)
    order.assembled_at = _dt.utcnow()
    await _log_action(session, order, "order_assemble", actor_name=body.by)
    return await _order_action_and_broadcast(session, order_id)
```

`order_take` — заменить тело на:
```python
    order = await _load_order(session, order_id)
    order.on_way_at = _dt.utcnow()
    await _log_action(session, order, "take", actor_name=body.by)
    return await _order_action_and_broadcast(session, order_id)
```

`order_delivered` — в начале (после `order.delivered_at = _dt.utcnow()`) добавить:
```python
    await _log_action(session, order, "delivered", actor_name=body.by)
```
(iiko-логика ниже остаётся как есть.)

- [ ] **Step 5: Запустить — убедиться, что проходит и нет регрессий**

Run: `cd backend && .venv/bin/pytest tests/test_order_events_log.py tests/test_order_actions.py tests/test_telegram.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api.py backend/tests/test_order_events_log.py
git commit -m "feat(events): логировать каждое lifecycle-действие в order_events"
```

---

### Task 4: Сервис этапов — `order_stages` + перцентили + `stage_aggregates`

**Files:**
- Create: `backend/app/analytics_stages.py`
- Test: `backend/tests/test_analytics_stages.py`

**Interfaces:**
- Produces:
  - `STAGES: list[tuple[str, str]]` — `[("queue","Очередь на кухню"), ("cooking","Готовка"), ("assembly","Сборка"), ("pickup_wait","Ожидание курьера"), ("delivery","Доставка"), ("iiko_close","Закрытие в iiko")]`
  - `order_stages(order, close_at=None) -> list[dict]` — 6 этапов `{"key","label","seconds":float|None,"bottleneck":bool}`.
  - `pctl(values: list[float], p: float) -> float | None`
  - `stage_aggregates(orders, close_at_by_order=None) -> dict` — `{"stages":[{key,label,avg,median,p95,count}], "bottleneck_distribution":{key:count}}`.

- [ ] **Step 1: Написать падающий тест**

Создать `backend/tests/test_analytics_stages.py`:

```python
from datetime import datetime, timedelta
from app import analytics_stages as A
from app.models import Order, OrderItem


def _order(**ts):
    base = datetime(2026, 6, 20, 12, 0, 0)
    o = Order(iiko_order_id="x", org_id="org-1", number="1")
    o.created_at = base
    it = OrderItem(product_id="p", name="Ролл", station="rolls")
    it.station_accepted_at = ts.get("acc")
    it.station_ready_at = ts.get("ready")
    o.items = [it]
    o.assembled_at = ts.get("assembled")
    o.on_way_at = ts.get("on_way")
    o.delivered_at = ts.get("delivered")
    return o, base


def test_order_stages_durations_and_bottleneck():
    o, base = _order(
        acc=datetime(2026, 6, 20, 12, 2),       # queue 120s
        ready=datetime(2026, 6, 20, 12, 12),    # cooking 600s  <-- bottleneck
        assembled=datetime(2026, 6, 20, 12, 14),# assembly 120s
        on_way=datetime(2026, 6, 20, 12, 19),   # pickup_wait 300s
        delivered=datetime(2026, 6, 20, 12, 29) # delivery 600s -> tie, first max wins cooking
    )
    stages = A.order_stages(o)
    by = {s["key"]: s for s in stages}
    assert by["queue"]["seconds"] == 120
    assert by["cooking"]["seconds"] == 600
    assert by["iiko_close"]["seconds"] is None  # close_at не передан
    bott = [s["key"] for s in stages if s["bottleneck"]]
    assert bott == ["cooking"]  # первый максимум


def test_order_stages_none_when_missing_timestamp():
    o, _ = _order(acc=None, ready=None)
    by = {s["key"]: s for s in A.order_stages(o)}
    assert by["queue"]["seconds"] is None and by["cooking"]["seconds"] is None


def test_pctl_and_aggregates():
    assert A.pctl([], 95) is None
    assert A.pctl([10], 95) == 10
    o1, _ = _order(acc=datetime(2026,6,20,12,1), ready=datetime(2026,6,20,12,3))   # cooking 120
    o2, _ = _order(acc=datetime(2026,6,20,12,1), ready=datetime(2026,6,20,12,6))   # cooking 300
    agg = A.stage_aggregates([o1, o2])
    cooking = next(s for s in agg["stages"] if s["key"] == "cooking")
    assert cooking["count"] == 2 and cooking["avg"] == 210
    assert agg["bottleneck_distribution"]["cooking"] == 2
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_analytics_stages.py -v`
Expected: FAIL — `ModuleNotFoundError: app.analytics_stages`.

- [ ] **Step 3: Реализовать сервис**

Создать `backend/app/analytics_stages.py`:

```python
from __future__ import annotations
import statistics
from datetime import datetime

STAGES = [
    ("queue", "Очередь на кухню"),
    ("cooking", "Готовка"),
    ("assembly", "Сборка"),
    ("pickup_wait", "Ожидание курьера"),
    ("delivery", "Доставка"),
    ("iiko_close", "Закрытие в iiko"),
]


def _dur(a: datetime | None, b: datetime | None) -> float | None:
    """Длительность b-a в секундах; None если любой конец пуст; отрицательную клампим в 0."""
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds())


def _accepted_min(order) -> datetime | None:
    vals = [i.station_accepted_at for i in order.items if i.station_accepted_at]
    return min(vals) if vals else None


def _ready_max(order) -> datetime | None:
    vals = [i.station_ready_at for i in order.items if i.station_ready_at]
    return max(vals) if vals else None


def order_stages(order, close_at: datetime | None = None) -> list[dict]:
    acc, ready = _accepted_min(order), _ready_max(order)
    seconds = {
        "queue": _dur(order.created_at, acc),
        "cooking": _dur(acc, ready),
        "assembly": _dur(ready, order.assembled_at),
        "pickup_wait": _dur(order.assembled_at, order.on_way_at),
        "delivery": _dur(order.on_way_at, order.delivered_at),
        "iiko_close": _dur(order.delivered_at, close_at),
    }
    # бутылочное горло — первый максимум среди не-None
    best_key = None
    best_val = -1.0
    for key, _label in STAGES:
        s = seconds[key]
        if s is not None and s > best_val:
            best_val, best_key = s, key
    return [{"key": k, "label": lbl, "seconds": seconds[k],
             "bottleneck": (k == best_key)} for k, lbl in STAGES]


def pctl(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    rank = (p / 100) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def stage_aggregates(orders, close_at_by_order: dict | None = None) -> dict:
    close_at_by_order = close_at_by_order or {}
    buckets: dict[str, list[float]] = {k: [] for k, _ in STAGES}
    bottleneck: dict[str, int] = {k: 0 for k, _ in STAGES}
    for o in orders:
        stages = order_stages(o, close_at_by_order.get(o.id))
        for s in stages:
            if s["seconds"] is not None:
                buckets[s["key"]].append(s["seconds"])
            if s["bottleneck"]:
                bottleneck[s["key"]] += 1
    out_stages = []
    for key, label in STAGES:
        vals = buckets[key]
        out_stages.append({
            "key": key, "label": label, "count": len(vals),
            "avg": (sum(vals) / len(vals)) if vals else None,
            "median": statistics.median(vals) if vals else None,
            "p95": pctl(vals, 95),
        })
    return {"stages": out_stages, "bottleneck_distribution": bottleneck}
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_analytics_stages.py -v`
Expected: PASS (3 теста).

- [ ] **Step 5: Commit**

```bash
git add backend/app/analytics_stages.py backend/tests/test_analytics_stages.py
git commit -m "feat(analytics): сервис этапов — order_stages, перцентили, stage_aggregates"
```

---

### Task 5: Сервис — `sla_breaches` + `pickup_lag`

**Files:**
- Modify: `backend/app/analytics_stages.py`
- Test: `backend/tests/test_analytics_stages.py`

**Interfaces:**
- Consumes: `_dur`, `pctl`.
- Produces:
  - `sla_breaches(orders, threshold_sec: float) -> dict` — `{"total","breached","rate","by_day":[{date,total,breached}],"by_hour":[{hour,total,breached}]}`. Учитываются только заказы с `created_at` и `delivered_at`.
  - `pickup_lag(orders) -> dict` — `{"avg","median","p95","worst":[{number,seconds}]}` по этапу `assembled→on_way`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `backend/tests/test_analytics_stages.py`:

```python
def test_sla_breaches():
    from datetime import datetime
    def ord_(num, created, delivered):
        o = Order(iiko_order_id=num, org_id="org-1", number=num)
        o.created_at = created; o.delivered_at = delivered; o.items = []
        return o
    orders = [
        ord_("1", datetime(2026,6,20,12,0), datetime(2026,6,20,12,30)),  # 30m ok
        ord_("2", datetime(2026,6,20,13,0), datetime(2026,6,20,14,0)),   # 60m breach
        ord_("3", datetime(2026,6,21,12,0), datetime(2026,6,21,13,0)),   # 60m breach, next day
    ]
    r = A.sla_breaches(orders, threshold_sec=45*60)
    assert r["total"] == 3 and r["breached"] == 2
    assert round(r["rate"], 2) == 0.67
    days = {d["date"]: d for d in r["by_day"]}
    assert days["2026-06-20"]["breached"] == 1 and days["2026-06-21"]["breached"] == 1
    hours = {h["hour"]: h for h in r["by_hour"]}
    assert hours[13]["breached"] == 1


def test_pickup_lag():
    from datetime import datetime
    o = Order(iiko_order_id="1", org_id="org-1", number="9"); o.items = []
    o.assembled_at = datetime(2026,6,20,12,0); o.on_way_at = datetime(2026,6,20,12,5)  # 300s
    r = A.pickup_lag([o])
    assert r["avg"] == 300 and r["worst"][0]["number"] == "9"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_analytics_stages.py -k "sla or pickup" -v`
Expected: FAIL — нет `sla_breaches`/`pickup_lag`.

- [ ] **Step 3: Реализовать**

Добавить в `backend/app/analytics_stages.py`:

```python
def sla_breaches(orders, threshold_sec: float) -> dict:
    total = breached = 0
    by_day: dict[str, dict] = {}
    by_hour: dict[int, dict] = {}
    for o in orders:
        full = _dur(o.created_at, o.delivered_at)
        if full is None:
            continue
        total += 1
        is_breach = full > threshold_sec
        day = o.created_at.strftime("%Y-%m-%d")
        hour = o.created_at.hour
        d = by_day.setdefault(day, {"date": day, "total": 0, "breached": 0})
        h = by_hour.setdefault(hour, {"hour": hour, "total": 0, "breached": 0})
        d["total"] += 1; h["total"] += 1
        if is_breach:
            breached += 1; d["breached"] += 1; h["breached"] += 1
    return {
        "total": total, "breached": breached,
        "rate": (breached / total) if total else 0.0,
        "by_day": sorted(by_day.values(), key=lambda x: x["date"]),
        "by_hour": sorted(by_hour.values(), key=lambda x: x["hour"]),
    }


def pickup_lag(orders) -> dict:
    rows = []
    for o in orders:
        s = _dur(o.assembled_at, o.on_way_at)
        if s is not None:
            rows.append({"number": o.number, "seconds": s})
    vals = [r["seconds"] for r in rows]
    return {
        "avg": (sum(vals) / len(vals)) if vals else None,
        "median": statistics.median(vals) if vals else None,
        "p95": pctl(vals, 95),
        "worst": sorted(rows, key=lambda r: r["seconds"], reverse=True)[:10],
    }
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_analytics_stages.py -k "sla or pickup" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/analytics_stages.py backend/tests/test_analytics_stages.py
git commit -m "feat(analytics): SLA-просрочки и лаг готов->выехал"
```

---

### Task 6: Сервис — `kitchen_zones` + `anomalies` + `synth_events`

**Files:**
- Modify: `backend/app/analytics_stages.py`
- Test: `backend/tests/test_analytics_stages.py`

**Interfaces:**
- Produces:
  - `kitchen_zones(orders) -> dict` — `{"zones":[{zone,avg,median,count,employees:[{name,avg,count}]}]}` (prep = `accepted→ready` по позициям).
  - `anomalies(orders, revert_counts: dict[int,int]) -> list[dict]` — быстрые «готово» (`prep<60s`) + заказы с `revert_counts[order_id]>=2`.
  - `synth_events(order) -> list[dict]` — синтез событий из таймстампов: `{"at","type","item_id","item_name","actor_name"}`, отсортировано по `at` (для журнала старых заказов; Task 8/9).

- [ ] **Step 1: Написать падающий тест**

Добавить в `backend/tests/test_analytics_stages.py`:

```python
def test_kitchen_zones():
    from datetime import datetime
    o = Order(iiko_order_id="1", org_id="org-1", number="1")
    a = datetime(2026,6,20,12,0)
    i1 = OrderItem(product_id="p1", name="Ролл", station="rolls",
                   checked_by="Коля"); i1.station_accepted_at=a; i1.station_ready_at=datetime(2026,6,20,12,2)
    i2 = OrderItem(product_id="p2", name="Пицца", station="pizza",
                   checked_by="Аян"); i2.station_accepted_at=a; i2.station_ready_at=datetime(2026,6,20,12,6)
    o.items = [i1, i2]
    z = {x["zone"]: x for x in A.kitchen_zones([o])["zones"]}
    assert z["rolls"]["avg"] == 120 and z["pizza"]["avg"] == 360
    assert z["rolls"]["employees"][0]["name"] == "Коля"


def test_anomalies_fast_and_reverts():
    from datetime import datetime
    o = Order(iiko_order_id="1", org_id="org-1", number="7")
    it = OrderItem(product_id="p", name="Ролл", station="rolls", checked_by="Коля")
    it.station_accepted_at = datetime(2026,6,20,12,0,0)
    it.station_ready_at = datetime(2026,6,20,12,0,30)  # 30s < 60 -> fast
    o.id = 5; o.items = [it]
    res = A.anomalies([o], revert_counts={5: 3})
    kinds = {r["kind"] for r in res}
    assert "fast" in kinds and "reverts" in kinds
    assert all(r["number"] == "7" for r in res)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_analytics_stages.py -k "kitchen or anomal" -v`
Expected: FAIL — нет `kitchen_zones`/`anomalies`.

- [ ] **Step 3: Реализовать**

Добавить в `backend/app/analytics_stages.py`:

```python
def kitchen_zones(orders) -> dict:
    by_zone: dict[str, list[float]] = {}
    by_zone_emp: dict[str, dict[str, list[float]]] = {}
    for o in orders:
        for it in o.items:
            s = _dur(it.station_accepted_at, it.station_ready_at)
            if s is None:
                continue
            z = it.station or "other"
            by_zone.setdefault(z, []).append(s)
            emp = it.checked_by or "—"
            by_zone_emp.setdefault(z, {}).setdefault(emp, []).append(s)
    zones = []
    for z, vals in sorted(by_zone.items()):
        emps = [{"name": n, "count": len(v), "avg": sum(v) / len(v)}
                for n, v in sorted(by_zone_emp.get(z, {}).items())]
        emps.sort(key=lambda e: e["avg"], reverse=True)
        zones.append({"zone": z, "count": len(vals),
                      "avg": sum(vals) / len(vals),
                      "median": statistics.median(vals), "employees": emps})
    return {"zones": zones}


def anomalies(orders, revert_counts: dict) -> list[dict]:
    out = []
    for o in orders:
        for it in o.items:
            s = _dur(it.station_accepted_at, it.station_ready_at)
            if s is not None and s < 60:
                out.append({"order_id": o.id, "number": o.number, "kind": "fast",
                            "detail": f"{it.name}: {int(s)}с", "actor": it.checked_by or ""})
        rc = revert_counts.get(o.id, 0)
        if rc >= 2:
            out.append({"order_id": o.id, "number": o.number, "kind": "reverts",
                        "detail": f"возвратов: {rc}", "actor": ""})
    return out


def synth_events(order) -> list[dict]:
    ev = []
    for it in order.items:
        if it.station_accepted_at:
            ev.append({"at": it.station_accepted_at, "type": "item_accept",
                       "item_id": it.id, "item_name": it.name, "actor_name": it.checked_by or ""})
        if it.station_ready_at:
            ev.append({"at": it.station_ready_at, "type": "item_ready",
                       "item_id": it.id, "item_name": it.name, "actor_name": it.checked_by or ""})
        if it.checked_at:
            ev.append({"at": it.checked_at, "type": "item_check",
                       "item_id": it.id, "item_name": it.name, "actor_name": it.checked_by or ""})
    if order.assembled_at:
        ev.append({"at": order.assembled_at, "type": "order_assemble",
                   "item_id": None, "item_name": None, "actor_name": ""})
    if order.on_way_at:
        ev.append({"at": order.on_way_at, "type": "take",
                   "item_id": None, "item_name": None, "actor_name": order.assigned_courier_name or ""})
    if order.delivered_at:
        ev.append({"at": order.delivered_at, "type": "delivered",
                   "item_id": None, "item_name": None, "actor_name": order.assigned_courier_name or ""})
    return sorted(ev, key=lambda e: e["at"])
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_analytics_stages.py -q`
Expected: PASS (весь файл).

- [ ] **Step 5: Commit**

```bash
git add backend/app/analytics_stages.py backend/tests/test_analytics_stages.py
git commit -m "feat(analytics): кухня по зонам, аномалии, синтез событий из таймстампов"
```

---

### Task 7: Период-хелпер + `GET /admin/stage-stats` + `GET /admin/kitchen-zones`

**Files:**
- Modify: `backend/app/api.py`
- Test: `backend/tests/test_admin_analytics2.py` (new)

**Interfaces:**
- Consumes: `analytics_stages` (Task 4-6), `Order`/`OrderItem`.
- Produces:
  - `async _orders_in_period(session, org_id, dt_from, dt_to) -> list[Order]` (по `created_at`, с items через `selectin`).
  - `_parse_period(from_, to_) -> tuple[datetime, datetime]` — ISO-даты, дефолт последние 7 дней; `to` включительно (до конца дня).
  - `GET /admin/stage-stats?org_id&from&to&sla_minutes=45`
  - `GET /admin/kitchen-zones?org_id&from&to`

- [ ] **Step 1: Написать падающие тесты**

Создать `backend/tests/test_admin_analytics2.py`:

```python
import respx
from datetime import datetime
from httpx import ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from app.db import Base
import app.models  # noqa
from app.models import Order, OrderItem


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        o = Order(iiko_order_id="o1", org_id="org-1", number="101")
        o.created_at = datetime(2026, 6, 20, 12, 0)
        it = OrderItem(product_id="p1", name="Ролл", station="rolls", checked_by="Коля")
        it.station_accepted_at = datetime(2026, 6, 20, 12, 1)
        it.station_ready_at = datetime(2026, 6, 20, 12, 4)
        o.items = [it]
        o.assembled_at = datetime(2026, 6, 20, 12, 6)
        o.on_way_at = datetime(2026, 6, 20, 12, 8)
        o.delivered_at = datetime(2026, 6, 20, 12, 40)
        s.add(o); await s.commit()
    from app import api
    from app.db import get_session
    async def override():
        async with maker() as s:
            yield s
    api.app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as c:
        yield c
    api.app.dependency_overrides.clear()
    await engine.dispose()


@respx.mock
async def test_stage_stats(client):
    r = await client.get("/admin/stage-stats",
                         params={"org_id": "org-1", "from": "2026-06-20", "to": "2026-06-20",
                                 "sla_minutes": 45})
    assert r.status_code == 200
    body = r.json()
    cooking = next(s for s in body["stage_aggregates"]["stages"] if s["key"] == "cooking")
    assert cooking["count"] == 1 and cooking["avg"] == 180
    assert body["sla"]["total"] == 1 and body["sla"]["breached"] == 0  # 40m < 45m


@respx.mock
async def test_kitchen_zones_endpoint(client):
    r = await client.get("/admin/kitchen-zones",
                         params={"org_id": "org-1", "from": "2026-06-20", "to": "2026-06-20"})
    assert r.status_code == 200
    zones = {z["zone"]: z for z in r.json()["zones"]}
    assert zones["rolls"]["avg"] == 180
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_admin_analytics2.py -v`
Expected: FAIL — 404 (эндпоинтов нет).

- [ ] **Step 3: Реализовать хелперы и эндпоинты**

В `backend/app/api.py` добавить импорт рядом с другими (вверху файла): `from app import analytics_stages as _stages`. Также убедиться, что импортированы `from datetime import datetime, timedelta` (там уже есть `_dt`; добавить `timedelta` если нет: `from datetime import timedelta as _td`).

Добавить (около других `/admin/*`):

```python
def _parse_period(from_: str | None, to_: str | None):
    """ISO YYYY-MM-DD -> (dt_from, dt_to_inclusive). Дефолт: последние 7 дней."""
    today = _dt.utcnow().date()
    d_to = _dt.fromisoformat(to_).date() if to_ else today
    d_from = _dt.fromisoformat(from_).date() if from_ else (d_to - _td(days=6))
    dt_from = _dt(d_from.year, d_from.month, d_from.day)
    dt_to = _dt(d_to.year, d_to.month, d_to.day) + _td(days=1)  # эксклюзивная верхняя
    return dt_from, dt_to


async def _orders_in_period(session, org_id: str, dt_from, dt_to):
    rows = (await session.execute(
        _select(_Order).where(_Order.org_id == org_id,
                              _Order.created_at >= dt_from,
                              _Order.created_at < dt_to))).scalars().all()
    return rows


@app.get("/admin/stage-stats")
async def admin_stage_stats(org_id: str, from_: str | None = Query(None, alias="from"),
                            to: str | None = None, sla_minutes: int = 45,
                            session: AsyncSession = Depends(get_session)):
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    agg = _stages.stage_aggregates(orders)
    return {
        "stage_aggregates": {"stages": agg["stages"]},
        "bottleneck_distribution": agg["bottleneck_distribution"],
        "sla": _stages.sla_breaches(orders, sla_minutes * 60),
        "pickup_lag": _stages.pickup_lag(orders),
    }


@app.get("/admin/kitchen-zones")
async def admin_kitchen_zones(org_id: str, from_: str | None = Query(None, alias="from"),
                              to: str | None = None,
                              session: AsyncSession = Depends(get_session)):
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    return _stages.kitchen_zones(orders)
```

Убедиться, что `Query` импортирован из `fastapi` (вверху файла; если нет — добавить в существующий `from fastapi import ...`).

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_admin_analytics2.py -v`
Expected: PASS (2 теста).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api.py backend/tests/test_admin_analytics2.py
git commit -m "feat(api): /admin/stage-stats и /admin/kitchen-zones с период-фильтром"
```

---

### Task 8: `GET /admin/orders/{id}/timeline` (этапы + события, merge реальных и синтеза)

**Files:**
- Modify: `backend/app/api.py`
- Test: `backend/tests/test_admin_analytics2.py`

**Interfaces:**
- Consumes: `analytics_stages.order_stages`, `analytics_stages.synth_events`, `OrderEvent`.
- Produces: `GET /admin/orders/{order_id}/timeline` → `{"order":{"id","number","org_id"}, "stages":[...], "events":[{at,type,actor_name,item_id,payload}]}`. Если у заказа есть реальные `order_events` — берём их; иначе синтез из таймстампов.

- [ ] **Step 1: Написать падающий тест**

Добавить в `backend/tests/test_admin_analytics2.py`:

```python
@respx.mock
async def test_timeline_synthesizes_when_no_events(client):
    # seed-заказ из фикстуры событий не имеет -> синтез из таймстампов
    r = await client.get("/admin/orders/1/timeline", params={"org_id": "org-1"})
    assert r.status_code == 200
    body = r.json()
    keys = [s["key"] for s in body["stages"]]
    assert keys == ["queue", "cooking", "assembly", "pickup_wait", "delivery", "iiko_close"]
    types = [e["type"] for e in body["events"]]
    assert "item_accept" in types and "delivered" in types  # синтезированы
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_admin_analytics2.py::test_timeline_synthesizes_when_no_events -v`
Expected: FAIL — 404.

- [ ] **Step 3: Реализовать**

В `backend/app/api.py` добавить:

```python
def _close_at(events) -> "datetime | None":
    for e in events:
        if e.type == "iiko_write" and (e.payload or {}).get("method") == "close" \
                and (e.payload or {}).get("ok"):
            return e.at
    return None


@app.get("/admin/orders/{order_id}/timeline")
async def admin_order_timeline(order_id: int, org_id: str,
                               session: AsyncSession = Depends(get_session)):
    order = await _load_order(session, order_id)
    events = (await session.execute(
        _select(_OrderEvent).where(_OrderEvent.order_id == order_id)
        .order_by(_OrderEvent.at))).scalars().all()
    stages = _stages.order_stages(order, _close_at(events))
    # «настоящие» lifecycle-события (исключая iiko_write) или синтез
    real = [e for e in events if e.type != "iiko_write"]
    if real:
        ev_out = [{"at": _iso(e.at), "type": e.type, "actor_name": e.actor_name or "",
                   "item_id": e.item_id, "payload": e.payload or {}} for e in real]
    else:
        ev_out = [{"at": _iso(e["at"]), "type": e["type"], "actor_name": e["actor_name"],
                   "item_id": e["item_id"], "payload": {"item_name": e["item_name"]}}
                  for e in _stages.synth_events(order)]
    return {"order": {"id": order.id, "number": order.number, "org_id": order.org_id},
            "stages": stages, "events": ev_out}
```

(`_iso` уже есть в `realtime.py`; в `api.py` использовать `realtime._iso` или локальный — проверь импорт; если в api.py нет `_iso`, используй `realtime._iso`. Если `_iso` приватный и недоступен — добавь `from app.realtime import _iso`.)

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd backend && .venv/bin/pytest tests/test_admin_analytics2.py::test_timeline_synthesizes_when_no_events -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api.py backend/tests/test_admin_analytics2.py
git commit -m "feat(api): /admin/orders/{id}/timeline — этапы + события (real/синтез)"
```

---

### Task 9: `GET /admin/journal` + расширение `/admin/anomalies`

**Files:**
- Modify: `backend/app/api.py` (`admin_anomalies` :648 и новый journal)
- Test: `backend/tests/test_admin_analytics2.py`

**Interfaces:**
- Consumes: `OrderEvent`, `_orders_in_period`, `analytics_stages.synth_events`, `analytics_stages.anomalies`.
- Produces:
  - `GET /admin/journal?org_id&from&to&actor&type&order_number&limit=100&offset=0` → `{"rows":[{at,order_number,type,actor_name,item_name,detail}],"total"}`.
  - `/admin/anomalies` — добавить возвраты (`item_revert`≥2), не убирая существующие поля ответа.

- [ ] **Step 1: Написать падающий тест**

Добавить в `backend/tests/test_admin_analytics2.py`:

```python
@respx.mock
async def test_journal_returns_synth_rows(client):
    r = await client.get("/admin/journal",
                         params={"org_id": "org-1", "from": "2026-06-20", "to": "2026-06-20"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    types = {row["type"] for row in body["rows"]}
    assert "item_ready" in types or "delivered" in types


@respx.mock
async def test_journal_filters_by_type(client):
    r = await client.get("/admin/journal",
                         params={"org_id": "org-1", "from": "2026-06-20", "to": "2026-06-20",
                                 "type": "delivered"})
    assert r.status_code == 200
    assert all(row["type"] == "delivered" for row in r.json()["rows"])
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd backend && .venv/bin/pytest tests/test_admin_analytics2.py -k journal -v`
Expected: FAIL — 404.

- [ ] **Step 3: Реализовать journal**

В `backend/app/api.py` добавить:

```python
@app.get("/admin/journal")
async def admin_journal(org_id: str, from_: str | None = Query(None, alias="from"),
                        to: str | None = None, actor: str | None = None,
                        type: str | None = None, order_number: str | None = None,
                        limit: int = 100, offset: int = 0,
                        session: AsyncSession = Depends(get_session)):
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    by_id = {o.id: o for o in orders}
    # реальные события в периоде по заказам периода (кроме iiko_write — это аудит, не lifecycle)
    have_events: set[int] = set()
    rows: list[dict] = []
    if by_id:
        evs = (await session.execute(
            _select(_OrderEvent).where(_OrderEvent.order_id.in_(list(by_id)),
                                       _OrderEvent.type != "iiko_write"))).scalars().all()
        for e in evs:
            have_events.add(e.order_id)
            rows.append({"at": e.at, "order_number": by_id[e.order_id].number,
                         "type": e.type, "actor_name": e.actor_name or "",
                         "item_name": (e.payload or {}).get("name"),
                         "detail": (e.payload or {}).get("station", "")})
    # синтез для заказов периода без событий
    for o in orders:
        if o.id in have_events:
            continue
        for se in _stages.synth_events(o):
            rows.append({"at": se["at"], "order_number": o.number, "type": se["type"],
                         "actor_name": se["actor_name"], "item_name": se["item_name"],
                         "detail": ""})
    # фильтры
    if actor:
        rows = [r for r in rows if actor.lower() in (r["actor_name"] or "").lower()]
    if type:
        rows = [r for r in rows if r["type"] == type]
    if order_number:
        rows = [r for r in rows if r["order_number"] == order_number]
    rows.sort(key=lambda r: r["at"], reverse=True)
    total = len(rows)
    page = rows[offset:offset + limit]
    return {"total": total,
            "rows": [{**r, "at": _iso(r["at"])} for r in page]}
```

- [ ] **Step 4: Расширить anomalies**

Найти `admin_anomalies` (:648). Перед формированием ответа собрать счётчики возвратов и подмешать через сервис. Минимально: после загрузки сегодняшних заказов добавить запрос возвратов и вызвать `_stages.anomalies`. Не убирая текущие поля, добавить в ответ ключ `extra`:

```python
    # ... существующая логика формирует текущий ответ (список быстрых) ...
    # ДОБАВИТЬ перед return:
    revolts = (await session.execute(
        _select(_OrderEvent.order_id).where(_OrderEvent.org_id == org_id,
                                            _OrderEvent.type == "item_revert"))).scalars().all()
    revert_counts: dict[int, int] = {}
    for oid in revolts:
        revert_counts[oid] = revert_counts.get(oid, 0) + 1
    # orders_today — список заказов, уже загруженный существующей логикой эндпоинта;
    # если переменная называется иначе, использовать её имя.
    extra = _stages.anomalies(orders_today, revert_counts)
```

Затем в существующий возвращаемый объект добавить поле `"reverts": [a for a in extra if a["kind"] == "reverts"]` (если эндпоинт возвращает список — обернуть в `{"fast": <старый список>, "reverts": [...]}`; согласовать с фактической формой ответа, сохранив старые данные доступными).

> Реализатор: открой текущий `admin_anomalies`, посмотри имя переменной со списком заказов и форму ответа, и подмешай возвраты, НЕ ломая существующих потребителей (`EmployeeHistory`/`Dashboard`). Если форма ответа — голый список, переходи на `{"fast":[...старое...], "reverts":[...]}` и поправь фронт-потребителя в Task 11.

- [ ] **Step 5: Запустить — убедиться, что проходит (журнал + старые тесты аномалий)**

Run: `cd backend && .venv/bin/pytest tests/test_admin_analytics2.py tests/test_history.py -q`
Expected: PASS

- [ ] **Step 6: Прогнать весь backend**

Run: `cd backend && .venv/bin/pytest -q`
Expected: PASS (вся сюита зелёная).

- [ ] **Step 7: Commit**

```bash
git add backend/app/api.py backend/tests/test_admin_analytics2.py
git commit -m "feat(api): /admin/journal + возвраты в /admin/anomalies"
```

---

### Task 10: Фронт — Recharts, API-обёртки, маршрут `/analytics`, период + панели «этапы» и «SLA»

**Files:**
- Modify: `frontend/package.json` (dep `recharts`)
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/App.jsx` (импорт + маршрут `/analytics` под `RequireManager`)
- Create: `frontend/src/screens/Analytics.jsx`
- Modify: навигация управляющего (там же, где ссылки на `/dashboard`/`/admin` — найти `grep -rn "/dashboard" frontend/src`)

**Interfaces:**
- Consumes: `GET /admin/stage-stats`, `GET /admin/kitchen-zones`, `GET /admin/journal`, `GET /admin/orders/{id}/timeline`.
- Produces: экран `/analytics` (менеджерский) с пикером периода; в этой задаче — панели «Где застревают» и «SLA».

- [ ] **Step 1: Установить Recharts**

Run: `cd frontend && npm i recharts`
Expected: `recharts` появился в `package.json` dependencies, `npm install` без ошибок.

- [ ] **Step 2: Добавить API-обёртки**

В `frontend/src/lib/api.js` добавить (в том же объекте/стиле, что существующие методы; параметр периода — query):

```js
  stageStats: (orgId, from, to, slaMinutes = 45) =>
    get(`/admin/stage-stats?org_id=${orgId}&from=${from}&to=${to}&sla_minutes=${slaMinutes}`),
  kitchenZones: (orgId, from, to) =>
    get(`/admin/kitchen-zones?org_id=${orgId}&from=${from}&to=${to}`),
  journal: (orgId, from, to, params = {}) => {
    const q = new URLSearchParams({ org_id: orgId, from, to, ...params }).toString();
    return get(`/admin/journal?${q}`);
  },
  orderTimeline: (orgId, orderId) =>
    get(`/admin/orders/${orderId}/timeline?org_id=${orgId}`),
```

(Если в `api.js` обёртка GET называется иначе, чем `get` — использовать существующее имя; посмотреть верх файла.)

- [ ] **Step 3: Создать экран с пикером периода + 2 панели**

Создать `frontend/src/screens/Analytics.jsx`. Использовать тот же способ получения `org_id`/филиала, что и `Dashboard.jsx`/`Admin.jsx` (посмотреть, как они берут текущий филиал — из контекста/`localStorage`/props). Скелет с реальными Recharts:

```jsx
import { useEffect, useMemo, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
         LineChart, Line, PieChart, Pie, Cell } from "recharts";
import { api } from "../lib/api";
import "../admin.css";

function presetRange(days) {
  const to = new Date();
  const from = new Date(); from.setDate(to.getDate() - (days - 1));
  const iso = (d) => d.toISOString().slice(0, 10);
  return { from: iso(from), to: iso(to) };
}

export default function Analytics() {
  // org_id берётся так же, как в Dashboard.jsx (адаптировать под существующий способ)
  const orgId = localStorage.getItem("org_id") || "";
  const [range, setRange] = useState(presetRange(7));
  const [stats, setStats] = useState(null);

  useEffect(() => {
    if (!orgId) return;
    api.stageStats(orgId, range.from, range.to).then(setStats).catch(() => setStats(null));
  }, [orgId, range.from, range.to]);

  const stageData = useMemo(() =>
    (stats?.stage_aggregates?.stages || []).map((s) => ({
      label: s.label, avg: Math.round((s.avg || 0) / 60),
      p95: Math.round((s.p95 || 0) / 60),
    })), [stats]);

  return (
    <div className="analytics">
      <header className="analytics__bar">
        <h1>Этапы и журнал</h1>
        <div className="analytics__presets">
          <button onClick={() => setRange(presetRange(1))}>Сегодня</button>
          <button onClick={() => setRange(presetRange(7))}>7 дней</button>
          <button onClick={() => setRange(presetRange(30))}>30 дней</button>
          <input type="date" value={range.from}
                 onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))} />
          <input type="date" value={range.to}
                 onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))} />
        </div>
      </header>

      <section className="card">
        <h2>Где застревают (минуты)</h2>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={stageData}>
            <XAxis dataKey="label" /><YAxis /><Tooltip />
            <Bar dataKey="avg" name="среднее" fill="#2563eb" />
            <Bar dataKey="p95" name="p95" fill="#f59e0b" />
          </BarChart>
        </ResponsiveContainer>
      </section>

      <section className="card">
        <h2>SLA-просрочки</h2>
        <p className="kpi">{stats ? `${Math.round((stats.sla.rate || 0) * 100)}% просрочено`
                                  : "…"} ({stats?.sla?.breached || 0}/{stats?.sla?.total || 0})</p>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={(stats?.sla?.by_day || [])}>
            <XAxis dataKey="date" /><YAxis /><Tooltip />
            <Line type="monotone" dataKey="breached" name="просрочки" stroke="#dc2626" />
          </LineChart>
        </ResponsiveContainer>
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Подключить маршрут и навигацию**

В `frontend/src/App.jsx`: добавить `import Analytics from "./screens/Analytics.jsx";` и маршрут рядом с `/dashboard`:
```jsx
      <Route path="/analytics" element={<RequireManager><Analytics /></RequireManager>} />
```
В навигации управляющего (там, где ссылки на `/dashboard`/`/admin`) добавить ссылку «Этапы и журнал» → `/analytics`.

- [ ] **Step 5: Сборка**

Run: `cd frontend && npm run build`
Expected: успешная сборка, 0 ошибок, Recharts в бандле.

- [ ] **Step 6: Commit**

```bash
cd /Users/kiluazoldik/Desktop/при && git add frontend/package.json frontend/package-lock.json frontend/src
git commit -m "feat(front): экран /analytics на Recharts — период, этапы, SLA"
```

---

### Task 11: Фронт — панели «лаг», «кухня-зоны», «аномалии», «журнал» + drill-down тайм-лайн

**Files:**
- Modify: `frontend/src/screens/Analytics.jsx`
- Modify (если потребовалось в Task 9): фронт-потребитель `anomalies` при смене формы ответа
- Test: ручная проверка + сборка

**Interfaces:**
- Consumes: `api.kitchenZones`, `api.journal`, `api.orderTimeline`, `stats.pickup_lag`, `bottleneck_distribution`.

- [ ] **Step 1: Добавить панель «лаг готов→выехал» и пай бутылочных горл**

В `Analytics.jsx` добавить секции, использующие `stats.pickup_lag` (KPI avg/median/p95 + список `worst`) и `stats.bottleneck_distribution` (PieChart по этапам). Пример пай:

```jsx
const COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2"];
const bottleneckData = Object.entries(stats?.bottleneck_distribution || {})
  .map(([key, value]) => ({ name: key, value }));
// <PieChart><Pie data={bottleneckData} dataKey="value" nameKey="name" outerRadius={90}>
//   {bottleneckData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
// </Pie><Tooltip/></PieChart>
```

- [ ] **Step 2: Добавить панель «кухня по зонам»**

Отдельный `useEffect`, грузящий `api.kitchenZones(orgId, range.from, range.to)`; BarChart времени готовки по зонам (минуты) + раскрытие сотрудников зоны (таблица `employees`).

- [ ] **Step 3: Добавить панель «журнал» с фильтрами + drill-down**

`useEffect`, грузящий `api.journal(orgId, range.from, range.to, { type, actor, order_number })`. Таблица строк (`at`, `order_number`, `type`, `actor_name`, `item_name`). Поля-фильтры (select по типам, input по сотруднику/номеру). Клик по строке → `api.orderTimeline(orgId, orderId)` (orderId по номеру — добавь в journal-ответ при необходимости, либо открой по `order_number` через отдельный поиск) и показать модалку/панель: список `stages` (длительности, подсветка `bottleneck`) и `events` тайм-лайном.

> Примечание: если для drill-down нужен `order.id`, а в журнале только `order_number`, реализатор добавляет `order_id` в строки `/admin/journal` (Task 9) — это однострочное дополнение; согласуй и поправь тест журнала при необходимости.

- [ ] **Step 4: Панель «аномалии»**

Грузить `/admin/anomalies` (через существующую/новую обёртку), показать таблицу `fast`+`reverts` со ссылкой «открыть тайм-лайн». Если в Task 9 форма ответа аномалий изменилась — обновить и прежнего потребителя (проверить `EmployeeHistory.jsx`/`Dashboard.jsx`), чтобы не сломать.

- [ ] **Step 5: Сборка и ручная проверка**

Run: `cd frontend && npm run build`
Expected: 0 ошибок.
Ручная: запустить апп (`/run`), открыть `/analytics`, прогнать пресеты периода, раскрыть зоны, отфильтровать журнал, открыть тайм-лайн заказа.

- [ ] **Step 6: Commit**

```bash
cd /Users/kiluazoldik/Desktop/при && git add frontend/src
git commit -m "feat(front): аналитика — лаг, кухня-зоны, аномалии, журнал + тайм-лайн заказа"
```

---

## Self-Review

**Spec coverage:**
- §2 гибрид → таймстамп-сервис (Task 4-6) + событийный лог (Task 1-3) + синтез (Task 6/8/9). §3 этапы → Task 4 (`order_stages`, бутылочное горло, кламп). §4.1 расширение OrderEvent → Task 1. §4.2 `_log_action` → Task 2. §4.3 типы событий + вызовы → Task 3. §4.4 сервис (этапы/SLA/лаг/зоны/аномалии) → Task 4-6. §5 API (timeline/stage-stats/kitchen-zones/journal/anomalies) → Task 7-9, период-хелпер → Task 7. §6 фронт (Recharts, /analytics, 6 панелей, drill-down) → Task 10-11. §7 производительность (период-выборка, selectin, пагинация журнала) → Task 7/9. §9 тесты → распределены. Покрыто.

**Placeholder scan:** Backend-шаги содержат полный код. Task 9 (расширение anomalies) и часть Task 11 намеренно требуют от реализатора свериться с фактической формой существующего ответа `admin_anomalies`/способом получения `org_id` на фронте — это явные инструкции с конкретным действием, а не «доделай сам»; точные значения (типы событий, ключи ответов, сигнатуры) заданы.

**Type consistency:** Ключи этапов `queue/cooking/assembly/pickup_wait/delivery/iiko_close` едины в Task 4 и потребителях. `order_stages(order, close_at=None)`, `stage_aggregates`, `sla_breaches(orders, threshold_sec)`, `pickup_lag`, `kitchen_zones`, `anomalies(orders, revert_counts)`, `synth_events` — сигнатуры совпадают между сервис-задачами и API-задачами. Типы событий (`item_accept/item_ready/item_check/item_revert/order_assemble/take/delivered/iiko_write`) едины в Task 3, 8, 9. `_log_action(session, order, type, *, actor_name, actor_code, item_id, payload)` — одинаково в Task 2 и 3. Период-хелперы `_parse_period`/`_orders_in_period` — Task 7, переиспользуются в 9.
