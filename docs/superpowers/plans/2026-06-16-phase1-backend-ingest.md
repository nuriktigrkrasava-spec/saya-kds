# Phase 1 — Backend «движок»: real-time ingest + realtime + RMS login — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Стоящий локально FastAPI-сервис, который принимает вебхук iiko `DeliveryOrderUpdate`, дозапрашивает состав через `deliveries/by_id`, раскладывает позиции по зонам (rolls/pizza/hot/other), сохраняет в PostgreSQL идемпотентно и вещает по Socket.IO в комнаты `org:<id>:<zone>`; плюс вход сотрудников через RMS-сервер.

**Architecture:** Backend = Python 3.12 + FastAPI + python-socketio (ASGI) + httpx (вызовы iiko) + SQLAlchemy async. Источники строго разделены: заказы — iiko Cloud по apiLogin; сотрудники/филиалы — self-hosted RMS. `organizationId` берётся из самого вебхука, список филиалов из Cloud не тянем. Real-time приход моделируется вебхуком; для локальной отладки есть ручной триггер «дёрнуть by_id». Frontend — отдельный план.

**Tech Stack:** Python 3.12, FastAPI, python-socketio, httpx, SQLAlchemy 2 (async) + aiosqlite/asyncpg, PostgreSQL (Docker), pytest + pytest-asyncio + respx (мок httpx).

---

## File Structure

```
при/
├─ docker-compose.yml            # PostgreSQL для локали
├─ backend/
│  ├─ pyproject.toml             # зависимости + pytest config
│  ├─ .env.example               # шаблон секретов
│  ├─ app/
│  │  ├─ __init__.py
│  │  ├─ config.py               # настройки из ENV (pydantic-settings)
│  │  ├─ db.py                   # async engine + session + Base
│  │  ├─ models.py               # таблицы SQLAlchemy
│  │  ├─ schemas.py              # pydantic DTO (WebhookItem, NormalizedOrder)
│  │  ├─ iiko_cloud.py           # перенос проверенной механики: token, by_id, nomenclature, webhook
│  │  ├─ stations.py             # маппинг блюдо→зона (категории + исключения)
│  │  ├─ ingest.py               # upsert заказа+позиций, идемпотентно
│  │  ├─ realtime.py             # Socket.IO сервер + комнаты org:<id>:<zone>
│  │  ├─ rms.py                  # RMS: auth/logout, employees
│  │  ├─ api.py                  # REST: /webhooks/iiko/cloud, /auth/login, действия зон
│  │  └─ main.py                 # сборка ASGI (FastAPI + socketio)
│  └─ tests/
│     ├─ conftest.py             # фикстуры: in-memory sqlite, тестовый клиент
│     ├─ test_iiko_cloud.py
│     ├─ test_stations.py
│     ├─ test_ingest.py
│     ├─ test_webhook.py
│     ├─ test_rms.py
│     └─ test_actions.py
└─ docs/superpowers/...
```

Каждый файл — одна ответственность. Тесты на каждый модуль изолированы (sqlite in-memory, httpx замокан через respx — реальные сети в тестах не дёргаем).

---

## Task 1: Каркас проекта, конфиг, зависимости, Postgres

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.env.example`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `docker-compose.yml`
- Create: `backend/tests/__init__.py`

- [ ] **Step 1: Создать `backend/pyproject.toml`**

```toml
[project]
name = "saya-kds-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "python-socketio>=5.11",
    "httpx>=0.27",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",
    "asyncpg>=0.29",
    "pydantic-settings>=2.4",
    "pyjwt>=2.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "respx>=0.21", "httpx>=0.27"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Создать `backend/.env.example`**

```bash
# iiko Cloud (заказы real-time). ВНИМАНИЕ: эндпоинт /api/1/access_token (без /auth/)
CLOUD_API_LOGIN=replace-me
# Общий секрет вебхука: iiko шлёт ?key=<этот секрет> на /webhooks/iiko/cloud
WEBHOOK_SECRET=replace-me
# RMS self-hosted (сотрудники/филиалы)
RMS_BASE=https://saya-co.iiko.it:443/resto
RMS_LOGIN=replace-me
RMS_PASSWORD=replace-me
# JWT
JWT_SECRET=dev-only-change-me
# БД: локально sqlite по умолчанию; в проде — postgresql+asyncpg://...
DATABASE_URL=sqlite+aiosqlite:///./kitchen.db
```

- [ ] **Step 3: Создать `backend/app/__init__.py` и `backend/tests/__init__.py`** (пустые файлы)

```python
```

- [ ] **Step 4: Создать `backend/app/config.py`**

