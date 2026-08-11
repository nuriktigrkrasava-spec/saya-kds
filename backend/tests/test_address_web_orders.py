"""Адрес заказа с сайта: настоящий адрес лежит в line1, а не в улице/доме.

Жалоба заказчика 09.08.2026: в очереди курьера адреса выглядят сломанными.
Прод, заказ №73 (sourceKey=WEB): улица «----------», дом «0», координаты 0/0,
а реальный адрес — «г. Астана, ул. Туркестан 8, кв 155» в поле line1.
"""
from app.iiko_cloud import _coordinates, _format_address, normalize_full_order


def _dp(street, house, line1=None, comment=None, lat=None, lon=None, **addr):
    return {"coordinates": ({"latitude": lat, "longitude": lon}
                            if lat is not None else {}),
            "address": {"street": {"name": street}, "house": house,
                        "line1": line1, **addr},
            "comment": comment}


def test_web_order_address_falls_back_to_line1():
    got = _format_address(_dp("----------", "0",
                              line1="г. Астана, ул. Туркестан 8, кв 155",
                              comment="Примеч. к адресу: 5 подъезд, 4 этаж"))
    assert got == ("г. Астана, ул. Туркестан 8, кв 155 "
                   "(Примеч. к адресу: 5 подъезд, 4 этаж)")
    assert "----------" not in got and ", 0" not in got


def test_normal_address_is_unchanged():
    got = _format_address(_dp("Бектурова", "17/3", flat="44", entrance="1",
                              floor="9", comment="счет"))
    assert got == "Бектурова, 17/3, кв. 44, подъезд 1, этаж 9 (счет)"


def test_empty_address_stays_empty():
    assert _format_address({}) == ""


def test_label_without_text_is_not_appended():
    """iiko шлёт «Примеч. к адресу:» с пустым текстом — метка без содержания.

    На проде такие хвосты висели в адресах: «…, кв 155 (Примеч. к адресу:)».
    Курьеру это ничего не говорит, только мусорит строку.
    """
    got = _format_address(_dp("----------", "0",
                              line1="г. Астана, ул. Сыганак 58",
                              comment="Примеч. к адресу:"))
    assert got == "г. Астана, ул. Сыганак 58"


def test_comment_without_label_is_kept():
    got = _format_address(_dp("Бектурова", "17/3", comment="не звонить"))
    assert got == "Бектурова, 17/3 (не звонить)"


def test_zero_coordinates_are_not_coordinates():
    assert _coordinates({"coordinates": {"latitude": 0.0, "longitude": 0.0}}) == {}
    assert _coordinates({"coordinates": {"latitude": 51.1, "longitude": 71.4}}) == {
        "latitude": 51.1, "longitude": 71.4}


def test_web_order_end_to_end_has_no_fake_coordinates():
    order = normalize_full_order({"id": "o1", "order": {
        "number": "73",
        "deliveryPoint": _dp("----------", "0", line1="г. Астана, ул. Туркестан 8",
                             lat=0.0, lon=0.0),
        "items": [],
    }}, "org-1")
    assert order.address == "г. Астана, ул. Туркестан 8"
    assert order.lat is None and order.lon is None, "кнопка «Маршрут» не должна вести в океан"
