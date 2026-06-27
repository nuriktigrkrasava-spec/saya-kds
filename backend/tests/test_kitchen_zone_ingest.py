import pytest
from app.ingest import ingest_order
from app.schemas import NormalizedOrder, WebhookItem


def _order(items):
    return NormalizedOrder(
        iiko_order_id="o1", org_id="demo", number="1", status="New",
        customer="", phone="", address="", lat=None, lon=None, total=None,
        service_type=None, comment="", items=items,
    )


@pytest.mark.asyncio
async def test_ingest_sets_pre_merge_kitchen_zone(session):
    # «Том Ям» классифицируется как hot; station сливается в pizza,
    # а kitchen_zone должна остаться hot.
    items = [WebhookItem(product_id="p1", name="Том Ям", amount=1,
                         combo_id=None, comment="", category_name="", category_id=None)]
    order = await ingest_order(session, _order(items))
    it = order.items[0]
    assert it.station == "pizza"        # merge hot->pizza (поведение кухни)
    assert it.kitchen_zone == "hot"     # до-merge зона для аналитики
