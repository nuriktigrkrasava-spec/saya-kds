"""Разрез заказов по типам: зал, доставка, вынос — и сколько из них дошло к нам.

Зал — 76% заказов филиала, а вся аналитика строилась на доставке. Разрез нужен
и как диагностика приёма, и как цифра сама по себе.
"""
from app import olap


def _row(service_type, order_type, count):
    return {"OrderServiceType": service_type, "OrderType": order_type,
            "UniqOrderId.OrdersCount": count}


class TestFromIiko:
    def test_splits_hall_delivery_and_pickup(self):
        mix = olap.order_mix([
            _row("COMMON", "Обычный заказ", 680),
            _row("DELIVERY_BY_COURIER", "Сайт доставка", 95),
            _row("DELIVERY_BY_COURIER", "Доставка", 26),
            _row("DELIVERY_PICKUP", "Сайт самовывоз", 22),
            _row("DELIVERY_PICKUP", "Самовывоз", 16),
        ])
        assert mix == {"hall": 680, "delivery": 121, "pickup": 38, "aggregator": 0}

    def test_aggregators_are_not_pickup(self):
        """Wolt и Glovo лежат в самовывозе, но забирает их курьер сервиса.

        Без разделения «вынос» филиала на 59% состоял бы из чужих курьеров.
        """
        mix = olap.order_mix([
            _row("DELIVERY_PICKUP", "Wolt", 52),
            _row("DELIVERY_PICKUP", "Glovo", 2),
            _row("DELIVERY_PICKUP", "Самовывоз", 16),
        ])
        assert mix["aggregator"] == 54
        assert mix["pickup"] == 16

    def test_aggregator_name_matched_case_insensitively(self):
        mix = olap.order_mix([_row("DELIVERY_PICKUP", "яндекс еда", 5)])
        assert mix["aggregator"] == 5

    def test_unknown_service_type_is_not_silently_dropped(self):
        mix = olap.order_mix([_row("СТРАННЫЙ", "Что-то", 3)])
        assert mix["other"] == 3

    def test_empty_rows_give_zeros(self):
        assert olap.order_mix([]) == {"hall": 0, "delivery": 0, "pickup": 0,
                                      "aggregator": 0}


class TestFromOurBase:
    def test_our_service_types_map_to_the_same_buckets(self):
        assert olap.mix_key_for_service_type("DineIn") == "hall"
        assert olap.mix_key_for_service_type("Common") == "hall"
        assert olap.mix_key_for_service_type("DeliveryByCourier") == "delivery"
        # В нашей базе агрегаторы неотличимы от самовывоза: и те, и другие
        # приезжают как DeliveryByClient. Сравниваем их с суммой в iiko.
        assert olap.mix_key_for_service_type("DeliveryByClient") == "pickup"
        assert olap.mix_key_for_service_type(None) == "other"
