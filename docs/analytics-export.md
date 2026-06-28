# Выгрузка аналитики во внешний сервис

Один защищённый эндпоинт отдаёт **всю аналитику филиала одним JSON**. Твой сервис
просто делает HTTP GET с API-ключом и забирает данные.

---

## 1. Настройка ключа (один раз)

Эндпоинт защищён заголовком `X-API-Key` и **выключен, пока ключ не задан**
(fail-closed — без ключа отвечает `503`).

1. Придумай длинный секрет, например:
   ```bash
   openssl rand -hex 24
   ```
2. Render → сервис `saya-kds` → вкладка **Environment** → добавь переменную:
   - **Key:** `ANALYTICS_API_KEY`
   - **Value:** этот секрет
3. Сохрани → Render передеплоит сервис.
4. Тот же секрет пропиши у себя в сервисе (в конфиге / переменной окружения).

> Секрет нигде не светим в коде и в URL — только в заголовке `X-API-Key`.

---

## 2. Эндпоинт

```
GET https://saya-kds.onrender.com/admin/analytics/export
```

### Заголовок (обязателен)

| Заголовок | Значение |
|-----------|----------|
| `X-API-Key` | твой `ANALYTICS_API_KEY` |

### Query-параметры

| Параметр | Обяз. | Формат | По умолчанию | Описание |
|----------|:-----:|--------|--------------|----------|
| `org_id` | да | строка | — | id организации (филиала) |
| `from` | нет | `YYYY-MM-DD` | 7 дней назад | начало периода (включительно) |
| `to` | нет | `YYYY-MM-DD` | сегодня | конец периода (включительно) |
| `sla_minutes` | нет | число | `45` | порог SLA-просрочки в минутах |

`from`/`to` влияют на блоки `stage_stats`, `kitchen_zones`, `zone_load`,
`journal`. Блоки `analytics` и `anomalies` — за всё время / за сегодня
соответственно (как в исходных эндпоинтах). Даты считаются в локальном времени
филиала (UTC+5).

---

## 3. Коды ответа

| Код | Значение |
|-----|----------|
| `200` | OK — в теле весь JSON (см. ниже) |
| `401` | нет заголовка `X-API-Key` или он неверный |
| `503` | на сервере не задан `ANALYTICS_API_KEY` (выгрузка выключена) |

---

## 4. Структура ответа

```jsonc
{
  "org_id": "…",
  "period": { "from": "2026-06-21T00:00:00", "to": "2026-06-28T00:00:00" },

  // Сводка за всё время
  "analytics": {
    "totals": { "orders": 0, "delivered": 0, "in_progress": 0 },
    "employees": [ { "name": "…", "items_done": 0, "avg_prep_seconds": 0 } ],
    "stations":  [ { "station": "rolls", "items_total": 0, "items_ready": 0,
                     "avg_prep_seconds": 0 } ],
    "delivery":  { "delivered_count": 0, "avg_delivery_seconds": 0 }
  },

  // Этапы пути заказа, узкое место, SLA, лаг «собран -> выехал»
  "stage_stats": {
    "stage_aggregates": {
      "stages": [
        // key: cooking | assembly | pickup_wait | delivery | queue | iiko_close
        { "key": "delivery", "label": "Доставка", "count": 0,
          "avg": 0, "median": 0, "p95": 0 }
      ]
    },
    "bottleneck_distribution": { "cooking": 0, "delivery": 0, "…": 0 },
    "sla": { "total": 0, "breached": 0, "rate": 0.0,
             "by_day": [ { "date": "2026-06-27", "total": 0, "breached": 0 } ],
             "by_hour": [ { "hour": 13, "total": 0, "breached": 0 } ] },
    "pickup_lag": { "avg": 0, "median": 0, "p95": 0, "worst": [ … ] }
  },

  // Кухня по зонам (среднее время готовки + сотрудники)
  "kitchen_zones": {
    "zones": [ { "zone": "rolls", "count": 0, "avg": 0, "median": 0,
                 "employees": [ { "name": "…", "count": 0, "avg": 0 } ] } ]
  },

  // Нагрузка кухни по часам/зонам
  "zone_load": {
    "zones": ["pizza", "rolls", "hot"],
    "hours": [0, 1, …, 23],
    "cells": { "rolls": { "13": { "count": 0, "avg_sec": 0 } } },
    "totals_by_zone": { "rolls": { "count": 0, "avg_sec": 0 } }
  },

  // Точки спроса (для карты): координаты + число заказов
  "demand": [ { "lat": 51.16, "lon": 71.47, "count": 0,
                "sample_address": "Астана, …" } ],

  // Аномалии за сегодня
  "anomalies": {
    "fast":    [ { "name": "…", "order_number": "…", "item_name": "…",
                   "prep_seconds": 0, "accepted_at": "…", "ready_at": "…" } ],
    "reverts": [ { "order_number": "…", "count": 0 } ]
  },

  // Журнал событий за период (до 1000 строк)
  "journal": {
    "total": 0,
    "rows": [ { "at": "2026-06-27T10:05:00", "order_id": 1,
                "order_number": "1001", "type": "item_ready",
                "actor_name": "…", "item_name": "…", "detail": "" } ]
  }
}
```

