# Спека: Придержка предзаказов на доске KDS (показывать за час до срока)

**Дата:** 2026-06-26
**Статус:** утверждён дизайн, ждёт вычитки

## 1. Проблема и цель

Предзаказы (заказы на будущее время — у iiko это `completeBefore` в будущем) сейчас **сразу** попадают на доску KDS: каждый заказ из вебхука немедленно ingest'ится и broadcast'ится сборщику/кухне. Кухня видит их задолго до срока.

**Цель:** заказ с плановым временем `completeBefore` должен появляться на доске KDS **только за час до срока**, а не сразу. Обычные заказы (без планового времени или со сроком в пределах часа) — без изменений.

Scope — ТОЛЬКО наша доска KDS (бэкенд). Физический кухонный чек печатает iikoFront сам (перехватчик `iiko-print-capture` пассивный) — его в этой спеке не трогаем.

## 2. Контекст кода (как есть)

- `completeBefore` сейчас **нигде не сохраняется** — `normalize_full_order`/`normalize_table_order` (`backend/app/iiko_cloud.py`) его не читают; нет поля ни в `NormalizedOrder` (`schemas.py`), ни в `Order` (`models.py`).
- Каждый заказ из вебхука (`POST /webhooks/iiko/cloud`, `api.py`) сразу `ingest_order` → `realtime.broadcast_order`. Доска (`useOrders` во фронте) грузит `/orders` раз и слушает Socket.IO `order:update` — поллинга нет.
- `GET /orders` (`api.py`) фильтрует только по статусу (`NOT IN Closed/Deleted/Cancelled`).
- Фоновые петли есть: `poller.py`, `alerts.py` (обе по env-интервалу, паттерн `asyncio.create_task` + `while True: check; sleep`), запускаются в `main.py` startup.

## 3. Часовой пояс (критично)

iiko Transport отдаёт время в **локальном времени филиала** (Казахстан — UTC+5), naive, формат `YYYY-MM-DD HH:MM:SS.fff` (подтверждено на живом заказе: `whenCreated 16:59` локально = 11:59 UTC). Наши таймстампы (`created_at` и т.п.) — naive UTC (`datetime.utcnow()`).

**Решение:** на ingest парсим `completeBefore` как локальное и приводим к **naive UTC** вычитанием смещения `IIKO_TZ_OFFSET_HOURS` (env, дефолт `5`). Так `Order.complete_before` хранится в UTC, консистентно с остальными полями, и сравнивается с `utcnow()`.

## 4. Дизайн

### 4.1 Захват планового времени
- `NormalizedOrder` (`schemas.py`): `complete_before: datetime | None = None`.
- `normalize_full_order` И `normalize_table_order` (`iiko_cloud.py`): прочитать `o.get("completeBefore")`, распарсить и привести к UTC через хелпер `_parse_iiko_local_to_utc(s) -> datetime | None` (парсит `YYYY-MM-DD HH:MM:SS[.fff]`, на ошибке/пустом → `None`, вычитает `IIKO_TZ_OFFSET_HOURS`). Передать в `NormalizedOrder(complete_before=...)`.
- `Order` (`models.py`): `complete_before: Mapped[datetime | None]` (nullable), `preorder_held: Mapped[bool]` (default `False`).
- Миграция через `_ADDED_COLUMNS` (`main.py`): `("orders","complete_before","TIMESTAMP")`, `("orders","preorder_held","BOOLEAN DEFAULT FALSE")`.
- `ingest_order` (`ingest.py`): `order.complete_before = data.complete_before`; пересчитать `order.preorder_held = _is_held(order.complete_before, utcnow())`.

### 4.2 Признак «придержан»
Хелпер (в `ingest.py` или новом `preorder.py`, переиспользуется ingest'ом и релизером):
```
def _is_held(complete_before, now) -> bool:
    return complete_before is not None and complete_before > now + timedelta(minutes=LEAD)
```
`LEAD = PREORDER_LEAD_MINUTES` (env, дефолт `60`).

### 4.3 Поток ingest
В обработчике вебхука (`api.py`, ветки DeliveryOrderUpdate и TableOrderUpdate) после `ingest_order(...)`:
- если `order.preorder_held` — **НЕ** вызывать `broadcast_order` (заказ молча лежит в БД);
- иначе — `broadcast_order` как сейчас.

(Перерасчёт `preorder_held` живёт внутри `ingest_order`, так что повторный вебхук по тому же заказу корректно обновит флаг.)

### 4.4 Фильтр доски
`GET /orders` (`api.py`): к существующему фильтру статусов добавить `Order.preorder_held == False`. Придержанные не возвращаются при первичной загрузке.

### 4.5 Релизер (новая фоновая петля)
- Новый модуль `backend/app/preorder.py`: `start_releaser()/stop_releaser()` по образцу `poller.py`/`alerts.py`.
- `release_due(session, now)`: выбрать заказы `preorder_held == True AND complete_before <= now + LEAD AND status NOT IN (Closed,Deleted,Cancelled)`; для каждого: `preorder_held = False`, commit, `broadcast_order(order)`.
- Петля: каждые `PREORDER_CHECK_SECONDS` (env, дефолт `60`; `<=0` отключает) вызывает `release_due`.
- Запуск/останов в `main.py` startup/shutdown рядом с poller/alerts. **Дефолт ВКЛ** (в отличие от poller/alerts) — иначе фича не работает.

### 4.6 Конфиг (`config.py`)
- `iiko_tz_offset_hours: int = 5`
- `preorder_lead_minutes: int = 60`
- `preorder_check_seconds: int = 60`

## 5. Крайние случаи
- Заказ без `completeBefore` → `complete_before=None` → `preorder_held=False` → как сейчас.
- Срок в прошлом или в пределах часа на приходе → не придержан → сразу на доску.
- Придержанный заказ обновился веббхуком (статус сменился, срок ещё далеко) → `ingest_order` пересчитает `preorder_held=True`, broadcast не делаем.
- Придержанный отменён/закрыт пока ждал → терминальный статус → `/orders` его и так прячет; `release_due` пропускает (фильтр по статусу).
- Несколько филиалов: смещение одно (`IIKO_TZ_OFFSET_HOURS`), Казахстан весь UTC+5 — достаточно.

## 6. Тестирование
- **Хелперы (юнит):** `_parse_iiko_local_to_utc` (валидное локальное → UTC-сдвиг; пустое/битое → None; с/без миллисекунд); `_is_held` (None → False; срок > now+lead → True; срок <= now+lead → False; граница).
- **Нормализация:** `normalize_full_order`/`normalize_table_order` извлекают и сдвигают `complete_before`; без `completeBefore` → None.
- **Ingest:** будущий предзаказ → `preorder_held=True`; ближний/обычный → False.
- **Вебхук:** придержанный заказ ingest'ится, но `broadcast_order` НЕ вызывается (мок); обычный — вызывается.
- **`/orders`:** придержанные не возвращаются; обычные возвращаются.
- **Релизер:** `release_due` выпускает заказ, доросший до окна (флаг снят, broadcast вызван), и пропускает ещё-далёкие и терминальные.

## 7. Вне scope
- Управление физической печатью кухонного чека (iikoFront/перехватчик).
- Показ «во сколько предзаказ» на доске или отдельная вкладка предстоящих — отдельная задача (здесь только придержка).
- Разные часовые пояса на филиал (пока единое смещение).
