import respx
from httpx import Response, ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy import select
from app.db import Base
import app.models  # noqa: F401
from app.models import ProductStationMap, Order, OrderItem
from app.stations import (classify_group, classify_name, classify_by_group,
                           classify_product, build_resolver)
from app.schemas import WebhookItem


# ── Юнит-тесты классификатора групп меню ──────────────────────────────────

def test_classify_rolls():
    assert classify_group("Роллы") == "rolls"


def test_classify_hot_rolls_go_to_rolls():
    # «Горячие роллы» содержит и «ролл», и «горяч» — побеждает «ролл».
    assert classify_group("Горячие роллы") == "rolls"


def test_classify_pizza():
    assert classify_group("Пицца") == "pizza"


def test_classify_soups_are_hot():
    assert classify_group("Супы") == "hot"


def test_classify_wok_is_hot():
    assert classify_group("Вок") == "hot"


def test_classify_snacks_are_other():
    assert classify_group("Закуски") == "other"


def test_classify_empty_is_other():
    assert classify_group("") == "other"
    assert classify_group(None) == "other"


def test_classify_sets_and_sushi_are_rolls():
    assert classify_group("Сеты") == "rolls"
    assert classify_group("Суши") == "rolls"


# ── Юнит-тесты классификатора по НАЗВАНИЮ блюда ───────────────────────────

def test_classify_name_tempura_roll():
    assert classify_name("Сяке темпура ролл") == "rolls"


def test_classify_name_set():
    assert classify_name("*Нами сет") == "rolls"


def test_classify_name_philadelphia():
    assert classify_name("Филадельфия") == "rolls"


def test_classify_name_pizza():
    assert classify_name("Пицца С колбасками") == "pizza"


def test_classify_name_tom_yam():
    assert classify_name("Том Ям") == "hot"


def test_classify_name_wok():
    assert classify_name("Вок с курицей") == "hot"


def test_classify_name_other():
    assert classify_name("Кока-кола") == "other"


def test_classify_name_pizza_before_rolls():
    # «пицц» проверяется раньше ролл-ключей: пицца не должна попасть в rolls.
    assert classify_name("Пицца") == "pizza"


def test_classify_name_empty_is_other():
    assert classify_name("") == "other"
    assert classify_name(None) == "other"


# ── Юнит-тесты классификатора по ГРУППЕ меню ──────────────────────────────

def test_group_hot_dish_without_keyword():
    # «Плов» не содержит ключевых слов, но группа «Горячие блюда» → hot.
    assert classify_product("Плов", "Кухня / Горячие блюда", "Кухня") == "hot"


def test_group_pizza_path():
    assert classify_by_group("Кухня / Пицца / Пицца 25", "Кухня") == "pizza"


def test_group_rolls_maki():
    assert classify_by_group("Кухня / Маки 4 шт", "Кухня") == "rolls"


def test_group_sets_are_rolls():
    assert classify_by_group("Кухня / Сеты-Комбо", "Кухня") == "rolls"


def test_group_nonfood_root_is_other():
    # Хозтовары/бар/инвентарь → прочее, даже если в имени есть «пицца».
    assert classify_by_group("КУХНЯ ТОВАРЫ / Упаковка", "КУХНЯ ТОВАРЫ") == "other"
    assert classify_by_group("БАР / Коктейли", "БАР") == "other"


def test_group_ambiguous_returns_none():
    # «Подарок» неоднозначна — зону решает имя.
    assert classify_by_group("Кухня / Подарок", "Кухня") is None


def test_product_gift_pizza_by_name():
    # Подарочная пицца: группа неоднозначна, но имя → pizza.
    assert classify_product("Пицца 4 сезона", "Кухня / Подарок", "Кухня") == "pizza"


def test_product_bar_drink_is_other():
    assert classify_product("Кола 0.5", "БАР / Напитки", "БАР") == "other"


def test_combo_visible_to_kitchen_not_other():
    # Комбо без ключевых слов не должно прятаться в «прочее» — отдаём на роллы.
    assert classify_product("Комбо Универсал стандарт",
                            "Меню для Агрегаторов", "Меню для Агрегаторов") == "rolls"
    assert classify_product("Сытный микс",
                            "Кухня / Спецпредложение для зала", "Кухня") == "rolls"