```python
from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cloud_api_login: str = ""
    webhook_secret: str = ""
    rms_base: str = "https://saya-co.iiko.it:443/resto"
    rms_login: str = ""
    rms_password: str = ""
    jwt_secret: str = "dev-only-change-me"
    database_url: str = "sqlite+aiosqlite:///./kitchen.db"


settings = Settings()
```

- [ ] **Step 5: Создать `docker-compose.yml`** (для прод-подобной Postgres локально)

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: kds
      POSTGRES_PASSWORD: kds
      POSTGRES_DB: kds
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
volumes:
  pgdata:
```

- [ ] **Step 6: Установить зависимости и убедиться, что окружение собирается**

Run: `cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"`
Expected: установка без ошибок; `python -c "import fastapi, socketio, sqlalchemy, httpx"` завершается без вывода.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/.env.example backend/app/__init__.py backend/tests/__init__.py backend/app/config.py docker-compose.yml
git commit -m "chore: backend scaffold, config, docker postgres"
```

---

## Task 2: Слой БД и модели

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/app/models.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_models.py`

- [ ] **Step 1: Создать `backend/app/db.py`**

```python
from __future__ import annotations
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
```

- [ ] **Step 2: Создать `backend/app/models.py`**

```python
from __future__ import annotations
from datetime import datetime
from sqlalchemy import String, Float, Integer, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base


class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    server_department_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    server_employee_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="")
    org_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    iiko_order_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    org_id: Mapped[str] = mapped_column(String, index=True)
    number: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="")
    customer_name: Mapped[str] = mapped_column(String, default="")
    phone: Mapped[str] = mapped_column(String, default="")
    address: Mapped[str] = mapped_column(String, default="")
    address_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    address_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    assembled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    on_way_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    iiko_item_id: Mapped[str] = mapped_column(String, default="")
    product_id: Mapped[str] = mapped_column(String, default="")
    name: Mapped[str] = mapped_column(String, default="")
    amount: Mapped[float] = mapped_column(Float, default=1)
    station: Mapped[str] = mapped_column(String, default="other")
    combo_id: Mapped[str | None] = mapped_column(String, nullable=True)
    is_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    station_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    station_ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    order: Mapped["Order"] = relationship(back_populates="items")


class ProductStationMap(Base):
    __tablename__ = "product_station_map"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    category_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    station: Mapped[str] = mapped_column(String)
    __table_args__ = (UniqueConstraint("product_id", "category_id", name="uq_psm"),)


class OrderEvent(Base):
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 3: Создать `backend/tests/conftest.py`** (in-memory БД + сессия на каждый тест)

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from app.db import Base
import app.models  # noqa: F401  (регистрирует таблицы)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        yield s
    await engine.dispose()
```

- [ ] **Step 4: Создать падающий тест `backend/tests/test_models.py`**

```python
from sqlalchemy import select
from app.models import Order, OrderItem


async def test_order_with_items_persists(session):
    order = Order(iiko_order_id="o1", org_id="org-1", number="101")
    order.items.append(OrderItem(product_id="p1", name="Ролл", station="rolls"))
    session.add(order)
    await session.commit()

    got = (await session.execute(select(Order).where(Order.iiko_order_id == "o1"))).scalar_one()
    assert got.org_id == "org-1"
    assert len(got.items) == 1
    assert got.items[0].station == "rolls"
```

- [ ] **Step 5: Запустить тест — должен пройти (модели уже написаны)**

Run: `cd backend && pytest tests/test_models.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/app/models.py backend/tests/conftest.py backend/tests/test_models.py
git commit -m "feat: db layer + ORM models"
```

---

## Task 3: Клиент iiko Cloud (перенос проверенной механики)

**Files:**
- Create: `backend/app/schemas.py`
- Create: `backend/app/iiko_cloud.py`
- Test: `backend/tests/test_iiko_cloud.py`

- [ ] **Step 1: Создать `backend/app/schemas.py`**

```python
from __future__ import annotations
from pydantic import BaseModel, Field


class WebhookItem(BaseModel):
    product_id: str = ""
    category_id: str | None = None
    name: str = ""
    amount: float = 1
    combo_id: str | None = None


class NormalizedOrder(BaseModel):
    iiko_order_id: str
    org_id: str
    number: str = ""
    status: str = ""
    customer: str = ""
    phone: str = ""
    address: str = ""
    lat: float | None = None
    lon: float | None = None
    total: float | None = None
    items: list[WebhookItem] = Field(default_factory=list)
```

- [ ] **Step 2: Написать падающий тест `backend/tests/test_iiko_cloud.py`**

