import respx
from httpx import Response
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app import poller
from app.db import Base
import app.models  # noqa: F401  (регистрирует таблицы)
from app.models import Order
from app.config import settings


@pytest_asyncio.fixture
async def memory_maker(monkeypatch):
    """In-memory SQLite, подменяющий poller.SessionLocal, чтобы poll_once
    ингестил в реальную (но эфемерную) БД без сети."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(poller, "SessionLocal", maker)
    # Чистый курсор ревизий на каждый тест.
    poller._revisions.clear()
    yield maker
    await engine.dispose()


async def test_run_poller_disabled_by_default(monkeypatch):
    """Без POLL_INTERVAL_SECONDS (или =0) поллер сразу возвращается без сети."""
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("POLL_ORG_IDS", raising=False)
    # Возврат должен произойти немедленно (никаких бесконечных циклов/сети).
    await poller.run_poller()


async def test_run_poller_disabled_when_no_orgs(monkeypatch):
    """Интервал задан, но нет org -> поллер всё равно отключён."""
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "5")
    monkeypatch.delenv("POLL_ORG_IDS", raising=False)
    await poller.run_poller()


@respx.mock
async def test_poll_once_ingests_and_advances_revision(memory_maker):
    settings.cloud_api_login = "key"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    route = respx.post("https://api-ru.iiko.services/api/1/deliveries/by_revision").mock(
        return_value=Response(200, json={
            "orders": [{
                "id": "o1",
                "order": {"number": "5",
                          "items": [{"product": {"id": "p1", "name": "Ролл"},
                                     "amount": 1}]},
            }],
            "maxRevision": 7,
        }))

    org = "org-1"
    await poller.poll_once(org)

    # Заказ заингещён.
    async with memory_maker() as s:
        order = (await s.execute(
            select(Order).where(Order.iiko_order_id == "o1"))).scalar_one()
        assert order.number == "5"

    # Курсор ревизии продвинулся до maxRevision.
    assert poller._revisions[org] == 7
    # Запрос ушёл со стартовой ревизией 0.
    assert b'"startRevision":0' in route.calls.last.request.read().replace(b" ", b"")


@respx.mock
async def test_poll_once_swallows_errors(memory_maker):
    """Сбой по филиалу не пробрасывается наружу (логируется и проглатывается)."""
    settings.cloud_api_login = "key"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(500, json={"error": "boom"}))
    # Не должно бросить исключение.
    await poller.poll_once("org-err")
    # Курсор не двинулся.
    assert poller._revisions.get("org-err", 0) == 0
