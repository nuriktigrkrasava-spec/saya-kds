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