```python
import respx
from httpx import Response
from app import iiko_cloud as c
from app.config import settings


@respx.mock
async def test_get_token_uses_access_token_endpoint():
    settings.cloud_api_login = "key123"
    route = respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    token = await c.get_token()
    assert token == "T"
    assert route.called
    assert route.calls.last.request.read() == b'{"apiLogin": "key123"}' or b'"apiLogin"' in route.calls.last.request.read()


@respx.mock
async def test_fetch_delivery_by_id_returns_first_order():
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    respx.post("https://api-ru.iiko.services/api/1/deliveries/by_id").mock(
        return_value=Response(200, json={"orders": [{"id": "o1", "order": {"number": "101"}}]}))
    order = await c.fetch_delivery_by_id("org-1", "o1")
    assert order["id"] == "o1"


def test_normalize_full_order_extracts_items_and_combo():
    wrapper = {
        "id": "o1",
        "order": {
            "number": "101",
            "customer": {"name": "Иван"},
            "phone": "+7700",
            "deliveryPoint": {
                "address": {"street": {"name": "Абая"}, "house": "1"},
                "coordinates": {"latitude": 51.1, "longitude": 71.4},
            },
            "sum": 5000,
            "items": [
                {"type": "Product", "amount": 2,
                 "product": {"id": "p-roll", "name": "Филадельфия",
                             "category": {"id": "cat-rolls"}}},
                {"type": "Product", "amount": 1,
                 "product": {"id": "p-pizza", "name": "Маргарита",
                             "category": {"id": "cat-pizza"}},
                 "comboInformation": {"comboId": "combo-7"}},
            ],
        },
    }
    n = c.normalize_full_order(wrapper, "org-1")
    assert n.iiko_order_id == "o1"
    assert n.org_id == "org-1"
    assert n.address == "Абая"
    assert n.lat == 51.1 and n.lon == 71.4
    assert len(n.items) == 2
    assert n.items[0].product_id == "p-roll" and n.items[0].amount == 2
    assert n.items[0].category_id == "cat-rolls"
    assert n.items[1].combo_id == "combo-7"
```

- [ ] **Step 3: Запустить тест — убедиться, что падает**

Run: `cd backend && pytest tests/test_iiko_cloud.py -v`
Expected: FAIL (модуль `iiko_cloud` ещё не имеет нужных функций).

- [ ] **Step 4: Создать `backend/app/iiko_cloud.py`**

```python
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
```

- [ ] **Step 5: Запустить тест — должен пройти**

Run: `cd backend && pytest tests/test_iiko_cloud.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/iiko_cloud.py backend/tests/test_iiko_cloud.py
git commit -m "feat: iiko Cloud client (token/by_id/nomenclature/webhook) + normalize"
```

---

## Task 4: Маппинг блюдо → зона (категории + исключения)

**Files:**
- Create: `backend/app/stations.py`
- Test: `backend/tests/test_stations.py`

Зоны: `rolls | pizza | hot | other`. Правила приоритета: точное исключение по `product_id`
важнее правила по `category_id`; если ничего не совпало — `other`.

- [ ] **Step 1: Написать падающий тест `backend/tests/test_stations.py`**

```python
from app.models import ProductStationMap
from app import stations
from app.schemas import WebhookItem


async def test_product_exception_wins_over_category(session):
    session.add(ProductStationMap(category_id="cat-rolls", station="rolls"))
    session.add(ProductStationMap(product_id="p-special", station="hot"))
    await session.commit()
    resolver = await stations.build_resolver(session)

    roll = WebhookItem(product_id="p1", category_id="cat-rolls", name="Ролл")
    special = WebhookItem(product_id="p-special", category_id="cat-rolls", name="Спец")
    unknown = WebhookItem(product_id="pX", category_id="cat-unknown", name="?")

    assert resolver(roll) == "rolls"
    assert resolver(special) == "hot"   # исключение по product_id важнее категории
    assert resolver(unknown) == "other"
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd backend && pytest tests/test_stations.py -v`
Expected: FAIL (`stations.build_resolver` не существует).

- [ ] **Step 3: Создать `backend/app/stations.py`**

