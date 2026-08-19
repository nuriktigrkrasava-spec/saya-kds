"""Опоздания доставок из OLAP-отчёта iiko (reportType DELIVERIES).

Считаем на готовой колонке Delivery.Delay — той же, что показывает
«Отчёт заказы-доставка» в iikoOffice, чтобы цифры сходились с отчётом.
"""
from app import olap


def _date_filter(date_from, date_to):
    return olap.build_report_body("Алиева", date_from, date_to)["filters"]["OpenDate.Typed"]


def test_upper_bound_is_shifted_a_day_because_olap_excludes_it():
    """Наш API отдаёт период включительно, OLAP верхнюю дату не включает.

    Проверено на проде: сумма [1,2)+[2,3)+[3,4) сходится с [1,4) по числу строк
    только при исключительной границе. Без сдвига последний день (обычно
    сегодняшний) молча терялся бы из отчёта.
    """
    f = _date_filter("2026-08-04", "2026-08-11")
    assert (f["from"], f["to"]) == ("2026-08-04", "2026-08-12")


def test_single_day_becomes_a_one_day_range():
    """from == to — «за конкретный день». В OLAP это пустой диапазон и 409."""
    f = _date_filter("2026-08-09", "2026-08-09")
    assert (f["from"], f["to"]) == ("2026-08-09", "2026-08-10")


def _row(number, delay, service="COURIER", courier="Иванов",
         expected="2026-08-09T14:00:00", actual="2026-08-09T14:30:00",
         address="ул. Абая 1", order_id="oid-1"):
    return {
        "Delivery.Id": order_id,
        "Delivery.Number": number,
        "Delivery.Courier": courier,
        "Delivery.Delay": delay,
        "Delivery.ServiceType": service,
        "Delivery.ExpectedTime": expected,
        "Delivery.ActualTime": actual,
        "Delivery.Address": address,
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


def test_placeholder_address_is_not_shown_as_is():
    """Заказы с сайта и из приложения приходят в OLAP с «ул. ---------- д. 0».

    Настоящий адрес iiko кладёт в line1, а OLAP собирает строку из улицы и дома
    и про line1 не знает. Показывать эти полоски бессмысленно — отдаём None,
    чтобы адрес подставили из нашей базы.
    """
    res = olap.late_deliveries([_row(1, 10, address="ул. ---------- д. 0")])
    assert res["orders"][0]["address"] is None


def test_placeholder_with_house_home_is_also_recognized():
    """Второй вид заглушки с прода: дом приходит словом «home».

    Прочерки вместо улицы — достаточный признак: осмысленного адреса там нет,
    что бы ни стояло в доме.
    """
    res = olap.late_deliveries([
        _row(1, 10, address="ул. ---------- д. home"),
        _row(2, 10, address="ул. ---------- д. home кв./оф. Блок С"),
    ])
    assert [o["address"] for o in res["orders"]] == [None, None]


def test_real_address_is_kept():
    res = olap.late_deliveries([_row(1, 10, address="ул. АЛМАТЫ д. 13 кв./оф. 352")])
    assert res["orders"][0]["address"] == "ул. АЛМАТЫ д. 13 кв./оф. 352"


def test_order_id_is_returned_for_joining_with_our_base():
    res = olap.late_deliveries([_row(1, 10, order_id="abc-123")])
    assert res["orders"][0]["order_id"] == "abc-123"


def test_empty_input_gives_zeros_not_crash():
    res = olap.late_deliveries([])
    assert res == {"total": 0, "late_count": 0, "late_rate": 0.0,
                   "avg_delay_minutes": None, "max_delay_minutes": None,
                   "orders": []}


def test_pickup_can_be_included_on_demand():
    """Самовывоз тоже опаздывает — управляющему это видно в отчёте iiko.

    По умолчанию не считаем (курьер к нему отношения не имеет), но прятать
    совсем нельзя: на проде за 18.08 курьерских опозданий 2, а самовывозных 10,
    и цифра «2» выглядела как потеря данных.
    """
    rows = [_row(1, 10), _row(2, 40, service="PICKUP")]
    only_courier = olap.late_deliveries(rows)
    with_pickup = olap.late_deliveries(rows, include_pickup=True)
    assert only_courier["late_count"] == 1
    assert with_pickup["late_count"] == 2
    assert with_pickup["total"] == 2


def test_included_pickup_is_marked_in_the_row():
    """В списке должно быть видно, что это самовывоз, а не работа курьера."""
    res = olap.late_deliveries([_row(2, 40, service="PICKUP")], include_pickup=True)
    assert res["orders"][0]["is_pickup"] is True
    assert olap.late_deliveries([_row(1, 10)])["orders"][0]["is_pickup"] is False