Единицы: все `*_seconds` / `avg` / `p95` по этапам — **в секундах**;
`rate` в SLA — доля от 0 до 1.

### Этапы пути (`stage_stats.stage_aggregates.stages[].key`)

| key | смысл | граница (от → до) |
|-----|-------|-------------------|
| `queue` | очередь на кухню | создание → первое «Принял» |
| `cooking` | готовка | «Принял» → «Готово» |
| `assembly` | сборка | «Готово» → «Собран» |
| `pickup_wait` | ожидание курьера | «Собран» → «Забрал» |
| `delivery` | доставка | «Забрал» → «Доставлен» |
| `iiko_close` | закрытие в iiko (служебное) | «Доставлен» → закрытие |

---

## 5. Примеры

### curl
```bash
curl -H "X-API-Key: $ANALYTICS_API_KEY" \
  "https://saya-kds.onrender.com/admin/analytics/export?org_id=ORG&from=2026-06-01&to=2026-06-28"
```

### Python
```python
import os, requests

BASE = "https://saya-kds.onrender.com"
KEY = os.environ["ANALYTICS_API_KEY"]

def fetch_analytics(org_id, date_from=None, date_to=None):
    params = {"org_id": org_id}
    if date_from: params["from"] = date_from
    if date_to:   params["to"] = date_to
    r = requests.get(f"{BASE}/admin/analytics/export",
                     params=params,
                     headers={"X-API-Key": KEY},
                     timeout=60)
    r.raise_for_status()
    return r.json()

data = fetch_analytics("ORG", "2026-06-01", "2026-06-28")
print(data["stage_stats"]["sla"]["rate"])
print(data["analytics"]["delivery"])
```

### Node.js
```js
const BASE = "https://saya-kds.onrender.com";
const KEY = process.env.ANALYTICS_API_KEY;

async function fetchAnalytics(orgId, from, to) {
  const u = new URL(`${BASE}/admin/analytics/export`);
  u.searchParams.set("org_id", orgId);
  if (from) u.searchParams.set("from", from);
  if (to)   u.searchParams.set("to", to);
  const res = await fetch(u, { headers: { "X-API-Key": KEY } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

---

## 6. Как часто дёргать

Аналитика считается **на лету при каждом запросе** (без кэша), поэтому всегда
свежая. Для дашборда достаточно опрашивать раз в 1–5 минут; для разовой выгрузки
— по нужному периоду.

## 7. Если не работает

- `401` → проверь, что шлёшь заголовок `X-API-Key` и значение совпадает с
  `ANALYTICS_API_KEY` на сервере.
- `503` → переменная `ANALYTICS_API_KEY` не задана на Render (или сервис ещё не
  передеплоился после её добавления).
- Пустые цифры по доставке → по заказам ещё нет завершённого цикла (см. этап
  `delivery`); наполняется по мере реальных доставок.
