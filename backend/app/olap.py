"""OLAP-отчёты iiko через RMS (resto/api/v2/reports/olap).

Cloud-ключ к OLAP не пускает («Right api/v1/reports/olap is not allowed for this
ApiLogin»), поэтому ходим тем же RMS-логином, что и за сотрудниками.

Опоздания берём из готовой колонки Delivery.Delay — той самой, что показывает
«Отчёт заказы-доставка» в iikoOffice. Считать разницу самим нельзя: цифры
разойдутся с отчётом, по которому заказчик и проверяет работу.
"""
from __future__ import annotations
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
_ROWS_CACHE: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}


def reset_cache() -> None:
    """Сбросить кэш (тесты, ручная перепроверка после правок в iiko)."""
    _ROWS_CACHE.clear()
    _ORG_NAMES.clear()

# Строки отчёта: номер, курьер, времена, опоздание, тип доставки, адрес.
_ROW_FIELDS = [
    "Delivery.Number", "Delivery.Courier", "Delivery.ExpectedTime",
    "Delivery.ActualTime", "Delivery.Delay", "Delivery.ServiceType",
    "Delivery.Address",
]


def late_deliveries(rows: list[dict], min_delay_minutes: int = 1) -> dict:
    """Сводка опозданий по строкам OLAP: сколько всего, сколько опоздали, список.

    Самовывоз (ServiceType != COURIER) отбрасываем: iiko проставляет ему Delay,
    но курьер к нему отношения не имеет.

    ВАЖНО: Delay=null означает «приехали вовремя», а не «нет данных» — iiko
    заполняет колонку только у опоздавших (на проде у таких строк ActualTime
    раньше ExpectedTime). Считать их пропусками нельзя: тогда в total попадут
    одни опоздавшие и процент опозданий всегда будет 100.
    """
    deliveries = [r for r in rows
                  if r.get("Delivery.ServiceType") == "COURIER"
                  and r.get("Delivery.ActualTime")]

    late = [r for r in deliveries
            if r.get("Delivery.Delay") is not None
            and r["Delivery.Delay"] >= min_delay_minutes]
    delays = [r["Delivery.Delay"] for r in late]

    orders = [{
        "number": r.get("Delivery.Number"),
        "courier": r.get("Delivery.Courier"),
        "address": r.get("Delivery.Address"),
        "expected_at": r.get("Delivery.ExpectedTime"),
        "actual_at": r.get("Delivery.ActualTime"),
        "delay_minutes": r["Delivery.Delay"],
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
