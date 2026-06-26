from datetime import datetime, timedelta

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


def test_is_held():
    from app.ingest import is_held
    now = datetime(2026, 6, 26, 10, 0, 0)
    assert is_held(None, now, 60) is False
    assert is_held(now + timedelta(minutes=90), now, 60) is True   # дальше часа
    assert is_held(now + timedelta(minutes=30), now, 60) is False  # в пределах часа
    assert is_held(now - timedelta(minutes=10), now, 60) is False  # в прошлом


async def test_ingest_sets_preorder_held(session):
    from app.ingest import ingest_order
    from app.schemas import NormalizedOrder
    far = datetime.utcnow() + timedelta(hours=5)
    order = await ingest_order(session, NormalizedOrder(
        iiko_order_id="pre1", org_id="org-1", number="9", complete_before=far))
    assert order.preorder_held is True and order.complete_before == far
    near = await ingest_order(session, NormalizedOrder(
        iiko_order_id="now1", org_id="org-1", number="10"))
    assert near.preorder_held is False


async def test_ingest_is_idempotent_on_same_iiko_id(session):
    await ingest.ingest_order(session, _order(number="101"))
    await ingest.ingest_order(session, _order(number="999"))  # тот же iiko_order_id

    count = (await session.execute(select(func.count()).select_from(Order))).scalar_one()
    items = (await session.execute(select(func.count()).select_from(OrderItem))).scalar_one()
    assert count == 1            # не задублировался
    got = (await session.execute(select(Order))).scalar_one()
    assert got.number == "999"   # обновился
    assert items == 2            # позиции пересозданы, а не удвоены
