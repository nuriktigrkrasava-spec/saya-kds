"""OLAP-отчёты iiko через RMS (resto/api/v2/reports/olap).

Cloud-ключ к OLAP не пускает («Right api/v1/reports/olap is not allowed for this
ApiLogin»), поэтому ходим тем же RMS-логином, что и за сотрудниками.

Опоздания берём из готовой колонки Delivery.Delay — той самой, что показывает
«Отчёт заказы-доставка» в iikoOffice. Считать разницу самим нельзя: цифры
разойдутся с отчётом, по которому заказчик и проверяет работу.
"""
from __future__ import annotations
import re
import time
from datetime import date as _date, timedelta
import httpx

from app.config import settings
from app import rms

OLAP_PATH = "/api/v2/reports/olap"

_ORG_NAMES: dict[str, str] = {}
_ORG_NAMES_TS = 0.0
_ORG_NAMES_TTL = 3600.0

# Кэш строк отчёта: (филиал, дата с, дата по) -> (когда положили, строки).
# Две минуты — компромисс: аналитику можно перезагружать, а свежие доставки
# появляются в отчёте не мгновенно.
CACHE_TTL_SECONDS = 120.0
_ROWS_CACHE: dict[tuple, tuple[float, list[dict]]] = {}


def reset_cache() -> None:
    """Сбросить кэш (тесты, ручная перепроверка после правок в iiko)."""
    _ROWS_CACHE.clear()
    _ORG_NAMES.clear()

# Строки отчёта: id (ключ склейки с нашей базой), номер, курьер, времена,
# опоздание, тип доставки, адрес.
_ROW_FIELDS = [
    "Delivery.Id", "Delivery.Number", "Delivery.Courier", "Delivery.ExpectedTime",
    "Delivery.ActualTime", "Delivery.Delay", "Delivery.ServiceType",
    "Delivery.Address",
]

# Улица-заглушка из справочника iiko — сплошные прочерки. Дом при этом бывает
# и «0», и «home», и с довеском «кв./оф. Блок С», поэтому смотрим на улицу.
_DASH_STREET = re.compile(r"-{3,}")

# Из адреса вычищаем всё служебное: подписи частей, цифры, пунктуацию и прочерки.
# Если ничего осмысленного не осталось — это тоже заглушка, а не адрес.
_ADDRESS_NOISE = re.compile(r"(ул\.|д\.|кв\./оф\.|под\.|эт\.|\d|[-–—_.,/\s])+", re.I)


def clean_address(value: str | None) -> str | None:
    """Адрес из OLAP или None, если там заглушка вида «ул. ---------- д. 0».

    Заказы с сайта и из приложения (sourceKey WEB/IOS) приходят в iiko с улицей
    «----------» и домом «0», а настоящий адрес лежит в line1. OLAP собирает
    строку из улицы и дома и про line1 не знает — показывать эти прочерки
    бессмысленно, адрес подставим из своей базы.
    """
    value = (value or "").strip()
    if not value or _DASH_STREET.search(value) or not _ADDRESS_NOISE.sub("", value):
        return None
    return value


def late_deliveries(rows: list[dict], min_delay_minutes: int = 1,
                    include_pickup: bool = False) -> dict:
    """Сводка опозданий по строкам OLAP: сколько всего, сколько опоздали, список.

    Самовывоз по умолчанию отбрасываем: iiko проставляет ему Delay, но курьер к
    нему отношения не имеет. include_pickup=True возвращает его в счёт — на
    проде за 18.08 курьерских опозданий было 2, а самовывозных 10, и цифра «2»
    без объяснения выглядела как потеря данных.

    ВАЖНО: Delay=null означает «приехали вовремя», а не «нет данных» — iiko
    заполняет колонку только у опоздавших (на проде у таких строк ActualTime
    раньше ExpectedTime). Считать их пропусками нельзя: тогда в total попадут
    одни опоздавшие и процент опозданий всегда будет 100.
    """
    kinds = ("COURIER", "PICKUP") if include_pickup else ("COURIER",)
    deliveries = [r for r in rows
                  if r.get("Delivery.ServiceType") in kinds
                  and r.get("Delivery.ActualTime")]

    late = [r for r in deliveries
            if r.get("Delivery.Delay") is not None
            and r["Delivery.Delay"] >= min_delay_minutes]
    delays = [r["Delivery.Delay"] for r in late]

    orders = [{
        "order_id": r.get("Delivery.Id"),
        "number": r.get("Delivery.Number"),
        "courier": r.get("Delivery.Courier"),
        "address": clean_address(r.get("Delivery.Address")),
        "expected_at": r.get("Delivery.ExpectedTime"),
        "actual_at": r.get("Delivery.ActualTime"),
        "delay_minutes": r["Delivery.Delay"],
        "is_pickup": r.get("Delivery.ServiceType") == "PICKUP",
    } for r in sorted(late, key=lambda x: -x["Delivery.Delay"])]

    return {
        "total": len(deliveries),
        "late_count": len(late),
        "late_rate": (len(late) / len(deliveries)) if deliveries else 0.0,
        "avg_delay_minutes": (sum(delays) / len(delays)) if delays else None,
        "max_delay_minutes": max(delays) if delays else None,
        "orders": orders,
    }


