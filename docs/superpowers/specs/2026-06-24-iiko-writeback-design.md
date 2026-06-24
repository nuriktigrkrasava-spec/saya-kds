# Спека 1: Запись обратно в iiko (назначение курьера + закрытие заказа)

**Дата:** 2026-06-24
**Ветка:** phase1-backend-ingest (или новая feature-ветка)
**Статус:** утверждён дизайн, ждёт вычитки

## 1. Цель

Сейчас система по отношению к iiko — **только чтение**: заказы приходят Cloud-вебхуком / перехватом печати, а действия сборщика и курьера (`assemble`, `assign-courier`, `take`, `delivered`) меняют **только локальную БД и доску**. Обратно в iiko ничего не пишется.

Эта спека добавляет **запись в iiko Cloud Transport API** по двум сценариям:

- **п.2 — Сборщик назначает курьера** → курьер проставляется в самой iiko.
- **п.3 — Курьер закрывает заказ с типом оплаты** → заказ в iiko получает оплату и закрывается («полное закрытие, как касса»).

Работаем **только с Cloud-заказами доставки** (`org_id` + `iiko_order_id` уже хранятся в `orders`). Заказы, которых нет в Cloud, не трогаем.

## 2. Что подтверждено спайком (2026-06-24)

Пробные вызовы Transport API (`https://api-ru.iiko.services`) с заведомо несуществующим `orderId` на живом ключе (Render env `CLOUD_API_LOGIN`):

| Метод | Назначение | Право у ключа |
|---|---|---|
| `deliveries/update_order_courier` | назначить курьера (поле **`employeeId`**) | ✅ открыт |
| `deliveries/update_order_payments` | оплата (поле **`paymentItems`**) | ✅ открыт |
| `deliveries/close` | закрыть заказ | ✅ открыт |
| `employees/couriers` (чтение) | список курьеров Cloud с id | ✅ открыт |
| `deliveries/change_delivery_status` | статус готов/в пути/доставлен | ❌ **запрещён ключу** |

**Решение заказчика:** за правом `change_delivery_status` не гоняемся. Зеркалим **факты** — курьера и закрытие. Внутри iiko заказ не проходит промежуточные «в пути/доставлен»: показывает назначенного курьера, а `close` в конце переводит его в **Closed**. Трекинг «в пути» живёт в нашем приложении (доска + мини-апп курьера).

## 3. Архитектура

### 3.1 Новый модуль `backend/app/iiko_cloud_write.py`

Переиспользует `iiko_cloud.get_token()` и `BASE`. Чистые async-функции, каждая делает один POST и возвращает `(ok: bool, detail: dict)`:

- `assign_courier(org_id, order_id, employee_id) -> (ok, detail)`
  POST `deliveries/update_order_courier`, тело `{organizationId, orderId, employeeId}`.
- `set_payments(org_id, order_id, payment_items) -> (ok, detail)`
  POST `deliveries/update_order_payments`, тело `{organizationId, orderId, paymentItems}`.
- `close_order(org_id, order_id) -> (ok, detail)`
  POST `deliveries/close`, тело `{organizationId, orderId}`.

Функции **не бросают** на 4xx/5xx от iiko — возвращают `ok=False` + тело ошибки, чтобы вызывающий код решал, как реагировать (см. 3.4).

### 3.2 Связка курьеров RMS ↔ Cloud `couriers.py` (или внутри write-модуля)

`update_order_courier` хочет Cloud-`employeeId`, а у нас курьер — это RMS `code`/`name` (см. `/branches/couriers`, `AssignCourierIn`).

- `cloud_courier_id(org_id, courier_name) -> str | None`
- Источник: POST `employees/couriers` `{organizationIds:[org_id]}` → `[{organizationId, items:[{id, firstName, lastName, displayName,...}]}]`.
- Матч по **нормализованному имени** (`rms._norm_name`, как уже делается для филиалов RMS↔Cloud).
- Кэш per-org ~5 мин, чтобы не жечь запросы (по аналогии с `_EMP_CACHE`).
- Нет совпадения → `None` → курьера в iiko не шлём, помечаем рассинхрон (3.4).

### 3.3 Резолвер типа оплаты `payment_types`