```python
from __future__ import annotations
from typing import Callable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ProductStationMap
from app.schemas import WebhookItem

VALID_STATIONS = {"rolls", "pizza", "hot", "other"}


async def build_resolver(session: AsyncSession) -> Callable[[WebhookItem], str]:
    rows = (await session.execute(select(ProductStationMap))).scalars().all()
    by_product = {r.product_id: r.station for r in rows if r.product_id}
    by_category = {r.category_id: r.station for r in rows if r.category_id and not r.product_id}

    def resolve(item: WebhookItem) -> str:
        if item.product_id in by_product:
            return by_product[item.product_id]
        if item.category_id and item.category_id in by_category:
            return by_category[item.category_id]
        return "other"

    return resolve
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `cd backend && pytest tests/test_stations.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/stations.py backend/tests/test_stations.py
git commit -m "feat: product->station resolver (category rules + product overrides)"
```

---

## Task 5: Идемпотентный ingest заказа

**Files:**
- Create: `backend/app/ingest.py`
- Test: `backend/tests/test_ingest.py`

- [ ] **Step 1: Написать падающий тест `backend/tests/test_ingest.py`**

```python
from sqlalchemy import select, func
from app import ingest
from app.models import Order, OrderItem, ProductStationMap
from app.schemas import NormalizedOrder, WebhookItem


def _order(number="101"):
    return NormalizedOrder(
        iiko_order_id="o1", org_id="org-1", number=number, customer="Иван",
        items=[
            WebhookItem(product_id="p-roll", category_id="cat-rolls", name="Ролл", amount=2),
            WebhookItem(product_id="p-pizza", category_id="cat-pizza", name="Пицца", amount=1),
        ],
    )


async def test_ingest_creates_order_and_assigns_stations(session):
    session.add(ProductStationMap(category_id="cat-rolls", station="rolls"))
    session.add(ProductStationMap(category_id="cat-pizza", station="pizza"))
    await session.commit()

    order = await ingest.ingest_order(session, _order())
    stations = {i.name: i.station for i in order.items}
    assert stations == {"Ролл": "rolls", "Пицца": "pizza"}


async def test_ingest_is_idempotent_on_same_iiko_id(session):
    await ingest.ingest_order(session, _order(number="101"))
    await ingest.ingest_order(session, _order(number="999"))  # тот же iiko_order_id

    count = (await session.execute(select(func.count()).select_from(Order))).scalar_one()
    items = (await session.execute(select(func.count()).select_from(OrderItem))).scalar_one()
    assert count == 1            # не задублировался
    got = (await session.execute(select(Order))).scalar_one()
    assert got.number == "999"   # обновился
    assert items == 2            # позиции пересозданы, а не удвоены
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd backend && pytest tests/test_ingest.py -v`
Expected: FAIL (`ingest.ingest_order` не существует).

- [ ] **Step 3: Создать `backend/app/ingest.py`**

```python
from __future__ import annotations
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Order, OrderItem
from app.schemas import NormalizedOrder
from app.stations import build_resolver


async def ingest_order(session: AsyncSession, data: NormalizedOrder) -> Order:
    """Идемпотентно по iiko_order_id: создаёт или обновляет заказ и пересоздаёт позиции."""
    resolve = await build_resolver(session)

    existing = (await session.execute(
        select(Order).where(Order.iiko_order_id == data.iiko_order_id))).scalar_one_or_none()

    if existing is None:
        order = Order(iiko_order_id=data.iiko_order_id)
        session.add(order)
    else:
        order = existing
        await session.execute(delete(OrderItem).where(OrderItem.order_id == order.id))

    order.org_id = data.org_id
    order.number = data.number
    order.status = data.status
    order.customer_name = data.customer
    order.phone = data.phone
    order.address = data.address
    order.address_lat = data.lat
    order.address_lon = data.lon
    order.total = data.total

    await session.flush()  # гарантирует order.id

    for it in data.items:
        session.add(OrderItem(
            order_id=order.id,
            product_id=it.product_id,
            name=it.name,
            amount=it.amount,
            combo_id=it.combo_id,
            station=resolve(it),
        ))
    await session.commit()
    await session.refresh(order)
    return order
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `cd backend && pytest tests/test_ingest.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingest.py backend/tests/test_ingest.py
git commit -m "feat: idempotent order ingest with station assignment"
```

---

## Task 6: Socket.IO realtime + комнаты org:<id>:<zone>

**Files:**
- Create: `backend/app/realtime.py`
- Test: `backend/tests/test_realtime.py`

Здесь проверяем чистую функцию формирования имени комнаты и сериализацию заказа в payload
(сетевой Socket.IO покроем интеграционно в Task 7/8). Имя комнаты: `org:<org_id>:<zone>`.

- [ ] **Step 1: Написать падающий тест `backend/tests/test_realtime.py`**

