from app import ingest, iiko_cloud as c
from app.realtime import order_payload
from app.schemas import NormalizedOrder, WebhookItem


def test_normalize_full_order_extracts_service_type():
    wrapper = {
        "id": "o1",
        "order": {
            "number": "101",
            "orderType": {
                "id": "type-1",
                "name": "Доставка курьером",
                "orderServiceType": "DeliveryByCourier",
            },
            "items": [
                {"type": "Product", "amount": 1,
                 "product": {"id": "p-roll", "name": "Филадельфия",
                             "category": {"id": "cat-rolls"}}},
            ],
        },
    }
    n = c.normalize_full_order(wrapper, "org-1")
    assert n.service_type == "DeliveryByCourier"


def test_normalize_full_order_service_type_missing_is_none():
    wrapper = {"id": "o2", "order": {"number": "102", "items": []}}
    n = c.normalize_full_order(wrapper, "org-1")
    assert n.service_type is None


async def test_ingest_persists_service_type_and_appears_in_payload(session):
    data = NormalizedOrder(
        iiko_order_id="o1", org_id="org-1", number="101",
        service_type="DeliveryByPickUp",
        items=[WebhookItem(product_id="p-roll", name="Ролл", amount=1)],
    )
    order = await ingest.ingest_order(session, data)
    assert order.service_type == "DeliveryByPickUp"

    payload = order_payload(order)
    assert payload["service_type"] == "DeliveryByPickUp"
