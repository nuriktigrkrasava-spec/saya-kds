"""Опоздания доставок из OLAP-отчёта iiko (reportType DELIVERIES).

Считаем на готовой колонке Delivery.Delay — той же, что показывает
«Отчёт заказы-доставка» в iikoOffice, чтобы цифры сходились с отчётом.
"""
from app import olap


def _row(number, delay, service="COURIER", courier="Иванов",
         expected="2026-08-09T14:00:00", actual="2026-08-09T14:30:00"):
    return {
        "Delivery.Number": number,
        "Delivery.Courier": courier,
        "Delivery.Delay": delay,
        "Delivery.ServiceType": service,
        "Delivery.ExpectedTime": expected,
        "Delivery.ActualTime": actual,
        "Delivery.Address": "ул. Абая 1",
    }


def test_counts_only_late_courier_deliveries():
    res = olap.late_deliveries([
        _row(1, 12),
        _row(2, 0),
        _row(3, -5),          # приехал раньше срока
        _row(4, 30),
    ])
    assert res["total"] == 4
    assert res["late_count"] == 2
    assert res["late_rate"] == 0.5


def test_pickup_is_not_a_delivery():
    """Самовывоз в iiko тоже получает Delay, но это не опоздание курьера."""
    res = olap.late_deliveries([_row(1, 40, service="PICKUP"), _row(2, 10)])
    assert res["total"] == 1
    assert res["late_count"] == 1
    assert [o["number"] for o in res["orders"]] == [2]


def test_null_delay_means_on_time_not_missing_data():
    """iiko заполняет Delay только у опоздавших: null = приехали вовремя.

    Проверено на проде: у строк с Delay=null ActualTime раньше ExpectedTime.
    Если считать их «нет данных», в total попадут одни опоздавшие и процент
    опозданий будет 100.
    """
    res = olap.late_deliveries([_row(1, None), _row(2, 12)])
    assert res["total"] == 2
    assert res["late_count"] == 1
    assert res["late_rate"] == 0.5


def test_service_rows_are_skipped():
    """Итоговые строки OLAP приходят вообще без типа доставки — они не заказы."""
    res = olap.late_deliveries([
        {"Delivery.Number": None, "Delivery.ServiceType": None,
         "Delivery.Delay": None, "Delivery.ActualTime": None},
        _row(7, 5),
    ])
    assert res["total"] == 1


def test_undelivered_orders_are_skipped():
    """Без фактического времени доставки опоздание не определено."""
    res = olap.late_deliveries([_row(1, None, actual=None), _row(2, 5)])
    assert res["total"] == 1
    assert res["late_count"] == 1


def test_each_late_order_listed_worst_first():
    res = olap.late_deliveries([_row(1, 12), _row(2, 47), _row(3, 3)])
    assert [(o["number"], o["delay_minutes"]) for o in res["orders"]] == [
        (2, 47), (1, 12), (3, 3)]
    first = res["orders"][0]
    assert first["courier"] == "Иванов"
    assert first["expected_at"] == "2026-08-09T14:00:00"
    assert first["actual_at"] == "2026-08-09T14:30:00"
    assert first["address"] == "ул. Абая 1"


def test_avg_delay_counts_only_late_ones():
    res = olap.late_deliveries([_row(1, 10), _row(2, 20), _row(3, 0)])
    assert res["avg_delay_minutes"] == 15
    assert res["max_delay_minutes"] == 20


def test_empty_input_gives_zeros_not_crash():
    res = olap.late_deliveries([])
    assert res == {"total": 0, "late_count": 0, "late_rate": 0.0,
                   "avg_delay_minutes": None, "max_delay_minutes": None,
                   "orders": []}