```python
from app import realtime
from app.models import Order, OrderItem


def test_room_name():
    assert realtime.room("org-1", "rolls") == "org:org-1:rolls"


def test_order_payload_groups_items_by_station():
    order = Order(id=5, iiko_order_id="o1", org_id="org-1", number="101", customer_name="Иван")
    order.items = [
        OrderItem(id=1, product_id="p1", name="Ролл", station="rolls", amount=2),
        OrderItem(id=2, product_id="p2", name="Пицца", station="pizza", amount=1),
    ]
    p = realtime.order_payload(order)
    assert p["number"] == "101"
    assert p["org_id"] == "org-1"
    assert {i["name"] for i in p["items"] if i["station"] == "rolls"} == {"Ролл"}
    assert p["items"][0]["amount"] == 2
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd backend && pytest tests/test_realtime.py -v`
Expected: FAIL (`realtime` не существует).

- [ ] **Step 3: Создать `backend/app/realtime.py`**

```python
from __future__ import annotations
import socketio
from app.models import Order

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")

ZONES = ("rolls", "pizza", "hot", "other")


def room(org_id: str, zone: str) -> str:
    return f"org:{org_id}:{zone}"


def order_payload(order: Order) -> dict:
    return {
        "id": order.id,
        "iiko_order_id": order.iiko_order_id,
        "org_id": order.org_id,
        "number": order.number,
        "status": order.status,
        "customer": order.customer_name,
        "phone": order.phone,
        "address": order.address,
        "items": [
            {
                "id": i.id, "product_id": i.product_id, "name": i.name,
                "amount": i.amount, "station": i.station,
                "is_checked": i.is_checked,
                "accepted": i.station_accepted_at is not None,
                "ready": i.station_ready_at is not None,
            }
            for i in order.items
        ],
    }


async def broadcast_order(order: Order) -> None:
    """Рассылает заказ в комнаты всех зон его филиала, в которых есть позиции."""
    payload = order_payload(order)
    zones_present = {i.station for i in order.items} or {"other"}
    for zone in zones_present:
        await sio.emit("order:update", payload, room=room(order.org_id, zone))
    # сборщик видит весь заказ
    await sio.emit("order:update", payload, room=room(order.org_id, "assembler"))


@sio.event
async def join(sid, data):
    """Клиент просит вступить в комнату своего филиала+зоны. data={org_id, zone}."""
    org_id, zone = data.get("org_id"), data.get("zone")
    if not org_id or zone not in (*ZONES, "assembler"):
        return {"ok": False, "error": "bad room"}
    await sio.enter_room(sid, room(org_id, zone))
    return {"ok": True}
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `cd backend && pytest tests/test_realtime.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/realtime.py backend/tests/test_realtime.py
git commit -m "feat: socket.io server, rooms, order broadcast"
```

---

## Task 7: Вебхук-эндпоинт + ручной триггер, сборка ASGI

**Files:**
- Create: `backend/app/api.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/test_webhook.py`

- [ ] **Step 1: Написать падающий тест `backend/tests/test_webhook.py`**

```python
import respx
from httpx import Response, ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy import select, func
from app.db import Base
import app.models  # noqa
from app.models import Order
from app.config import settings


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    from app import api
    from app.db import get_session

    async def override_session():
        async with maker() as s:
            yield s

    api.app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=api.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, maker
    api.app.dependency_overrides.clear()
    await engine.dispose()


@respx.mock
async def test_webhook_rejects_bad_secret(client):
    c, _ = client
    settings.webhook_secret = "s3cret"
    r = await c.post("/webhooks/iiko/cloud?key=wrong", json=[])
    assert r.status_code == 403


@respx.mock
async def test_webhook_ingests_delivery_event(client):
    c, maker = client
    settings.webhook_secret = "s3cret"
    settings.cloud_api_login = "key"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    respx.post("https://api-ru.iiko.services/api/1/deliveries/by_id").mock(
        return_value=Response(200, json={"orders": [{
            "id": "o1",
            "order": {"number": "101", "customer": {"name": "Иван"},
                      "items": [{"type": "Product", "amount": 1,
                                 "product": {"id": "p1", "name": "Ролл"}}]},
        }]}))

    event = [{
        "eventType": "DeliveryOrderUpdate",
        "organizationId": "org-1",
        "eventInfo": {"id": "o1"},
    }]
    r = await c.post("/webhooks/iiko/cloud?key=s3cret", json=event)
    assert r.status_code == 200

    async with maker() as s:
        n = (await s.execute(select(func.count()).select_from(Order))).scalar_one()
        assert n == 1
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd backend && pytest tests/test_webhook.py -v`
Expected: FAIL (нет `app.api`).

- [ ] **Step 3: Создать `backend/app/api.py`**

```python
from __future__ import annotations
from fastapi import FastAPI, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_session
from app import iiko_cloud, ingest, realtime