def test_aggregator_drinks_stay_other():
    # Напитки внутри меню агрегаторов — всё равно «прочее», не на кухню.
    assert classify_product("Кола 1л", "Меню для Агрегаторов / Напитки для Агрегаторов",
                            "Меню для Агрегаторов") == "other"


def test_nuggets_and_chicken_are_hot():
    assert classify_product("*Куриные наггетсы 9 шт",
                            "Меню для Агрегаторов / Блюда агрегаторов",
                            "Меню для Агрегаторов") == "hot"
    assert classify_name("Боул с курицей") == "hot"


# ── Юнит-тест: resolver откатывается на классификацию по имени ─────────────

async def test_resolver_falls_back_to_name():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        # Пустой маппинг: нет ни by_product, ни by_category.
        async with maker() as s:
            resolve = await build_resolver(s)
            item = WebhookItem(product_id="x", category_id=None,
                               name="Дракон ролл", amount=1)
            assert resolve(item) == "rolls"
    finally:
        await engine.dispose()


# ── Интеграционный тест эндпоинта авторазметки ────────────────────────────

NOMENCLATURE = {
    "groups": [
        {"id": "g-rolls", "name": "Роллы"},
        {"id": "g-pizza", "name": "Пицца"},
    ],
    "productCategories": [],
    "sizes": [],
    "products": [
        {"id": "p-fila", "name": "Филадельфия", "groupId": "g-rolls",
         "productCategoryId": "pc-1"},
        {"id": "p-marg", "name": "Маргарита", "groupId": "g-pizza",
         "productCategoryId": "pc-2"},
    ],
}


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    # Активный заказ с теми же продуктами, изначально в зоне other.
    async with maker() as s:
        order = Order(iiko_order_id="o1", org_id="org-1", number="101")
        order.items = [
            OrderItem(product_id="p-fila", name="Филадельфия", station="other"),
            OrderItem(product_id="p-marg", name="Маргарита", station="other"),
        ]
        s.add(order)
        await s.commit()

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


async def test_auto_endpoint_maps_products_and_reclassifies(client, monkeypatch):
    c, maker = client
    # Авторазметка теперь берёт меню из RMS — мокаем rms.fetch_products.
    import app.rms

    async def fake_products():
        return [
            {"id": "p-fila", "name": "Филадельфия ролл", "group_id": None},
            {"id": "p-marg", "name": "Пицца Маргарита", "group_id": None},
        ]

    monkeypatch.setattr(app.rms, "fetch_products", fake_products)

    r = await c.post("/admin/stations/auto?org_id=org-1")
    assert r.status_code == 200
    body = r.json()
    assert body["mapped_products"] == 2
    assert body["reclassified_items"] == 2

    # Маппинг сохранён: по строке на продукт с корректной зоной.
    async with maker() as s:
        rows = (await s.execute(select(ProductStationMap))).scalars().all()
        by_product = {row.product_id: row.station for row in rows}
        assert by_product == {"p-fila": "rolls", "p-marg": "pizza"}

        # Позиции активного заказа пересчитаны.
        items = (await s.execute(select(OrderItem))).scalars().all()
        stations = {it.product_id: it.station for it in items}
        assert stations == {"p-fila": "rolls", "p-marg": "pizza"}


@respx.mock
async def test_manual_override_post(client):
    c, maker = client
    r = await c.post("/admin/stations?org_id=org-1",
                     json={"items": [{"product_id": "p-fila", "station": "hot"}]})
    assert r.status_code == 200
    assert r.json()["updated"] == 1

    async with maker() as s:
        row = (await s.execute(
            select(ProductStationMap).where(ProductStationMap.product_id == "p-fila"))
        ).scalar_one()
        assert row.station == "hot"
        item = (await s.execute(
            select(OrderItem).where(OrderItem.product_id == "p-fila"))
        ).scalar_one()
        assert item.station == "hot"


@respx.mock
async def test_manual_override_rejects_bad_station(client):
    c, _ = client
    r = await c.post("/admin/stations?org_id=org-1",
                     json={"items": [{"product_id": "p-fila", "station": "grill"}]})
    assert r.status_code == 400
