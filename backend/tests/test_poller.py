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
            "ordersByOrganizations": [{"organizationId": "org-1", "orders": [{
                "id": "o1",
                "order": {"number": "5",
                          "items": [{"product": {"id": "p1", "name": "Ролл"},
                                     "amount": 1}]},
            }]}],
            "maxRevision": 7,
        }))

    org = "org-1"
    poller._revisions[org] = 3       # курсор уже есть -> идём инкрементально
    await poller.poll_once(org)

    # Заказ заингещён.
    async with memory_maker() as s:
        order = (await s.execute(
            select(Order).where(Order.iiko_order_id == "o1"))).scalar_one()
        assert order.number == "5"

    # Курсор ревизии продвинулся до maxRevision.
    assert poller._revisions[org] == 7
    # Запрос ушёл с текущей ревизии, а не с нуля.
    assert b'"startRevision":3' in route.calls.last.request.read().replace(b" ", b"")


@respx.mock
async def test_first_pass_catches_up_by_date_and_seeds_cursor(memory_maker):
    """Первый проход (курсора нет) догоняет по дате: ревизию 0 iiko отклоняет
    (TOO_OLD_REVISION), поэтому by_revision на первом проходе не дёргаем вовсе.
    Именно этот проход подбирает заказы, закрытые в iiko, пока вебхуки терялись."""
    settings.cloud_api_login = "key"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    rev = respx.post("https://api-ru.iiko.services/api/1/deliveries/by_revision").mock(
        return_value=Response(400, json={"error": "TOO_OLD_REVISION"}))
    respx.post(
        "https://api-ru.iiko.services/api/1/deliveries/by_delivery_date_and_status").mock(
        return_value=Response(200, json={
            "ordersByOrganizations": [{"organizationId": "org-1", "orders": [{
                "id": "old-1",
                "order": {"number": "77", "status": "Closed",
                          "items": [{"product": {"id": "p1", "name": "Ролл"},
                                     "amount": 1}]},
            }]}],
            "maxRevision": 99,
        }))

    await poller.poll_once("org-1")

    assert not rev.called
    async with memory_maker() as s:
        order = (await s.execute(
            select(Order).where(Order.iiko_order_id == "old-1"))).scalar_one()
        assert order.status == "Closed"
    assert poller._revisions["org-1"] == 99


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


async def test_hall_closes_orders_opened_before_restart(memory_maker, monkeypatch):
    """Заказ зала, открытый ДО перезапуска процесса, должен закрыться.

    _hall_active живёт в памяти и обнуляется рестартом: без досева из БД такие
    заказы никогда не попадают в «пропавшие» и висят на планшете вечно.
    """
    from app import iiko_cloud

    poller._hall_active.clear()
    poller._hall_cache.clear()

    async with memory_maker() as s:
        s.add(Order(iiko_order_id="h1", org_id="org-1", number="12",
                    status="Bill", service_type="Common"))
        await s.commit()

    monkeypatch.setattr(iiko_cloud, "fetch_terminal_groups",
                        lambda org: _async_value(["tg1"]))
    monkeypatch.setattr(iiko_cloud, "fetch_sections",
                        lambda tgs: _async_value(["t1"]))
    monkeypatch.setattr(iiko_cloud, "order_init_by_table",
                        lambda *a, **k: _async_value(None))
    monkeypatch.setattr(iiko_cloud, "order_by_table",
                        lambda *a, **k: _async_value([]))  # в зале пусто

    await poller.hall_poll_once("org-1")

    async with memory_maker() as s:
        order = (await s.execute(
            select(Order).where(Order.iiko_order_id == "h1"))).scalar_one()
        assert order.status == "Closed"


async def test_hall_order_returned_empty_is_not_closed(memory_maker, monkeypatch):
    """Заказ, который iiko вернул БЕЗ позиций, не закрыт — он ещё на столе.

    iiko отдаёт только что открытый стол с items: [] и наполняет его позже.
    Такой заказ мы пропускали мимо seen, и следующий проход считал его
    «пропавшим» — заказ снимался с планшета, хотя касса его не закрывала.
    """
    from app import iiko_cloud

    poller._hall_active.clear()
    poller._hall_cache.clear()

    async with memory_maker() as s:
        s.add(Order(iiko_order_id="h2", org_id="org-1", number="36",
                    status="New", service_type="Common"))
        await s.commit()

    empty_from_iiko = [{"id": "h2", "order": {
        "number": 36, "status": "New", "items": [],
        "orderType": {"name": "Обычный заказ", "orderServiceType": "Common"}}}]

    monkeypatch.setattr(iiko_cloud, "fetch_terminal_groups",
                        lambda org: _async_value(["tg1"]))
    monkeypatch.setattr(iiko_cloud, "fetch_sections",
                        lambda tgs: _async_value(["t1"]))
    monkeypatch.setattr(iiko_cloud, "order_init_by_table",
                        lambda *a, **k: _async_value(None))
    monkeypatch.setattr(iiko_cloud, "order_by_table",
                        lambda *a, **k: _async_value(empty_from_iiko))

    await poller.hall_poll_once("org-1")

    async with memory_maker() as s:
        order = (await s.execute(
            select(Order).where(Order.iiko_order_id == "h2"))).scalar_one()
        assert order.status == "New", "пустой ответ iiko — не повод закрывать заказ"


async def _async_value(value):
    return value


async def test_stale_revision_falls_back_to_date_catchup(memory_maker, monkeypatch):
    """Протухший курсор чинится сам, а не вешает приём заказов навсегда.

    iiko отбрасывает ревизию старше суток: «Passed revision is too old». Курсор
    при ошибке не двигался, поэтому один упавший опрос означал, что и все
    следующие упадут — 23.08.2026 заказы не приходили 20 часов.
    """
    import httpx
    from app import iiko_cloud

    poller._revisions["org-1"] = 1
    calls = []

    async def stale(org_id, start_revision):
        calls.append("by_revision")
        request = httpx.Request("POST", "https://api-ru.iiko.services/api/1/deliveries/by_revision")
        response = httpx.Response(400, json={"errorDescription": "Passed revision is too old."},
                                  request=request)
        raise httpx.HTTPStatusError("400", request=request, response=response)

    async def catchup(org_id, days=2):
        calls.append("by_date")
        return {"orders": [], "maxRevision": 777}

    monkeypatch.setattr(iiko_cloud, "fetch_deliveries_by_revision", stale)
    monkeypatch.setattr(iiko_cloud, "fetch_deliveries_by_date", catchup)

    await poller.poll_once("org-1")

    assert calls == ["by_revision", "by_date"], "после «too old» догоняем по дате"
    assert poller._revisions["org-1"] == 777, "курсор должен обновиться"


async def test_other_errors_do_not_trigger_catchup(memory_maker, monkeypatch):
    """Сеть моргнула — ждём следующего прохода, а не тянем два дня заказов."""
    import httpx
    from app import iiko_cloud

    poller._revisions["org-1"] = 500
    calls = []

    async def boom(org_id, start_revision):
        calls.append("by_revision")
        raise httpx.ConnectError("сеть")

    async def catchup(org_id, days=2):
        calls.append("by_date")
        return {"orders": [], "maxRevision": 999}

    monkeypatch.setattr(iiko_cloud, "fetch_deliveries_by_revision", boom)
    monkeypatch.setattr(iiko_cloud, "fetch_deliveries_by_date", catchup)

    await poller.poll_once("org-1")

    assert calls == ["by_revision"]
    assert poller._revisions["org-1"] == 500