Курьер выбирает «наличные» / «карта». iiko требует конкретный `paymentTypeId` филиала.

- `payment_item(org_id, kind) -> dict | None`, где `kind ∈ {"Cash","Card"}`.
- Источник: POST `payment_types` `{organizationIds:[org_id]}` → берём тип с подходящим `paymentTypeKind`.
- Кэш per-org ~30 мин.
- Возвращает элемент `paymentItems`: `{paymentTypeKind, sum, paymentTypeId, isProcessedExternally: true}` (сумма = `order.total`).

### 3.4 Устойчивость и аудит

Локальная доска **не зависит** от iiko — локальный статус ставим всегда, как сейчас. Поверх:

- Каждый вызов записи логируем в `order_events` (`type="iiko_write"`, `payload={method, request, ok, error}`). Это аудит и материал для аналитики (п.4).
- Если вызов в iiko упал (или курьер не сматчился) — на заказе выставляем флаг рассинхрона **`iiko_sync_error`** (новое поле, см. 4.1), чтобы управляющий видел «не ушло в iiko».
- **Ретрай:** лёгкий — повтор при следующем действии над заказом + ручная кнопка «повторить отправку в iiko» в админке (детали — в плане). Без отдельного воркера на этой итерации (YAGNI).

## 4. Изменения по слоям

### 4.1 Модель `Order`
- Новое поле `iiko_sync_error: str | None` — текст последней ошибки записи в iiko (NULL = всё синхронно). Миграция: `ALTER TABLE orders ADD COLUMN`.

### 4.2 Эндпоинт `POST /orders/{order_id}/assign-courier` (п.2)
После локальной записи (как сейчас, `api.py:911`):
1. `employee_id = await cloud_courier_id(order.org_id, body.courier_name)`
2. если есть → `assign_courier(order.org_id, order.iiko_order_id, employee_id)`
3. результат → `order_events` + `order.iiko_sync_error`.

### 4.3 Закрытие заказа курьером (п.3)
Курьер в мини-аппе на «Доставлен» выбирает тип оплаты. Нужен новый/расширенный эндпоинт (сейчас `delivered` тип оплаты не принимает, `api.py:388`):
- `POST /orders/{order_id}/delivered` принимает `payment_kind: "Cash"|"Card"`.
- Локально: `order.delivered_at = now`.
- В iiko по порядку:
  1. `item = payment_item(org_id, payment_kind)`; `set_payments(org_id, iiko_order_id, [item])`
  2. при `ok` → `close_order(org_id, iiko_order_id)`
- Результат каждого шага → `order_events` + `order.iiko_sync_error`.

### 4.4 Фронт
- Мини-апп курьера: на «Доставлен» — выбор «Наличные / Карта», шлём `payment_kind`.
- Доска/админка сборщика: бейдж «не ушло в iiko» при `iiko_sync_error`, кнопка «повторить».

## 5. Открытый риск (проверить на реализации, на одном реальном заказе)

`change_delivery_status` ключу запрещён, статус мы не двигаем. Нужно проверить на **живом тестовом заказе**, что `deliveries/close` проходит **напрямую** из текущего статуса iiko (например, заказ ещё «готовится»), без обязательного перевода в Delivered. Если iiko требует предварительный статус — поднимаем вопрос включения `change_delivery_status` в iikoWeb. Бизнес-логику пишем так, чтобы этот шаг легко добавился.

## 6. Вне scope этой спеки
- Зеркалирование статусов готов/в пути/доставлен в iiko (ждёт права `change_delivery_status`).
- Заказы вне Cloud (зал/самовывоз только во фронте).
- Аналитика (п.4) — отдельная спека; переиспользует `order_events` из 3.4.

## 7. Тестирование
- Юнит: матч курьера по имени (точное/с разным регистром/нет совпадения); резолвер типа оплаты (Cash/Card/нет типа); сборка тел запросов с правильными полями `employeeId`/`paymentItems`.
- Контракт (мок httpx): успех и ошибка iiko → корректные `ok`/`iiko_sync_error`/`order_events`.
- Эндпоинты: `assign-courier` и `delivered` пишут локально даже при падении iiko (доска не ломается).
- Ручной прогон на одном реальном заказе (риск из §5) — перед выкаткой.
