"""Состав сета приходит в заказе модификаторами — терять их нельзя.

«Сет Хот» в чеке выглядит как одна позиция, а внутри лежат роллы:
«-Филадельфия жареная 4 шт», «-Филадельфия запеченная 4 шт», «-кола 1».
Пока мы их выбрасывали, повар видел на КДС только название сета и лез в
бумажный чек.
"""
import pytest
from app import iiko_cloud, ingest, realtime
from app.schemas import NormalizedOrder, WebhookItem


def _wrapper(modifiers):
    return {"id": "o-set", "order": {
        "number": "81", "status": "New",
        "items": [{"type": "Product", "amount": 1,
                   "product": {"id": "p-set", "name": "Сет Хот"},
                   "modifiers": modifiers}]}}


MODS = [
    {"product": {"id": "m1", "name": "-Филадельфия жареная 4 шт"}, "amount": 1.0},
    {"product": {"id": "m2", "name": "-Филадельфия запеченная 4 шт"}, "amount": 2.0},
    {"product": {"id": "m3", "name": "-кола 1"}, "amount": 1.0},
]


class TestParsing:
    def test_modifiers_are_kept_with_the_dish(self):
        order = iiko_cloud.normalize_full_order(_wrapper(MODS), "org-1")
        item = order.items[0]
        assert item.name == "Сет Хот"
        assert [(m.name, m.amount) for m in item.modifiers] == [
            ("Филадельфия жареная 4 шт", 1.0),
            ("Филадельфия запеченная 4 шт", 2.0),
            ("кола 1", 1.0),
        ]

    def test_leading_dash_is_stripped(self):
        """iiko помечает вложенные позиции дефисом — на экране он лишний."""
        order = iiko_cloud.normalize_full_order(_wrapper(MODS), "org-1")
        assert not any(m.name.startswith("-") for m in order.items[0].modifiers)

    def test_dish_without_modifiers_has_none(self):
        order = iiko_cloud.normalize_full_order(_wrapper(None), "org-1")
        assert order.items[0].modifiers == []


class TestStorage:
    @pytest.mark.asyncio
    async def test_modifiers_survive_ingest_and_reach_the_screen(self, session):
        data = NormalizedOrder(
            iiko_order_id="o1", org_id="org-1", number="81", status="New",
            items=[WebhookItem(product_id="p", name="Сет Хот", amount=1,
                               modifiers=[{"name": "Филадельфия", "amount": 2}])])
        order = await ingest.ingest_order(session, data)
        payload = realtime.order_payload(order)
        assert payload["items"][0]["modifiers"] == [
            {"name": "Филадельфия", "amount": 2.0}]

    @pytest.mark.asyncio
    async def test_progress_is_kept_when_the_order_is_resent(self, session):
        """iiko шлёт заказ несколько раз: состав не должен обнулять готовность."""
        data = NormalizedOrder(
            iiko_order_id="o2", org_id="org-1", number="82", status="New",
            items=[WebhookItem(product_id="p", name="Сет Хот", amount=1,
                               modifiers=[{"name": "Филадельфия", "amount": 1}])])
        order = await ingest.ingest_order(session, data)
        order.items[0].is_checked = True
        await session.commit()

        again = await ingest.ingest_order(session, data)
        assert again.items[0].is_checked is True
        assert again.items[0].modifiers == [{"name": "Филадельфия", "amount": 1.0}]
