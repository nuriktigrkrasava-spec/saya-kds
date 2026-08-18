"""Вынос с кассы отличается от зала позицией «Вынос» в составе.

В iiko оба типа приходят как Common/«Обычный заказ» — по типу заказа их не
различить. Кассир добавляет служебную позицию «Вынос»: на проде за четыре дня
она стоит в 35 заказах из 132.
"""
from app.ingest import is_takeaway
from app.schemas import WebhookItem


def _items(*names):
    return [WebhookItem(product_id=f"p{i}", name=n) for i, n in enumerate(names)]


def test_service_item_marks_takeaway():
    assert is_takeaway(_items("Филадельфия", "Вынос")) is True


def test_order_without_the_marker_is_hall():
    assert is_takeaway(_items("Филадельфия", "Кола")) is False


def test_marker_is_case_and_space_tolerant():
    assert is_takeaway(_items(" вынос ")) is True
    assert is_takeaway(_items("ВЫНОС")) is True


def test_dish_that_merely_contains_the_word_is_not_a_marker():
    """«Коробка для выноса» — упаковка, а не признак выноса."""
    assert is_takeaway(_items("Коробка для выноса")) is False


def test_empty_order_is_not_takeaway():
    assert is_takeaway([]) is False


import pytest                                                  # noqa: E402
from app import ingest                                         # noqa: E402
from app.schemas import NormalizedOrder                         # noqa: E402


def _order(service_type, *names):
    return NormalizedOrder(iiko_order_id="t1", org_id="org-1", number="5",
                           status="New", service_type=service_type,
                           items=_items(*names))


@pytest.mark.asyncio
async def test_hall_order_with_marker_becomes_takeaway(session):
    order = await ingest.ingest_order(session, _order("Common", "Ролл", "Вынос"))
    assert order.service_type == "ToGo"


@pytest.mark.asyncio
async def test_hall_order_without_marker_stays_hall(session):
    order = await ingest.ingest_order(session, _order("Common", "Ролл"))
    assert order.service_type == "Common"


@pytest.mark.asyncio
async def test_delivery_is_never_reclassified(session):
    """У доставки своя позиция «Вынос» ничего не значит — везёт курьер."""
    order = await ingest.ingest_order(
        session, _order("DeliveryByCourier", "Ролл", "Вынос"))
    assert order.service_type == "DeliveryByCourier"