app = FastAPI(title="Saya KDS backend")


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/webhooks/iiko/cloud")
async def iiko_webhook(events: list[dict], key: str = Query(default=""),
                       session: AsyncSession = Depends(get_session)):
    if settings.webhook_secret and key != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad key")

    handled = 0
    for event in events or []:
        if event.get("eventType") != "DeliveryOrderUpdate":
            continue
        org_id = event.get("organizationId")
        order_id = (event.get("eventInfo") or {}).get("id")
        if not org_id or not order_id:
            continue
        wrapper = await iiko_cloud.fetch_delivery_by_id(org_id, order_id)
        if not wrapper:
            continue
        normalized = iiko_cloud.normalize_full_order(wrapper, org_id)
        order = await ingest.ingest_order(session, normalized)
        await realtime.broadcast_order(order)
        handled += 1
    return {"ok": True, "handled": handled}


@app.post("/debug/pull")
async def debug_pull(org_id: str, order_id: str,
                     session: AsyncSession = Depends(get_session)):
    """Локальная отладка без ожидания вебхука: дёрнуть by_id и заингестить."""
    wrapper = await iiko_cloud.fetch_delivery_by_id(org_id, order_id)
    if not wrapper:
        raise HTTPException(status_code=404, detail="order not found")
    normalized = iiko_cloud.normalize_full_order(wrapper, org_id)
    order = await ingest.ingest_order(session, normalized)
    await realtime.broadcast_order(order)
    return realtime.order_payload(order)
```

- [ ] **Step 4: Создать `backend/app/main.py`** (ASGI: FastAPI + Socket.IO)

```python
from __future__ import annotations
import socketio
from app.api import app as fastapi_app
from app.realtime import sio
from app.db import engine, Base
import app.models  # noqa: F401


@fastapi_app.on_event("startup")
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# Socket.IO оборачивает FastAPI: один ASGI-апп на uvicorn.
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)
```

- [ ] **Step 5: Запустить тест — должен пройти**

Run: `cd backend && pytest tests/test_webhook.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Проверить, что сервис стартует**

Run: `cd backend && uvicorn app.main:app --port 8000 &` затем `sleep 2 && curl -s localhost:8000/health && kill %1`
Expected: `{"ok":true}`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api.py backend/app/main.py backend/tests/test_webhook.py
git commit -m "feat: iiko webhook endpoint + debug pull + ASGI app"
```

---

## Task 8: RMS-клиент (сотрудники) + логин сотрудника

**Files:**
- Create: `backend/app/rms.py`
- Modify: `backend/app/api.py` (добавить `/auth/login`)
- Test: `backend/tests/test_rms.py`

- [ ] **Step 1: Написать падающий тест `backend/tests/test_rms.py`**

```python
import hashlib
import respx
from httpx import Response
from app import rms
from app.config import settings

EMPLOYEES_XML = """<?xml version="1.0"?><employees>
<employee><id>e1</id><name>Повар Алия</name><code>1234</code></employee>
</employees>"""


@respx.mock
async def test_rms_auth_uses_sha1_and_logs_out():
    settings.rms_base = "https://rms.test/resto"
    settings.rms_login = "nignarok"
    settings.rms_password = "pw"
    sha1 = hashlib.sha1(b"pw").hexdigest()
    auth = respx.get(f"https://rms.test/resto/api/auth?login=nignarok&pass={sha1}").mock(
        return_value=Response(200, text="TOKEN-1"))
    logout = respx.get("https://rms.test/resto/api/logout?key=TOKEN-1").mock(
        return_value=Response(200, text="ok"))

    async with rms.session() as token:
        assert token == "TOKEN-1"
    assert auth.called and logout.called  # слот лицензии освобождён


@respx.mock
async def test_fetch_employees_parses_xml():
    settings.rms_base = "https://rms.test/resto"
    settings.rms_login = "nignarok"
    settings.rms_password = "pw"
    sha1 = hashlib.sha1(b"pw").hexdigest()
    respx.get(f"https://rms.test/resto/api/auth?login=nignarok&pass={sha1}").mock(
        return_value=Response(200, text="TOKEN-1"))
    respx.get("https://rms.test/resto/api/logout?key=TOKEN-1").mock(
        return_value=Response(200, text="ok"))
    respx.get("https://rms.test/resto/api/employees?key=TOKEN-1").mock(
        return_value=Response(200, text=EMPLOYEES_XML))

    emps = await rms.fetch_employees()
    assert emps == [{"id": "e1", "name": "Повар Алия", "code": "1234"}]
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd backend && pytest tests/test_rms.py -v`
Expected: FAIL (`rms` не существует).

- [ ] **Step 3: Создать `backend/app/rms.py`**

```python
from __future__ import annotations
import hashlib
from contextlib import asynccontextmanager
import xml.etree.ElementTree as ET
import httpx
from app.config import settings


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


