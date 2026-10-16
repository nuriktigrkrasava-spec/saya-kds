# Зональная аналитика — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить зональный взгляд в аналитику — нагрузка по часам по кухонным зонам (Пицца/Япония/Горячее) и по курьерским зонам города (полигоны на карте), у каждой ячейки объём + среднее время. Существующие блоки не трогаем.

**Architecture:** Бэкенд: новая до-merge колонка `OrderItem.kitchen_zone` (горячее отделяется от пиццы), чистые функции-агрегаторы в `analytics_stages.py`, point-in-polygon в новом `geo.py`, таблица `DeliveryZone` с CRUD, привязка заказа к району на лету по координатам. Фронтенд: два новых блока-хитмапа в `Analytics.jsx` и экран-редактор полигонов на Leaflet+leaflet-draw.

**Tech Stack:** Python (FastAPI, SQLAlchemy async, pytest, sqlite aiosqlite in-memory для тестов), React 18 + Vite, Leaflet 1.9.4 (глобал `window.L` через CDN), recharts. Фронтенд JS-тест-раннера НЕТ — фронт-задачи проверяются вручную (vite dev + браузер).

## Global Constraints

- Время в БД — UTC. Локализация по часам/дням — прибавляем `settings.iiko_tz_offset_hours` (=5), как в `analytics_stages.sla_breaches`.
- Миграции колонок: добавлять кортеж в `_ADDED_COLUMNS` в `backend/app/main.py` (идемпотентный `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). Новые таблицы создаются `Base.metadata.create_all` автоматически.
- `OrderItem.station` и логика рассылки на планшеты (`merge_zone`) **не меняются** — добавляем только `kitchen_zone` как источник для аналитики.
- Кухонные зоны для аналитики: ключи `pizza|rolls|hot`; лейблы для отображения — Пицца / **Япония** (для `rolls`) / Горячее. `other` в нагрузке не показываем.
- Leaflet используется как глобал `window.L` (CDN в `index.html`), НЕ через npm. leaflet-draw подключаем тем же способом (CDN в `index.html`).
- Центр карты по умолчанию — Астана `[51.16, 71.47]` (как в `DemandMap.jsx`/`CourierMap.jsx`).
- Полигон храним как список `[[lat, lon], ...]` (порядок lat,lon — как в Leaflet API проекта).
- Тесты бэкенда: pytest, фикстура `session` из `backend/tests/conftest.py` (in-memory sqlite, `Base.metadata.create_all`). Запуск: `cd backend && python -m pytest`.
- Коммиты частые, в конце каждой задачи. Сообщения на русском, в стиле проекта (`feat(...)`/`test(...)`). Завершать тег-строкой:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

**Backend**
- `backend/app/models.py` — +поле `OrderItem.kitchen_zone`; +модель `DeliveryZone`.
- `backend/app/main.py` — +миграция колонки `order_items.kitchen_zone`.
- `backend/app/stations.py` — `build_resolver` дополнительно отдаёт до-merge зону (`resolve.raw`).
- `backend/app/ingest.py` — при создании `OrderItem` пишет `kitchen_zone=resolve.raw(it)`.
- `backend/app/geo.py` — **новый**: `point_in_polygon`, `assign_zone`.
- `backend/app/analytics_stages.py` — +`zone_load(...)`, +`courier_zone_load(...)`.
- `backend/app/api.py` — +эндпоинты `/admin/zone-load`, `/admin/courier-zone-load`, CRUD `/admin/delivery-zones`.
- `backend/scripts/backfill_kitchen_zone.py` — **новый**: бэкфилл `kitchen_zone` по имени блюда.

**Frontend**
- `frontend/index.html` — +CDN leaflet-draw (css+js).
- `frontend/src/lib/api.js` — +`zoneLoad`, `courierZoneLoad`, CRUD `deliveryZones`.
- `frontend/src/screens/Analytics.jsx` — +блоки «Нагрузка по зонам кухни» и «Курьер: нагрузка по районам».
- `frontend/src/screens/DeliveryZones.jsx` — **новый**: редактор полигонов.
- `frontend/src/App.jsx` — +маршрут `/delivery-zones`.

**Tests**
- `backend/tests/test_kitchen_zone_ingest.py`
- `backend/tests/test_kitchen_zone_backfill.py`
- `backend/tests/test_zone_load.py`
- `backend/tests/test_geo.py`
- `backend/tests/test_delivery_zones_api.py`
- `backend/tests/test_courier_zone_load.py`

---

## Task 1: Колонка `kitchen_zone` + ingest пишет до-merge зону

**Files:**
- Modify: `backend/app/models.py:58-74` (OrderItem)
- Modify: `backend/app/main.py:22-35` (_ADDED_COLUMNS)
- Modify: `backend/app/stations.py:116-161` (build_resolver)
- Modify: `backend/app/ingest.py:72-87` (OrderItem(...))
- Test: `backend/tests/test_kitchen_zone_ingest.py`

**Interfaces:**
- Produces: `OrderItem.kitchen_zone: str | None` — до-merge зона (`rolls|pizza|hot|other`).
- Produces: `resolve.raw(item: WebhookItem) -> str` — атрибут на возвращаемом из `build_resolver` колбэке, отдаёт зону ДО `merge_zone`.

- [ ] **Step 1: Write the failing test**

Создать `backend/tests/test_kitchen_zone_ingest.py`:

```python
import pytest
from app.ingest import ingest_order
from app.schemas import NormalizedOrder, WebhookItem


def _order(items):
    return NormalizedOrder(
        iiko_order_id="o1", org_id="demo", number="1", status="New",
        customer="", phone="", address="", lat=None, lon=None, total=None,
        service_type=None, comment="", items=items,
    )


@pytest.mark.asyncio
async def test_ingest_sets_pre_merge_kitchen_zone(session):
    # «Том Ям» классифицируется как hot; station сливается в pizza,
    # а kitchen_zone должна остаться hot.
    items = [WebhookItem(product_id="p1", name="Том Ям", amount=1,
                         combo_id=None, comment="", category_name="", category_id=None)]
    order = await ingest_order(session, _order(items))
    it = order.items[0]
    assert it.station == "pizza"        # merge hot->pizza (поведение кухни)
    assert it.kitchen_zone == "hot"     # до-merge зона для аналитики
```

> ПРИМЕЧАНИЕ: точные имена полей `NormalizedOrder`/`WebhookItem` сверить в `backend/app/schemas.py`; передавать ровно те, что объявлены (см. как заполняет `normalize_full_order`). Если у `WebhookItem` нет `category_name`/`category_id` — убрать лишние, оставив `product_id,name,amount,combo_id,comment`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kitchen_zone_ingest.py -v`
Expected: FAIL — `AttributeError: 'OrderItem' object has no attribute 'kitchen_zone'` (или `TypeError` про неизвестный kwarg).

- [ ] **Step 3a: Добавить поле в модель**

В `backend/app/models.py`, в классе `OrderItem`, после строки `station: Mapped[str] = mapped_column(String, default="other")` добавить:

```python
    kitchen_zone: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 3b: Миграция колонки**

В `backend/app/main.py`, в список `_ADDED_COLUMNS` добавить строку:

```python
    ("order_items", "kitchen_zone", "VARCHAR"),
```

- [ ] **Step 3c: Резолвер отдаёт raw-зону**

В `backend/app/stations.py`, в `build_resolver`, заменить блок определения `resolve` (строки ~157-161) на:

```python
    # Любой источник зоны проходит через merge (hot -> pizza) для поведения кухни.
    def resolve(item: WebhookItem) -> str:
        return merge_zone(_raw(item))

    # До-merge зона — источник для зональной аналитики (горячее != пицца).
    resolve.raw = _raw  # type: ignore[attr-defined]
    return resolve
```

- [ ] **Step 3d: Ingest пишет kitchen_zone**

В `backend/app/ingest.py`, в `session.add(OrderItem(...))` (строки 74-87) после `station=resolve(it),` добавить строку:

```python
            kitchen_zone=resolve.raw(it),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kitchen_zone_ingest.py -v`
Expected: PASS

- [ ] **Step 5: Прогон всех тестов (регрессия ingest/stations)**

Run: `cd backend && python -m pytest tests/test_ingest.py tests/test_stations.py tests/test_station_automap.py -v`
Expected: PASS (поведение `station`/merge не изменилось).

- [ ] **Step 6: Commit**

```bash
cd /path/to/workspace/при
git add backend/app/models.py backend/app/main.py backend/app/stations.py backend/app/ingest.py backend/tests/test_kitchen_zone_ingest.py
git commit -m "feat(analytics): OrderItem.kitchen_zone — до-merge зона для зональной аналитики

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Бэкфилл `kitchen_zone` для существующих строк

**Files:**
- Create: `backend/scripts/backfill_kitchen_zone.py`
- Test: `backend/tests/test_kitchen_zone_backfill.py`

**Interfaces:**
- Consumes: `classify_name`, `classify_product` из `app.stations`.
- Produces: `backfill_kitchen_zone(session) -> int` — проставляет `kitchen_zone` всем строкам где оно NULL, возвращает число обновлённых.

- [ ] **Step 1: Write the failing test**

Создать `backend/tests/test_kitchen_zone_backfill.py`:

```python
import pytest
from sqlalchemy import select
from app.models import Order, OrderItem
from app.scripts_backfill import backfill_kitchen_zone


@pytest.mark.asyncio
async def test_backfill_separates_hot_from_pizza(session):
    o = Order(iiko_order_id="o1", org_id="demo", number="1")
    session.add(o)
    await session.flush()
    # Имитируем «старые» строки: kitchen_zone не заполнен, station уже слитый.
    session.add_all([
        OrderItem(order_id=o.id, name="Том Ям", station="pizza", kitchen_zone=None),
        OrderItem(order_id=o.id, name="Пицца Пепперони", station="pizza", kitchen_zone=None),
        OrderItem(order_id=o.id, name="Сет Филадельфия", station="rolls", kitchen_zone=None),
    ])
    await session.commit()

    n = await backfill_kitchen_zone(session)
    assert n == 3

    rows = (await session.execute(select(OrderItem).order_by(OrderItem.id))).scalars().all()
    assert rows[0].kitchen_zone == "hot"     # Том Ям отделился от pizza
    assert rows[1].kitchen_zone == "pizza"
    assert rows[2].kitchen_zone == "rolls"
```

> Тест импортирует `app.scripts_backfill` (модуль-обёртка с чистой функцией, чтобы тестировать без `__main__`). Скрипт-CLI ниже просто вызывает её.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kitchen_zone_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.scripts_backfill'`.

- [ ] **Step 3a: Чистая функция бэкфилла**

Создать `backend/app/scripts_backfill.py`:

```python
from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import OrderItem
from app.stations import classify_name, classify_product


def _kitchen_zone_by_name(name: str, station: str | None) -> str:
    """До-merge зона по имени блюда; если имя зону не даёт — оставляем station."""
    z = classify_name(name)
    if z != "other":
        return z
    z = classify_product(name, group_path=None, group_root=None)
    if z != "other":
        return z
    return station or "other"


async def backfill_kitchen_zone(session: AsyncSession) -> int:
    rows = (await session.execute(
        select(OrderItem).where(OrderItem.kitchen_zone.is_(None)))).scalars().all()
    for it in rows:
        it.kitchen_zone = _kitchen_zone_by_name(it.name, it.station)
    await session.commit()
    return len(rows)
```

> ВНИМАНИЕ: `classify_product` применяет `merge_zone` (вернёт pizza для hot). Поэтому первым идёт `classify_name`, который merge НЕ делает и сохраняет `hot`. Это и даёт отделение горячего.

- [ ] **Step 3b: CLI-обёртка**

Создать `backend/scripts/backfill_kitchen_zone.py`:

```python
"""Одноразовый бэкфилл OrderItem.kitchen_zone по имени блюда.
Запуск: cd backend && python -m scripts.backfill_kitchen_zone"""
import asyncio
from app.db import engine, Base
import app.models  # noqa: F401
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from app.scripts_backfill import backfill_kitchen_zone


async def main():
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        n = await backfill_kitchen_zone(s)
        print(f"kitchen_zone backfilled: {n}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kitchen_zone_backfill.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /path/to/workspace/при
git add backend/app/scripts_backfill.py backend/scripts/backfill_kitchen_zone.py backend/tests/test_kitchen_zone_backfill.py
git commit -m "feat(analytics): бэкфилл kitchen_zone по имени блюда

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `zone_load` — кухонная нагрузка по часам + эндпоинт

**Files:**
- Modify: `backend/app/analytics_stages.py` (добавить функцию в конец)
- Modify: `backend/app/api.py` (новый эндпоинт после `/admin/kitchen-zones`, ~строка 565)
- Test: `backend/tests/test_zone_load.py`

**Interfaces:**
- Produces: `zone_load(orders, tz_offset_hours=0, zones=("pizza","rolls","hot")) -> dict` со схемой:
  `{ "zones": [...], "hours": [0..23], "cells": {zone: {hour:int -> {count, avg_sec}}}, "totals_by_zone": {zone: {count, avg_sec}} }`
- Consumes (в эндпоинте): `_orders_in_period`, `_parse_period`, `settings.iiko_tz_offset_hours`.

- [ ] **Step 1: Write the failing test**

Создать `backend/tests/test_zone_load.py`:

```python
from datetime import datetime
from app.analytics_stages import zone_load


class _It:
    def __init__(self, kitchen_zone, station, acc, ready):
        self.kitchen_zone = kitchen_zone
        self.station = station
        self.station_accepted_at = acc
        self.station_ready_at = ready


class _Ord:
    def __init__(self, items):
        self.items = items


def test_zone_load_buckets_by_local_hour_with_avg():
    # ready в 07:00 UTC -> локальный час 12 при offset=5.
    acc = datetime(2026, 6, 27, 6, 50)
    ready = datetime(2026, 6, 27, 7, 0)   # готовка 600с
    orders = [_Ord([_It("hot", "pizza", acc, ready)])]
    out = zone_load(orders, tz_offset_hours=5)

    assert out["zones"] == ["pizza", "rolls", "hot"]
    assert out["hours"] == list(range(24))
    assert out["cells"]["hot"][12] == {"count": 1, "avg_sec": 600.0}
    assert out["totals_by_zone"]["hot"] == {"count": 1, "avg_sec": 600.0}
    # пустая зона — пустые ячейки, total нулевой
    assert out["cells"]["rolls"] == {}
    assert out["totals_by_zone"]["rolls"] == {"count": 0, "avg_sec": None}


def test_zone_load_falls_back_to_station_when_kitchen_zone_null():
    acc = datetime(2026, 6, 27, 6, 50)
    ready = datetime(2026, 6, 27, 7, 0)
    orders = [_Ord([_It(None, "pizza", acc, ready)])]
    out = zone_load(orders, tz_offset_hours=5)
    assert out["cells"]["pizza"][12]["count"] == 1


def test_zone_load_skips_unready_and_other():
    acc = datetime(2026, 6, 27, 6, 50)
    orders = [
        _Ord([_It("hot", "pizza", acc, None)]),       # не готово — пропуск
        _Ord([_It("other", "other", acc, acc)]),       # other не в зонах — пропуск
    ]
    out = zone_load(orders, tz_offset_hours=5)
    assert out["totals_by_zone"]["hot"]["count"] == 0
    assert "other" not in out["cells"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_zone_load.py -v`
Expected: FAIL — `ImportError: cannot import name 'zone_load'`.

- [ ] **Step 3: Реализация**

В конец `backend/app/analytics_stages.py` добавить:

```python
def zone_load(orders, tz_offset_hours: int = 0,
              zones: tuple[str, ...] = ("pizza", "rolls", "hot")) -> dict:
    """Нагрузка кухни по часам: для каждой зоны и локального часа — число готовых
    блюд (count) и среднее время готовки (avg_sec). Час берётся по station_ready_at
    (момент завершения готовки), переведённому в локальное время (UTC+offset).
    Зона блюда — kitchen_zone (fallback на station). other и блюда без ready пропускаем.
    """
    cells: dict[str, dict[int, dict]] = {z: {} for z in zones}
    totals: dict[str, dict] = {z: {"count": 0, "sum": 0.0, "timed": 0} for z in zones}
    for o in orders:
        for it in o.items:
            z = it.kitchen_zone or it.station or "other"
            if z not in cells:
                continue
            ready = it.station_ready_at
            if ready is None:
                continue
            hour = (ready + timedelta(hours=tz_offset_hours)).hour
            dur = _dur(it.station_accepted_at, it.station_ready_at)
            cell = cells[z].setdefault(hour, {"count": 0, "sum": 0.0, "timed": 0})
            cell["count"] += 1
            totals[z]["count"] += 1
            if dur is not None:
                cell["sum"] += dur; cell["timed"] += 1
                totals[z]["sum"] += dur; totals[z]["timed"] += 1

    def _fin(d: dict) -> dict:
        return {"count": d["count"],
                "avg_sec": (d["sum"] / d["timed"]) if d["timed"] else None}

    return {
        "zones": list(zones),
        "hours": list(range(24)),
        "cells": {z: {h: _fin(c) for h, c in hrs.items()} for z, hrs in cells.items()},
        "totals_by_zone": {z: _fin(t) for z, t in totals.items()},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_zone_load.py -v`
Expected: PASS

- [ ] **Step 5: Эндпоинт**

В `backend/app/api.py` после функции `admin_kitchen_zones` (заканчивается ~строка 564) добавить:

```python
@app.get("/admin/zone-load")
async def admin_zone_load(org_id: str, from_: str | None = Query(None, alias="from"),
                          to: str | None = None,
                          session: AsyncSession = Depends(get_session)):
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    return _stages.zone_load(orders, tz_offset_hours=settings.iiko_tz_offset_hours)
```

- [ ] **Step 6: Smoke-тест эндпоинта**

Run: `cd backend && python -c "import app.api"` (импорт без ошибок).
Expected: без traceback.

- [ ] **Step 7: Commit**

```bash
cd /path/to/workspace/при
git add backend/app/analytics_stages.py backend/app/api.py backend/tests/test_zone_load.py
git commit -m "feat(analytics): zone_load — нагрузка кухни по часам (объём+ср.время) + эндпоинт

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `geo.py` — point-in-polygon и привязка к зоне

**Files:**
- Create: `backend/app/geo.py`
- Test: `backend/tests/test_geo.py`

**Interfaces:**
- Produces: `point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool` — ray-casting; `polygon` — список `[lat, lon]`.
- Produces: `assign_zone(lat, lon, zones: list[dict]) -> int | None` — id первой зоны, содержащей точку, иначе None. `zones[i]` имеет ключи `id` и `polygon`.

- [ ] **Step 1: Write the failing test**

Создать `backend/tests/test_geo.py`:

```python
from app.geo import point_in_polygon, assign_zone

# Квадрат вокруг центра Астаны (lat 51.1..51.2, lon 71.4..71.5).
SQUARE = [[51.1, 71.4], [51.1, 71.5], [51.2, 71.5], [51.2, 71.4]]


def test_point_inside():
    assert point_in_polygon(51.15, 71.45, SQUARE) is True


def test_point_outside():
    assert point_in_polygon(51.05, 71.45, SQUARE) is False


def test_assign_zone_first_match():
    zones = [{"id": 7, "polygon": SQUARE}]
    assert assign_zone(51.15, 71.45, zones) == 7
    assert assign_zone(0.0, 0.0, zones) is None


def test_assign_zone_handles_missing_coords():
    zones = [{"id": 7, "polygon": SQUARE}]
    assert assign_zone(None, 71.45, zones) is None
    assert assign_zone(51.15, None, zones) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_geo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.geo'`.

- [ ] **Step 3: Реализация**

Создать `backend/app/geo.py`:

```python
from __future__ import annotations


def point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
    """Ray-casting. polygon — замкнутый/незамкнутый список вершин [lat, lon].
    Точка на ребре считается внутри непостоянно — для аналитики это приемлемо."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i][0], polygon[i][1]   # lat, lon
        yj, xj = polygon[j][0], polygon[j][1]
        intersect = ((yi > lat) != (yj > lat)) and \
            (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi)
        if intersect:
            inside = not inside
        j = i
    return inside


def assign_zone(lat, lon, zones: list[dict]) -> int | None:
    """Id первой зоны, чей полигон содержит точку. None если нет координат/совпадений."""
    if lat is None or lon is None:
        return None
    for z in zones:
        poly = z.get("polygon") or []
        if point_in_polygon(lat, lon, poly):
            return z.get("id")
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_geo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /path/to/workspace/при
git add backend/app/geo.py backend/tests/test_geo.py
git commit -m "feat(geo): point_in_polygon + assign_zone для курьерских зон

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Модель `DeliveryZone` + CRUD-эндпоинты

**Files:**
- Modify: `backend/app/models.py` (новая модель в конец)
- Modify: `backend/app/api.py` (импорт + 4 эндпоинта в конец файла)
- Test: `backend/tests/test_delivery_zones_api.py`

**Interfaces:**
- Produces (модель): `DeliveryZone(id, org_id, name, polygon: JSON list[[lat,lon]], color, created_at, updated_at)`.
- Produces (HTTP):
  - `GET /admin/delivery-zones?org_id=` -> `[{id,name,polygon,color}]`
  - `POST /admin/delivery-zones` body `{org_id,name,polygon,color}` -> `{id,name,polygon,color}`
  - `PUT /admin/delivery-zones/{zone_id}` body `{name,polygon,color}` -> `{id,name,polygon,color}`
  - `DELETE /admin/delivery-zones/{zone_id}` -> `{ok: true}`

- [ ] **Step 1: Write the failing test**

Создать `backend/tests/test_delivery_zones_api.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.api import app
from app.db import get_session


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://t")
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delivery_zone_crud(client):
    async with client as c:
        poly = [[51.1, 71.4], [51.1, 71.5], [51.2, 71.5]]
        r = await c.post("/admin/delivery-zones",
                         json={"org_id": "demo", "name": "Центр", "polygon": poly, "color": "#f00"})
        assert r.status_code == 200
        zid = r.json()["id"]
        assert r.json()["name"] == "Центр"

        r = await c.get("/admin/delivery-zones?org_id=demo")
        assert len(r.json()) == 1

        r = await c.put(f"/admin/delivery-zones/{zid}",
                        json={"name": "Левый берег", "polygon": poly, "color": "#0f0"})
        assert r.json()["name"] == "Левый берег"

        r = await c.delete(f"/admin/delivery-zones/{zid}")
        assert r.json() == {"ok": True}
        r = await c.get("/admin/delivery-zones?org_id=demo")
        assert r.json() == []
```

> Если `httpx` не установлен — проверить `backend/tests/` на существующий паттерн вызова эндпоинтов (например через `app` напрямую) и повторить его. Иначе `pip install httpx` в окружении тестов.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_delivery_zones_api.py -v`
Expected: FAIL — 404 на POST (эндпоинта нет).

- [ ] **Step 3a: Модель**

В конец `backend/app/models.py` добавить:

```python
class DeliveryZone(Base):
    __tablename__ = "delivery_zones"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    polygon: Mapped[list] = mapped_column(JSON, default=list)  # [[lat, lon], ...]
    color: Mapped[str] = mapped_column(String, default="#2563eb")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 3b: Импорт модели в api.py**

В `backend/app/api.py` рядом с другими импортами моделей (после строки 285 `from app.models import Order as _Order, OrderItem as _OrderItem`) добавить:

```python
from app.models import DeliveryZone as _DeliveryZone
```

- [ ] **Step 3c: CRUD-эндпоинты**

В конец `backend/app/api.py` добавить:

```python
def _zone_out(z: "_DeliveryZone") -> dict:
    return {"id": z.id, "name": z.name, "polygon": z.polygon or [], "color": z.color}


@app.get("/admin/delivery-zones")
async def list_delivery_zones(org_id: str, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        _select(_DeliveryZone).where(_DeliveryZone.org_id == org_id)
        .order_by(_DeliveryZone.id))).scalars().all()
    return [_zone_out(z) for z in rows]


@app.post("/admin/delivery-zones")
async def create_delivery_zone(body: dict, session: AsyncSession = Depends(get_session)):
    z = _DeliveryZone(
        org_id=body.get("org_id", ""), name=body.get("name", ""),
        polygon=body.get("polygon") or [], color=body.get("color") or "#2563eb")
    session.add(z)
    await session.commit()
    await session.refresh(z)
    return _zone_out(z)


@app.put("/admin/delivery-zones/{zone_id}")
async def update_delivery_zone(zone_id: int, body: dict,
                               session: AsyncSession = Depends(get_session)):
    z = (await session.execute(
        _select(_DeliveryZone).where(_DeliveryZone.id == zone_id))).scalar_one_or_none()
    if z is None:
        raise HTTPException(status_code=404, detail="zone not found")
    if "name" in body: z.name = body["name"]
    if "polygon" in body: z.polygon = body["polygon"] or []
    if "color" in body: z.color = body["color"]
    z.updated_at = _dt.utcnow()
    await session.commit()
    await session.refresh(z)
    return _zone_out(z)


@app.delete("/admin/delivery-zones/{zone_id}")
async def delete_delivery_zone(zone_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(_sl_delete(_DeliveryZone).where(_DeliveryZone.id == zone_id))
    await session.commit()
    return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_delivery_zones_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /path/to/workspace/при
git add backend/app/models.py backend/app/api.py backend/tests/test_delivery_zones_api.py
git commit -m "feat(zones): модель DeliveryZone + CRUD /admin/delivery-zones

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `courier_zone_load` — курьерская нагрузка по районам + эндпоинт

**Files:**
- Modify: `backend/app/analytics_stages.py` (добавить функцию; +импорт geo)
- Modify: `backend/app/api.py` (новый эндпоинт)
- Test: `backend/tests/test_courier_zone_load.py`

**Interfaces:**
- Produces: `courier_zone_load(orders, zones: list[dict], tz_offset_hours=0) -> dict`:
  `{ "zones": [{id,name,color}, ..., {id:None,name:"Без зоны",color:...}], "hours":[0..23], "cells": {zone_key: {hour -> {count, avg_sec}}}, "totals_by_zone": {zone_key: {count, avg_sec}} }`
  где `zone_key` = `str(id)` для зон и `"none"` для «Без зоны».
- Consumes: `assign_zone` из `app.geo`; `zones[i]` = `{id,name,color,polygon}`.

- [ ] **Step 1: Write the failing test**

Создать `backend/tests/test_courier_zone_load.py`:

```python
from datetime import datetime
from app.analytics_stages import courier_zone_load

SQUARE = [[51.1, 71.4], [51.1, 71.5], [51.2, 71.5], [51.2, 71.4]]
ZONES = [{"id": 1, "name": "Центр", "color": "#f00", "polygon": SQUARE}]


class _Ord:
    def __init__(self, lat, lon, on_way, delivered):
        self.address_lat = lat
        self.address_lon = lon
        self.on_way_at = on_way
        self.delivered_at = delivered


def test_courier_load_assigns_zone_and_avg():
    on_way = datetime(2026, 6, 27, 7, 0)       # локальный час 12 при offset=5
    delivered = datetime(2026, 6, 27, 7, 30)   # доставка 1800с
    orders = [_Ord(51.15, 71.45, on_way, delivered)]
    out = courier_zone_load(orders, ZONES, tz_offset_hours=5)
    assert out["cells"]["1"][12] == {"count": 1, "avg_sec": 1800.0}
    assert out["totals_by_zone"]["1"] == {"count": 1, "avg_sec": 1800.0}
    # «Без зоны» присутствует в списке зон, но пустой
    assert out["zones"][-1]["id"] is None
    assert out["totals_by_zone"]["none"]["count"] == 0


def test_courier_load_no_coords_goes_to_none_zone():
    on_way = datetime(2026, 6, 27, 7, 0)
    delivered = datetime(2026, 6, 27, 7, 10)
    orders = [_Ord(None, None, on_way, delivered)]
    out = courier_zone_load(orders, ZONES, tz_offset_hours=5)
    assert out["cells"]["none"][12]["count"] == 1


def test_courier_load_skips_orders_without_on_way():
    orders = [_Ord(51.15, 71.45, None, None)]
    out = courier_zone_load(orders, ZONES, tz_offset_hours=5)
    assert out["totals_by_zone"]["1"]["count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_courier_zone_load.py -v`
Expected: FAIL — `ImportError: cannot import name 'courier_zone_load'`.

- [ ] **Step 3: Реализация**

В `backend/app/analytics_stages.py`: вверху рядом с прочими импортами добавить:

```python
from app.geo import assign_zone
```

В конец файла добавить:

```python
def courier_zone_load(orders, zones: list[dict], tz_offset_hours: int = 0) -> dict:
    """Нагрузка курьеров по районам города и часам. Заказ привязывается к зоне по
    координатам доставки (address_lat/lon). Час — по on_way_at (момент выезда) в
    локальном времени. avg_sec — среднее время доставки (on_way_at -> delivered_at).
    Заказы без on_way_at пропускаем. Без координат/совпадения — псевдо-зона «Без зоны».
    """
    keys = [str(z["id"]) for z in zones] + ["none"]
    cells: dict[str, dict[int, dict]] = {k: {} for k in keys}
    totals: dict[str, dict] = {k: {"count": 0, "sum": 0.0, "timed": 0} for k in keys}
    for o in orders:
        if o.on_way_at is None:
            continue
        zid = assign_zone(o.address_lat, o.address_lon, zones)
        key = str(zid) if zid is not None else "none"
        hour = (o.on_way_at + timedelta(hours=tz_offset_hours)).hour
        dur = _dur(o.on_way_at, o.delivered_at)
        cell = cells[key].setdefault(hour, {"count": 0, "sum": 0.0, "timed": 0})
        cell["count"] += 1
        totals[key]["count"] += 1
        if dur is not None:
            cell["sum"] += dur; cell["timed"] += 1
            totals[key]["sum"] += dur; totals[key]["timed"] += 1

    def _fin(d: dict) -> dict:
        return {"count": d["count"],
                "avg_sec": (d["sum"] / d["timed"]) if d["timed"] else None}

    zones_out = [{"id": z["id"], "name": z["name"], "color": z.get("color", "#2563eb")}
                 for z in zones]
    zones_out.append({"id": None, "name": "Без зоны", "color": "#94a3b8"})
    return {
        "zones": zones_out,
        "hours": list(range(24)),
        "cells": {k: {h: _fin(c) for h, c in hrs.items()} for k, hrs in cells.items()},
        "totals_by_zone": {k: _fin(t) for k, t in totals.items()},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_courier_zone_load.py -v`
Expected: PASS

- [ ] **Step 5: Эндпоинт**

В `backend/app/api.py` после эндпоинта `admin_zone_load` добавить:

```python
@app.get("/admin/courier-zone-load")
async def admin_courier_zone_load(org_id: str, from_: str | None = Query(None, alias="from"),
                                  to: str | None = None,
                                  session: AsyncSession = Depends(get_session)):
    dt_from, dt_to = _parse_period(from_, to)
    orders = await _orders_in_period(session, org_id, dt_from, dt_to)
    zrows = (await session.execute(
        _select(_DeliveryZone).where(_DeliveryZone.org_id == org_id))).scalars().all()
    zones = [{"id": z.id, "name": z.name, "color": z.color, "polygon": z.polygon or []}
             for z in zrows]
    return _stages.courier_zone_load(orders, zones,
                                     tz_offset_hours=settings.iiko_tz_offset_hours)
```

- [ ] **Step 6: Прогон всего бэкенд-сьюта**

Run: `cd backend && python -m pytest -q`
Expected: PASS (все тесты, включая старые).

- [ ] **Step 7: Commit**

```bash
cd /path/to/workspace/при
git add backend/app/analytics_stages.py backend/app/api.py backend/tests/test_courier_zone_load.py
git commit -m "feat(analytics): courier_zone_load — нагрузка по районам города + эндпоинт

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Фронтенд — API-обёртки + leaflet-draw в index.html

**Files:**
- Modify: `frontend/index.html:11-12` (после leaflet css/js)
- Modify: `frontend/src/lib/api.js:91-103` (блок аналитики)

**Interfaces:**
- Produces: `api.zoneLoad(orgId, from, to)`, `api.courierZoneLoad(orgId, from, to)`,
  `api.deliveryZones(orgId)`, `api.createDeliveryZone(body)`, `api.updateDeliveryZone(id, body)`, `api.deleteDeliveryZone(id)`.

- [ ] **Step 1: leaflet-draw через CDN**

В `frontend/index.html` после строки 12 (`<script src="...leaflet.js"></script>`) добавить:

```html
    <link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css" />
    <script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
```

- [ ] **Step 2: API-обёртки**

Проверить, как объявлены `post`/`get`/`put`/`del` в `frontend/src/lib/api.js` (вверху файла). Если нет `put`/`del` — добавить по образцу `post` (fetch с методом PUT/DELETE). Затем в объект `api`, в блок «Аналитика» (после `anomalies`, ~строка 103), добавить:

```javascript
  zoneLoad: (orgId, from, to) =>
    get(`/admin/zone-load?org_id=${orgId}&from=${from}&to=${to}`),
  courierZoneLoad: (orgId, from, to) =>
    get(`/admin/courier-zone-load?org_id=${orgId}&from=${from}&to=${to}`),
  deliveryZones: (orgId) =>
    get(`/admin/delivery-zones?org_id=${encodeURIComponent(orgId)}`),
  createDeliveryZone: (body) => post(`/admin/delivery-zones`, body),
  updateDeliveryZone: (id, body) => put(`/admin/delivery-zones/${id}`, body),
  deleteDeliveryZone: (id) => del(`/admin/delivery-zones/${id}`),
```

> Если функций `put`/`del` нет — добавить рядом с `post`:
> ```javascript
> const put = (path, body) => fetch(`${BASE}${path}`, {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}).then(handle);
> const del = (path) => fetch(`${BASE}${path}`, {method:"DELETE"}).then(handle);
> ```
> Сверить точную сигнатуру `handle`/`post` в файле и повторить стиль.

- [ ] **Step 3: Проверка сборки**

Run: `cd /path/to/workspace/при/frontend && npx vite build`
Expected: сборка без ошибок.

- [ ] **Step 4: Commit**

```bash
cd /path/to/workspace/при
git add frontend/index.html frontend/src/lib/api.js
git commit -m "feat(frontend): API-обёртки зональной аналитики + leaflet-draw

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Фронтенд — блоки нагрузки в Analytics.jsx

**Files:**
- Modify: `frontend/src/screens/Analytics.jsx` (добавить два компонента и вставить вверху основного рендера)

**Interfaces:**
- Consumes: `api.zoneLoad`, `api.courierZoneLoad`; формат ответа — см. Task 3/6.

- [ ] **Step 1: Компонент ZoneLoadTable**

В `frontend/src/screens/Analytics.jsx` добавить компонент (рядом с другими секционными компонентами). Лейблы зон — локально (rolls→Япония):

```jsx
const KZONE_LABEL = { pizza: "Пицца", rolls: "Япония", hot: "Горячее" };

function loadCellStyle(count, max) {
  if (!count) return {};
  const r = max > 0 ? count / max : 0;
  const bg = r >= 0.66 ? "#fee2e2" : r >= 0.33 ? "#fef3c7" : "#dcfce7";
  return { background: bg };
}

function HoursHeatTable({ title, data, rowLabel }) {
  if (!data) return null;
  const { zones, hours, cells, totals_by_zone } = data;
  const keyOf = (z) => (typeof z === "string" ? z : z.id == null ? "none" : String(z.id));
  const max = Math.max(1, ...zones.flatMap((z) =>
    hours.map((h) => cells[keyOf(z)]?.[h]?.count || 0)));
  return (
    <section className="card">
      <h2>{title}</h2>
      <div style={{ overflowX: "auto" }}>
        <table className="heat">
          <thead>
            <tr><th>Зона</th>{hours.map((h) => <th key={h}>{h}</th>)}<th>Итого</th></tr>
          </thead>
          <tbody>
            {zones.map((z) => {
              const k = keyOf(z);
              const tot = totals_by_zone[k] || { count: 0, avg_sec: null };
              return (
                <tr key={k}>
                  <td>{rowLabel(z)}</td>
                  {hours.map((h) => {
                    const c = cells[k]?.[h];
                    return (
                      <td key={h} style={loadCellStyle(c?.count, max)} title={c ? `${c.count} · ${sec2min(c.avg_sec)} мин` : ""}>
                        {c ? <span>{c.count}<br/><small>{sec2min(c.avg_sec)}м</small></span> : ""}
                      </td>
                    );
                  })}
                  <td><b>{tot.count}</b><br/><small>{sec2min(tot.avg_sec)}м</small></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
```

- [ ] **Step 2: Загрузка данных и вставка блоков**

В основном компоненте `Analytics` добавить state и загрузку (рядом с существующими загрузками по `from`/`to`):

```jsx
const [zoneLoadData, setZoneLoadData] = useState(null);
const [courierLoadData, setCourierLoadData] = useState(null);

useEffect(() => {
  const org = getOrgId();
  api.zoneLoad(org, range.from, range.to).then(setZoneLoadData).catch(() => setZoneLoadData(null));
  api.courierZoneLoad(org, range.from, range.to).then(setCourierLoadData).catch(() => setCourierLoadData(null));
}, [range.from, range.to]);
```

> `range`/`from`/`to` — использовать те же имена переменных диапазона, что уже есть в компоненте (свериться с текущим кодом; выше показан `presetRange`). Если диапазон хранится иначе — подставить фактические имена.

В JSX, в самом верху основного блока (до существующих секций «Этапы заказа»), вставить:

```jsx
<HoursHeatTable
  title="Нагрузка по зонам кухни (объём · ср. время готовки)"
  data={zoneLoadData}
  rowLabel={(z) => KZONE_LABEL[z] || z} />
<div style={{ marginBottom: 12 }}>
  <Link to="/delivery-zones">⚙ Настроить зоны города</Link>
</div>
<HoursHeatTable
  title="Курьер: нагрузка по районам (объём · ср. время доставки)"
  data={courierLoadData}
  rowLabel={(z) => z.name} />
```

- [ ] **Step 3: Минимальные стили**

В `frontend/src/admin.css` добавить:

```css
.heat { border-collapse: collapse; font-size: 12px; }
.heat th, .heat td { border: 1px solid #e2e8f0; padding: 2px 4px; text-align: center; min-width: 28px; }
.heat td small { color: #64748b; font-size: 10px; }
.heat th:first-child, .heat td:first-child { text-align: left; white-space: nowrap; font-weight: 600; }
```

- [ ] **Step 4: Manual verification**

Запустить бэкенд и фронт, открыть `/analytics`:
```bash
cd /path/to/workspace/при/backend && python -m uvicorn app.main:app --port 8000 &
cd /path/to/workspace/при/frontend && npx vite
```
Проверить: вверху страницы две таблицы «зона × час» с числами и минутами; кухонные строки — Пицца/Япония/Горячее; курьерская включает строку «Без зоны». Ошибок в консоли нет. (Можно через Playwright MCP: navigate на `http://localhost:5173/analytics`, snapshot.)
Expected: блоки отрисованы, старые секции аналитики на месте ниже.

- [ ] **Step 5: Commit**

```bash
cd /path/to/workspace/при
git add frontend/src/screens/Analytics.jsx frontend/src/admin.css
git commit -m "feat(frontend): блоки нагрузки по зонам кухни и районам в аналитике

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Фронтенд — экран-редактор полигонов DeliveryZones

**Files:**
- Create: `frontend/src/screens/DeliveryZones.jsx`
- Modify: `frontend/src/App.jsx` (импорт + маршрут `/delivery-zones`)

**Interfaces:**
- Consumes: `api.deliveryZones`, `api.createDeliveryZone`, `api.updateDeliveryZone`, `api.deleteDeliveryZone`; глобал `window.L` + `L.Control.Draw`/`L.Draw` из leaflet-draw.

- [ ] **Step 1: Экран-редактор**

Создать `frontend/src/screens/DeliveryZones.jsx`:

```jsx
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api.js";
import { getOrgId } from "../lib/auth.js";

const ASTANA = [51.16, 71.47];
const COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2"];

export default function DeliveryZones() {
  const mapRef = useRef(null);
  const drawnRef = useRef(null);   // L.FeatureGroup с полигонами
  const [zones, setZones] = useState([]);
  const [err, setErr] = useState("");
  const hasMap = typeof window !== "undefined" && !!window.L;

  async function reload() {
    try { setZones(await api.deliveryZones(getOrgId())); }
    catch (e) { setErr(e.message || String(e)); }
  }
  useEffect(() => { reload(); }, []);

  // init map + draw control
  useEffect(() => {
    const L = window.L;
    if (!L) return;
    const map = L.map("zones-map").setView(ASTANA, 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { attribution: "© OpenStreetMap", maxZoom: 19 }).addTo(map);
    const drawn = new L.FeatureGroup().addTo(map);
    drawnRef.current = drawn;
    const ctrl = new L.Control.Draw({
      edit: { featureGroup: drawn },
      draw: { polygon: true, marker: false, polyline: false,
              circle: false, rectangle: false, circlemarker: false },
    });
    map.addControl(ctrl);

    map.on(L.Draw.Event.CREATED, async (e) => {
      const latlngs = e.layer.getLatLngs()[0].map((p) => [p.lat, p.lng]);
      const color = COLORS[zonesLenRef.current % COLORS.length];
      const name = window.prompt("Название района:", "Район") || "Район";
      await api.createDeliveryZone({ org_id: getOrgId(), name, polygon: latlngs, color });
      await reload();
    });

    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, [hasMap]);

  // держим актуальную длину для индекса цвета внутри обработчика
  const zonesLenRef = useRef(0);
  useEffect(() => { zonesLenRef.current = zones.length; }, [zones]);

  // перерисовываем сохранённые полигоны при изменении списка
  useEffect(() => {
    const L = window.L;
    const drawn = drawnRef.current;
    if (!L || !drawn) return;
    drawn.clearLayers();
    for (const z of zones) {
      if (!z.polygon || z.polygon.length < 3) continue;
      L.polygon(z.polygon, { color: z.color })
        .bindTooltip(z.name, { permanent: true })
        .addTo(drawn);
    }
  }, [zones]);

  async function remove(id) {
    if (!window.confirm("Удалить зону?")) return;
    await api.deleteDeliveryZone(id);
    await reload();
  }

  return (
    <div className="admin">
      <header style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <Link to="/analytics">← Назад</Link>
        <h1>Зоны доставки города</h1>
      </header>
      {err && <p style={{ color: "#dc2626" }}>Ошибка: {err}</p>}
      {!hasMap && <p>Карта недоступна (Leaflet не загрузился).</p>}
      <div id="zones-map" style={{ height: "60vh", width: "100%", borderRadius: 8 }} />
      <ul>
        {zones.map((z) => (
          <li key={z.id} style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ width: 12, height: 12, background: z.color, display: "inline-block", borderRadius: 3 }} />
            {z.name} ({z.polygon?.length || 0} точек)
            <button onClick={() => remove(z.id)}>Удалить</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: Маршрут**

В `frontend/src/App.jsx`:
- добавить импорт рядом с другими экранами: `import DeliveryZones from "./screens/DeliveryZones.jsx";`
- добавить маршрут рядом с `/analytics` (внутри `<Routes>`):
  ```jsx
  <Route path="/delivery-zones" element={<RequireManager><DeliveryZones /></RequireManager>} />
  ```

- [ ] **Step 3: Сборка**

Run: `cd /path/to/workspace/при/frontend && npx vite build`
Expected: без ошибок.

- [ ] **Step 4: Manual verification**

Открыть `/delivery-zones` (vite dev). Проверить: карта Астаны грузится; в левом верхнем углу карты — инструмент рисования полигона; нарисовать полигон → запрос имени → зона появилась в списке и подписана на карте; «Удалить» убирает зону. Затем на `/analytics` курьерская таблица показывает заказы в этом районе.
> Через Playwright MCP: navigate `http://localhost:5173/delivery-zones`, snapshot, проверить наличие `#zones-map` и контрола Draw.
Expected: рисование/сохранение/удаление работают, в консоли нет ошибок.

- [ ] **Step 5: Commit**

```bash
cd /path/to/workspace/при
git add frontend/src/screens/DeliveryZones.jsx frontend/src/App.jsx
git commit -m "feat(frontend): редактор курьерских зон города на Leaflet+draw

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Финальная проверка

- [ ] `cd backend && python -m pytest -q` — весь сьют зелёный.
- [ ] `cd frontend && npx vite build` — сборка проходит.
- [ ] Ручной прогон: создать зону на `/delivery-zones`, открыть `/analytics` — обе таблицы заполнены, старые блоки на месте.
- [ ] Прогнать бэкфилл на проде/деве один раз: `cd backend && python -m scripts.backfill_kitchen_zone`.

## Замечания по реализации (на что смотреть исполнителю)

- **`schemas.py`**: точные поля `NormalizedOrder`/`WebhookItem` — единственное место, где тест Task 1 может не собраться. Свериться перед запуском.
- **`api.js` стиль**: проверить наличие `put`/`del`-хелперов; если нет — добавить (Task 7 Step 2).
- **Имена переменных диапазона** в `Analytics.jsx` (Task 8 Step 2) — подставить фактические из текущего кода.
- **leaflet-draw API**: события `L.Draw.Event.CREATED`; контрол `L.Control.Draw`. Версия 1.0.4 совместима с Leaflet 1.9.
