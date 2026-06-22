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
    assert b'"apiLogin"' in route.calls.last.request.read()


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
    assert n.address == "Абая, 1"
    assert n.lat == 51.1 and n.lon == 71.4
    assert len(n.items) == 2
    assert n.items[0].product_id == "p-roll" and n.items[0].amount == 2
    assert n.items[0].category_id == "cat-rolls"
    assert n.items[1].combo_id == "combo-7"


def test_normalize_captures_comments():
    wrapper = {
        "id": "o3",
        "order": {
            "number": "103",
            "comment": "без помидор",
            "items": [
                {"type": "Product", "amount": 1, "comment": "без лука",
                 "product": {"id": "p1", "name": "Бургер"}},
            ],
        },
    }
    n = c.normalize_full_order(wrapper, "org-1")
    assert n.comment == "без помидор"
    assert n.items[0].comment == "без лука"


def test_webhooks_filter_includes_table_orders():
    # Без tableOrderFilter iiko не присылает зал/вынос/самовывоз -> их не видно.
    f = c.build_webhooks_filter()
    assert "deliveryOrderFilter" in f
    assert "tableOrderFilter" in f
    assert f["tableOrderFilter"]["orderStatuses"]  # непустой список статусов


def test_parse_terminal_group_ids():
    resp = {"terminalGroups": [
        {"organizationId": "org-1", "items": [{"id": "tg-1"}, {"id": "tg-2"}]},
        {"organizationId": "org-1", "items": [{"id": "tg-3"}]},
    ]}
    assert c._parse_terminal_group_ids(resp) == ["tg-1", "tg-2", "tg-3"]
    assert c._parse_terminal_group_ids({}) == []


def test_parse_table_ids():
    resp = {"restaurantSections": [
        {"id": "sec-1", "tables": [{"id": "t-1", "number": 1}, {"id": "t-2"}]},
        {"id": "sec-2", "tables": [{"id": "t-3"}]},
    ]}
    assert c._parse_table_ids(resp) == ["t-1", "t-2", "t-3"]
    assert c._parse_table_ids({"restaurantSections": []}) == []


@respx.mock
async def test_order_by_table_returns_orders():
    settings.cloud_api_login = "key123"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    route = respx.post("https://api-ru.iiko.services/api/1/order/by_table").mock(
        return_value=Response(200, json={"orders": [
            {"id": "ord-1", "order": {"number": "5", "status": "New"}}]}))
    orders = await c.order_by_table(["org-1"], ["t-1"], ["New", "Bill"])
    assert orders and orders[0]["id"] == "ord-1"
    body = route.calls.last.request.read()
    assert b'"organizationIds"' in body and b'"tableIds"' in body


@respx.mock
async def test_order_init_by_table_posts_terminal_and_tables():
    settings.cloud_api_login = "key123"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    route = respx.post("https://api-ru.iiko.services/api/1/order/init_by_table").mock(
        return_value=Response(200, json={"correlationId": "cid-1"}))
    res = await c.order_init_by_table("org-1", "tg-1", ["t-1", "t-2"])
    assert res["correlationId"] == "cid-1"
    body = route.calls.last.request.read()
    assert b'"terminalGroupId"' in body and b'"tableIds"' in body


def test_normalize_table_order_handles_by_table_wrapper():
    # by_table отдаёт обёртки {id, order:{...}} — та же форма, что и by_id.
    wrapper = {"id": "ord-9", "order": {
        "number": "9", "status": "Bill",
        "orderType": {"orderServiceType": "Common"},
        "items": [{"type": "Product", "amount": 1,
                   "product": {"id": "p1", "name": "Ролл"}}]}}
    n = c.normalize_table_order(wrapper, "org-1")
    assert n.iiko_order_id == "ord-9"
    assert n.status == "Bill"
    assert len(n.items) == 1 and n.items[0].product_id == "p1"


def test_normalize_full_order_builds_full_address():
    wrapper = {
        "id": "o2",
        "order": {
            "number": "102",
            "deliveryPoint": {
                "address": {
                    "street": {"name": "Кабанбай батыра"},
                    "house": "1",
                    "flat": "42",
                    "entrance": "3",
                    "floor": "5",
                },
                "comment": "код 1234",
            },
        },
    }
    n = c.normalize_full_order(wrapper, "org-1")
    assert n.address == "Кабанбай батыра, 1, кв. 42, подъезд 3, этаж 5 (код 1234)"