@asynccontextmanager
async def session():
    """Логинится в RMS и ГАРАНТИРОВАННО делает logout (освобождает слот лицензии)."""
    base = settings.rms_base.rstrip("/")
    async with httpx.AsyncClient(timeout=60, verify=True) as client:
        r = await client.get(f"{base}/api/auth",
                             params={"login": settings.rms_login,
                                     "pass": _sha1(settings.rms_password)})
        r.raise_for_status()
        token = r.text.strip()
        try:
            yield token
        finally:
            await client.get(f"{base}/api/logout", params={"key": token})


async def fetch_employees() -> list[dict]:
    base = settings.rms_base.rstrip("/")
    async with session() as token:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{base}/api/employees", params={"key": token})
            r.raise_for_status()
            xml = r.text
    root = ET.fromstring(xml)
    out: list[dict] = []
    for emp in root.iter("employee"):
        out.append({
            "id": (emp.findtext("id") or "").strip(),
            "name": (emp.findtext("name") or "").strip(),
            "code": (emp.findtext("code") or "").strip(),
        })
    return out
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `cd backend && pytest tests/test_rms.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Добавить `/auth/login` в `backend/app/api.py`** (PIN сотрудника → JWT с org/role)

Добавить в конец `backend/app/api.py`:

```python
from datetime import datetime, timedelta
import jwt  # PyJWT
from pydantic import BaseModel


class LoginIn(BaseModel):
    org_id: str
    role: str
    pin: str


@app.post("/auth/login")
async def login(body: LoginIn):
    # MVP: PIN сверяем со списком сотрудников RMS по полю code.
    from app import rms
    employees = await rms.fetch_employees()
    match = next((e for e in employees if e["code"] and e["code"] == body.pin), None)
    if not match:
        raise HTTPException(status_code=401, detail="bad pin")
    if body.role not in ("rolls", "pizza", "hot", "assembler", "courier"):
        raise HTTPException(status_code=400, detail="bad role")
    token = jwt.encode(
        {"org_id": body.org_id, "role": body.role,
         "emp_id": match["id"], "name": match["name"],
         "exp": datetime.utcnow() + timedelta(hours=12)},
        settings.jwt_secret, algorithm="HS256")
    return {"token": token, "name": match["name"], "role": body.role, "org_id": body.org_id}
```

- [ ] **Step 6: Написать тест логина в `backend/tests/test_rms.py`** (дописать в конец файла)

```python
import respx as _respx
from httpx import Response as _Response, ASGITransport as _AT, AsyncClient as _AC


@_respx.mock
async def test_login_issues_jwt_for_valid_pin():
    settings.rms_base = "https://rms.test/resto"
    settings.rms_login = "nignarok"; settings.rms_password = "pw"
    settings.jwt_secret = "secret"
    sha1 = hashlib.sha1(b"pw").hexdigest()
    _respx.get(f"https://rms.test/resto/api/auth?login=nignarok&pass={sha1}").mock(
        return_value=_Response(200, text="TOKEN-1"))
    _respx.get("https://rms.test/resto/api/logout?key=TOKEN-1").mock(
        return_value=_Response(200, text="ok"))
    _respx.get("https://rms.test/resto/api/employees?key=TOKEN-1").mock(
        return_value=_Response(200, text=EMPLOYEES_XML))

    from app.api import app
    async with _AC(transport=_AT(app=app), base_url="http://test") as c:
        r = await c.post("/auth/login", json={"org_id": "org-1", "role": "rolls", "pin": "1234"})
    assert r.status_code == 200
    assert r.json()["name"] == "Повар Алия"
```

- [ ] **Step 7: Запустить тесты — должны пройти**

Run: `cd backend && pytest tests/test_rms.py -v`
Expected: PASS (3 passed).

- [ ] **Step 8: Commit**

```bash
git add backend/app/rms.py backend/app/api.py backend/tests/test_rms.py
git commit -m "feat: RMS employees client + PIN login issuing JWT"
```

---

## Task 9: Действия зон (Принял/Готово/галочка) + вещание

**Files:**
- Modify: `backend/app/api.py` (эндпоинты действий)
- Test: `backend/tests/test_actions.py`

- [ ] **Step 1: Написать падающий тест `backend/tests/test_actions.py`**

```python
import respx
from httpx import ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy import select
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
        order = Order(iiko_order_id="o1", org_id="org-1", number="101")
        order.items = [OrderItem(product_id="p1", name="Ролл", station="rolls")]
        s.add(order); await s.commit()

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