async def department_name(org_id: str) -> str | None:
    """Название филиала (Department в OLAP) по Cloud org_id. Кэш на час.

    Названия организаций Cloud и торговых предприятий RMS совпадают — на этом
    же совпадении держится rms.branch_to_org_id().
    """
    global _ORG_NAMES_TS
    from app import iiko_cloud
    now = time.monotonic()
    if not _ORG_NAMES or (now - _ORG_NAMES_TS) >= _ORG_NAMES_TTL:
        orgs = await iiko_cloud.fetch_organizations()
        _ORG_NAMES.clear()
        _ORG_NAMES.update({o["id"]: o.get("name") or "" for o in orgs if o.get("id")})
        _ORG_NAMES_TS = now
    return _ORG_NAMES.get(org_id)


async def fetch_delivery_rows(department: str, date_from: str, date_to: str) -> list[dict]:
    """Строки отчёта «доставки» по филиалу за период, с кэшом на CACHE_TTL_SECONDS.

    Каждый заход в RMS занимает слот лицензии, а аналитику открывают часто и
    перезагружают страницу — без кэша управляющие рискуют не попасть в
    iikoOffice. Ошибку не кэшируем: упавший RMS не должен залипнуть на две
    минуты, следующий показ обязан попробовать снова.
    """
    key = (department, date_from, date_to)
    hit = _ROWS_CACHE.get(key)
    if hit is not None and (time.monotonic() - hit[0]) < CACHE_TTL_SECONDS:
        return hit[1]
    rows = await _fetch_delivery_rows_uncached(department, date_from, date_to)
    _ROWS_CACHE[key] = (time.monotonic(), rows)
    return rows


def build_report_body(department: str, date_from: str, date_to: str) -> dict:
    """Тело запроса к OLAP. Даты снаружи — ВКЛЮЧИТЕЛЬНО, как во всей аналитике.

    Верхнюю границу сдвигаем на день вперёд: OpenDate.Typed её не включает
    (проверено на проде — сумма посуточных кусков сходится с целым периодом
    только так). Без сдвига из отчёта молча выпадал бы последний день, обычно
    сегодняшний, а «за конкретный день» (from == to) превращалось в пустой
    диапазон, на который RMS отвечает 409 Conflict.

    Фильтруем по названию торгового предприятия: в RMS оно совпадает с названием
    Cloud-организации — на этом же совпадении построен rms.branch_to_org_id().
    """
    to_exclusive = (_date.fromisoformat(date_to) + timedelta(days=1)).isoformat()
    return {
        "reportType": "DELIVERIES",
        "buildSummary": False,
        "groupByRowFields": _ROW_FIELDS,
        "groupByColFields": [],
        "aggregateFields": ["UniqOrderId.OrdersCount"],
        "filters": {
            "OpenDate.Typed": {"filterType": "DateRange", "periodType": "CUSTOM",
                               "from": date_from, "to": to_exclusive},
            "Department": {"filterType": "IncludeValues", "values": [department]},
        },
    }


# Агрегаторы забирают заказ своим курьером, но в iiko это «самовывоз».
# Без разделения «вынос» филиала больше чем наполовину состоял бы из чужих
# курьеров: за неделю Wolt 52 против 38 настоящих самовывозов.
AGGREGATOR_NAMES = ("wolt", "glovo", "яндекс", "yandex", "chocofood", "choco",
                    "delivery club", "bolt")

MIX_LABELS = {"hall": "Зал", "delivery": "Доставка", "pickup": "Вынос",
              "aggregator": "Агрегаторы", "other": "Прочее"}


def _mix_key(service_type: str | None, order_type: str | None) -> str:
    st = (service_type or "").upper()
    if st == "COMMON":
        return "hall"
    if st == "DELIVERY_BY_COURIER":
        return "delivery"
    if st == "DELIVERY_PICKUP":
        name = (order_type or "").lower()
        return "aggregator" if any(a in name for a in AGGREGATOR_NAMES) else "pickup"
    return "other"


