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


async def test_resolver_classifies_by_menu_group_via_name(session, monkeypatch):
    # «Пепперони» по keyword'ам -> other, но в группе меню «Пиццы» -> pizza.
    # Резолвер должен взять зону из кэша меню по имени (product_id в заказе иной).
    menu = [{"id": "menu-pep", "name": "Пепперони",
             "group_path": "Пиццы / Классические", "group_root": "Пиццы"}]
    monkeypatch.setattr(stations, "classify_name", stations.classify_name)
    from app import rms
    monkeypatch.setattr(rms, "_PROD_CACHE", menu)

    resolver = await stations.build_resolver(session)
    order_item = WebhookItem(product_id="ORDER-DIFFERENT-ID", name="Пепперони")
    assert stations.classify_name("Пепперони") == "other"  # старое поведение
    assert resolver(order_item) == "pizza"                 # новое: по группе меню
