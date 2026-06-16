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
