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


def test_normalize_extracts_payment_type():
    wrapper = {"id": "o1", "order": {"number": "5", "sum": 6990, "payments": [
        {"paymentType": {"id": "pt-1", "name": "Терминал Kaspi", "kind": "Card"}, "sum": 6990}]}}
    n = c.normalize_full_order(wrapper, "org-1")
    assert n.payment_type_name == "Терминал Kaspi"
    assert n.payment_type_id == "pt-1" and n.payment_type_kind == "Card"


def test_normalize_payment_type_missing():
    """Если payments нет или пустой — поля None, не падает."""
    wrapper = {"id": "o2", "order": {"number": "6", "sum": 1000}}
    n = c.normalize_full_order(wrapper, "org-1")
    assert n.payment_type_id is None
    assert n.payment_type_kind is None
    assert n.payment_type_name is None


from datetime import datetime


def test_parse_iiko_local_to_utc():
    from app.iiko_cloud import _parse_iiko_local_to_utc
    assert _parse_iiko_local_to_utc("2026-06-25 18:01:04.638", 5) == datetime(2026, 6, 25, 13, 1, 4, 638000)
    assert _parse_iiko_local_to_utc("2026-06-25 18:01:04", 5) == datetime(2026, 6, 25, 13, 1, 4)
    assert _parse_iiko_local_to_utc("", 5) is None
    assert _parse_iiko_local_to_utc("garbage", 5) is None


def test_normalize_extracts_complete_before_as_utc():
    from app.iiko_cloud import normalize_full_order
    wrapper = {"id": "o1", "order": {"number": "5", "sum": 100,
               "completeBefore": "2026-06-25 18:00:00"}}
    n = normalize_full_order(wrapper, "org-1")
    assert n.complete_before == datetime(2026, 6, 25, 13, 0, 0)  # -5h


def test_normalize_reads_lifecycle_timestamps():
    """whenSended/whenDelivered/whenCooking из iiko парсятся (iiko-local -> UTC)."""
    wrapper = {
        "id": "o9",
        "order": {
            "number": "909", "status": "Delivered",
            "whenCreated": "2026-06-27 08:00:00.000",
            "whenCookingCompleted": "2026-06-27 08:20:00.000",
            "whenSended": "2026-06-27 08:25:00.000",
            "whenDelivered": "2026-06-27 08:50:00.000",
            "deliveryPoint": {"coordinates": {"latitude": 51.1, "longitude": 71.4}},
            "items": [{"type": "Product", "amount": 1,
                       "product": {"id": "p1", "name": "X", "category": {"id": "c1"}}}],
        },
    }
    n = c.normalize_full_order(wrapper, "org-1")
    # поля непустые и упорядочены по времени (created < cooking < sended < delivered)
    assert n.when_created is not None
    assert n.when_cooking_completed is not None
    assert n.when_sended is not None
    assert n.when_delivered is not None
    assert n.when_created < n.when_cooking_completed < n.when_sended < n.when_delivered


@respx.mock
async def test_by_revision_sends_organization_ids_array():
    """iiko принимает только organizationIds (массив). С одиночным organizationId
    endpoint отвечает 400 INVALID_BODY_JSON_FORMAT — и фоллбэк-поллер молча не работал."""
    import json as _json
    settings.cloud_api_login = "key"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    route = respx.post("https://api-ru.iiko.services/api/1/deliveries/by_revision").mock(
        return_value=Response(200, json={
            "maxRevision": 42,
            "ordersByOrganizations": [
                {"organizationId": "org-1",
                 "orders": [{"id": "o1", "order": {"number": "5"}}]},
            ],
        }))
    data = await c.fetch_deliveries_by_revision("org-1", 10)
    body = _json.loads(route.calls.last.request.read())
    assert body["organizationIds"] == ["org-1"]
    assert body["startRevision"] == 10
    # Ответ приходит вложенным по организациям — поллеру отдаём плоский список.
    assert [w["id"] for w in data["orders"]] == ["o1"]
    assert data["maxRevision"] == 42


@respx.mock
async def test_by_date_returns_orders_and_revision():
    """Догон по дате: нужен и список заказов, и стартовый курсор ревизии."""
    import json as _json
    settings.cloud_api_login = "key"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    route = respx.post(
        "https://api-ru.iiko.services/api/1/deliveries/by_delivery_date_and_status").mock(
        return_value=Response(200, json={
            "maxRevision": 99,
            "ordersByOrganizations": [
                {"organizationId": "org-1",
                 "orders": [{"id": "o9", "order": {"number": "9"}}]},
            ],
        }))
    data = await c.fetch_deliveries_by_date("org-1", days=2)
    body = _json.loads(route.calls.last.request.read())
    assert body["organizationIds"] == ["org-1"]
    assert "deliveryDateFrom" in body
    assert [w["id"] for w in data["orders"]] == ["o9"]
    assert data["maxRevision"] == 99