@respx.mock
async def test_accept_then_ready_sets_timestamps(client):
    c, maker = client
    async with maker() as s:
        item_id = (await s.execute(select(OrderItem.id))).scalar_one()

    r1 = await c.post(f"/items/{item_id}/accept", json={"by": "Алия"})
    assert r1.status_code == 200
    r2 = await c.post(f"/items/{item_id}/ready", json={"by": "Алия"})
    assert r2.status_code == 200

    async with maker() as s:
        it = (await s.execute(select(OrderItem).where(OrderItem.id == item_id))).scalar_one()
        assert it.station_accepted_at is not None
        assert it.station_ready_at is not None
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd backend && pytest tests/test_actions.py -v`
Expected: FAIL (нет эндпоинтов действий).

- [ ] **Step 3: Добавить эндпоинты действий в конец `backend/app/api.py`**

```python
from datetime import datetime as _dt
from sqlalchemy import select as _select
from app.models import Order as _Order, OrderItem as _OrderItem


class ActionIn(BaseModel):
    by: str = ""


async def _load_order(session: AsyncSession, order_id: int) -> _Order:
    order = (await session.execute(_select(_Order).where(_Order.id == order_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return order


async def _item_and_broadcast(session: AsyncSession, item_id: int) -> dict:
    item = (await session.execute(
        _select(_OrderItem).where(_OrderItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    await session.commit()
    order = await _load_order(session, item.order_id)
    await realtime.broadcast_order(order)
    return realtime.order_payload(order)


@app.post("/items/{item_id}/accept")
async def item_accept(item_id: int, body: ActionIn,
                      session: AsyncSession = Depends(get_session)):
    item = (await session.execute(
        _select(_OrderItem).where(_OrderItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    item.station_accepted_at = _dt.utcnow()
    item.checked_by = body.by or item.checked_by
    return await _item_and_broadcast(session, item_id)


@app.post("/items/{item_id}/ready")
async def item_ready(item_id: int, body: ActionIn,
                     session: AsyncSession = Depends(get_session)):
    item = (await session.execute(
        _select(_OrderItem).where(_OrderItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    item.station_ready_at = _dt.utcnow()
    return await _item_and_broadcast(session, item_id)


@app.post("/items/{item_id}/check")
async def item_check(item_id: int, body: ActionIn,
                     session: AsyncSession = Depends(get_session)):
    item = (await session.execute(
        _select(_OrderItem).where(_OrderItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    item.is_checked = True
    item.checked_at = _dt.utcnow()
    item.checked_by = body.by or item.checked_by
    return await _item_and_broadcast(session, item_id)
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `cd backend && pytest tests/test_actions.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Прогнать весь набор тестов**

Run: `cd backend && pytest -v`
Expected: все тесты PASS (зелёный прогон).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api.py backend/tests/test_actions.py
git commit -m "feat: zone item actions (accept/ready/check) with broadcast"
```

---

## Self-Review (выполнено при написании плана)

- **Покрытие спеки (§ из спецификации):** §1 механика заказов → Task 3,7; §2 RMS → Task 8;
  §3 стек → Task 1; §6 зоны → Task 4,5; §7 изоляция (комнаты/секрет) → Task 6,7;
  §8 модель данных → Task 2; §9 поток данных → Task 5,6,7. Frontend (§5) и поллер-страховка
  (§4) и аналитика (§12.4) — намеренно вынесены в следующие планы (этот план = «движок»).
- **Плейсхолдеры:** не найдено — в каждом шаге реальный код и точные команды.
- **Согласованность типов:** `NormalizedOrder`/`WebhookItem` (schemas) едины в Task 3/4/5;
  `build_resolver`/`resolve` совпадают в Task 4 и Task 5; `room()`/`order_payload()`/
  `broadcast_order()` из Task 6 используются в Task 7/9 без расхождений; `rms.session()`/
  `fetch_employees()` из Task 8 совпадают с тестами.

## Что НЕ входит в этот план (следующие планы)
- Frontend (React+Vite, 4 экрана) — отдельный план.
- Поллер `by_revision` (страховка) — отдельная задача Phase 2.
- Таймеры/просрочка/история/аналитика, регистрация вебхука на проде через туннель — Phase 2–4.
- Авто-наполнение `product_station_map` из номенклатуры + админка — Phase 2.