def mix_key_for_service_type(service_type: str | None) -> str:
    """Наш Order.service_type в те же корзины, что и режим обслуживания iiko.

    Агрегаторы в нашей базе неотличимы от самовывоза: и те, и другие приходят
    как DeliveryByClient. Поэтому «вынос» у нас сравнивается с суммой выноса и
    агрегаторов в iiko.
    """
    st = (service_type or "").strip()
    # Кассовый вынос мы помечаем ToGo, чтобы кухня видела разницу, но в iiko он
    # проходит как COMMON — при сверке кладём его в ту же корзину, иначе
    # покрытие разъедется.
    if st in ("DineIn", "Common", "ToGo"):
        return "hall"
    if st == "DeliveryByCourier":
        return "delivery"
    if st == "DeliveryByClient":
        return "pickup"
    return "other"


def order_mix(rows: list[dict]) -> dict[str, int]:
    """Сколько заказов каждого типа в iiko за период."""
    mix = {"hall": 0, "delivery": 0, "pickup": 0, "aggregator": 0}
    for row in rows:
        key = _mix_key(row.get("OrderServiceType"), row.get("OrderType"))
        mix[key] = mix.get(key, 0) + int(row.get("UniqOrderId.OrdersCount") or 0)
    return mix


def build_order_mix_body(department: str, date_from: str, date_to: str) -> dict:
    """Запрос «сколько заказов какого типа» — по чекам, а не по блюдам."""
    body = build_sales_body(department, date_from, date_to)
    body["groupByRowFields"] = ["OrderServiceType", "OrderType"]
    body["aggregateFields"] = ["UniqOrderId.OrdersCount"]
    return body


async def fetch_order_mix(department: str, date_from: str, date_to: str) -> dict[str, int]:
    """Разрез заказов филиала по типам за период, с общим кэшом."""
    key = ("mix", department, date_from, date_to)
    hit = _ROWS_CACHE.get(key)
    if hit is not None and (time.monotonic() - hit[0]) < CACHE_TTL_SECONDS:
        return hit[1]
    base = settings.rms_base.rstrip("/")
    body = build_order_mix_body(department, date_from, date_to)
    async with rms.session() as rms_key:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(f"{base}{OLAP_PATH}", params={"key": rms_key},
                                  json=body)
            r.raise_for_status()
            rows = r.json().get("data", [])
    mix = order_mix(rows)
    _ROWS_CACHE[key] = (time.monotonic(), mix)
    return mix


def build_sales_body(department: str, date_from: str, date_to: str) -> dict:
    """Продажи блюд по часам — на них считаем нагрузку кухни по зонам.

    Наша история заказов стёрта 09.08.2026, а планировать смены надо по неделям,
    поэтому спрос берём из iiko. Блюдо раскладываем по зонам своим
    классификатором, для этого просим название и корневую группу меню.
    """
    to_exclusive = (_date.fromisoformat(date_to) + timedelta(days=1)).isoformat()
    return {
        "reportType": "SALES",
        "buildSummary": False,
        "groupByRowFields": ["OpenDate.Typed", "HourOpen", "DishName",
                             "DishGroup", "DishGroup.TopParent"],
        "groupByColFields": [],
        "aggregateFields": ["DishAmountInt"],
        "filters": {
            "OpenDate.Typed": {"filterType": "DateRange", "periodType": "CUSTOM",
                               "from": date_from, "to": to_exclusive},
            "Department": {"filterType": "IncludeValues", "values": [department]},
            "DeletedWithWriteoff": {"filterType": "IncludeValues",
                                    "values": ["NOT_DELETED"]},
        },
    }


async def fetch_sales_rows(department: str, date_from: str, date_to: str) -> list[dict]:
    """Строки продаж по часам, с тем же кэшом, что и отчёт по доставкам."""
    key = ("sales", department, date_from, date_to)
    hit = _ROWS_CACHE.get(key)
    if hit is not None and (time.monotonic() - hit[0]) < CACHE_TTL_SECONDS:
        return hit[1]
    base = settings.rms_base.rstrip("/")
    body = build_sales_body(department, date_from, date_to)
    async with rms.session() as rms_key:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(f"{base}{OLAP_PATH}", params={"key": rms_key},
                                  json=body)
            r.raise_for_status()
            rows = r.json().get("data", [])
    _ROWS_CACHE[key] = (time.monotonic(), rows)
    return rows


async def _fetch_delivery_rows_uncached(department: str, date_from: str,
                                        date_to: str) -> list[dict]:
    """Сам запрос в OLAP (даты YYYY-MM-DD, включительно)."""
    base = settings.rms_base.rstrip("/")
    body = build_report_body(department, date_from, date_to)
    async with rms.session() as key:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(f"{base}{OLAP_PATH}", params={"key": key}, json=body)
            r.raise_for_status()
            return r.json().get("data", [])
