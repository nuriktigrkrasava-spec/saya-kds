# KDS + Сборщик + Курьер для сети Saya Sushi — Спецификация

Дата: 2026-06-16
Статус: на согласовании

Аналог iikoFront + iikoSousChef: кухонный экран (KDS) на 3 зоны, экран сборщика,
экран курьера. Сеть Saya Sushi (~13 рабочих филиалов в Казахстане). Заказчик — не
программист; всё делается максимально автономно, с понятными комментариями,
`.env.example` и README по локальному запуску и деплою на Render.

---

## 1. Что переиспользуем из старого проекта, а что строим заново

Старый проект: `/path/to/workspace/приложение` (Python). Заказчику не
понравилась его реализация — **переиспользуем ТОЛЬКО механику получения заказов в
реальном времени**. Всё остальное (UI, экраны, БД, логика зон, сборщик, курьер,
аналитика) пишем с нуля.

Проверенная на живых данных механика real-time (источник истины — рабочий код
`app/iiko_cloud.py`):

| Шаг | Вызов |
|---|---|
| Авторизация Cloud | `POST https://api-ru.iiko.services/api/1/access_token` `{"apiLogin": …}` → `token` (~1 ч). **Важно: путь без `/auth/`.** |
| Приход заказа (push) | iiko шлёт вебхук `eventType="DeliveryOrderUpdate"` на `POST /webhooks/iiko/cloud?key=…`. В событии **нет состава заказа**. |
| Дозапрос состава | `POST /api/1/deliveries/by_id` `{"organizationId", "orderIds":[id]}` → полный заказ (items + модификаторы + координаты). |
| Регистрация вебхука | `POST /api/1/webhooks/update_settings` с телом `{organizationId, webHooksUri, webHooksFilter.deliveryOrderFilter.{orderStatuses,itemStatuses,errors}}`. |
| Привязка к филиалу | по `organizationId`, который **приходит внутри вебхука** (его НЕ нужно тянуть отдельным списком). |

Статусы доставки (из рабочего кода): `WaitCooking, ReadyForCooking, CookingStarted,
CookingCompleted, Waiting, OnWay, Delivered, Closed, Cancelled`.

## 2. Источники данных (строго разделены)

- **Заказы в реальном времени** — iiko Cloud по apiLogin (механика из п.1).
- **Филиалы и сотрудники** — self-hosted сервер RMS (Resto API),
  `https://saya-co.iiko.it:443/resto`. Проверено: авторизация работает, структура
  (юрлица/подразделения) отдаётся.
  - Авторизация: `GET /resto/api/auth?login=…&pass=<sha1(пароль)>` → токен
    (занимает слот лицензии — **всегда** `GET /resto/api/logout`).
  - Сотрудники: `/resto/api/employees`. Подразделения: `/resto/api/corporation/departments`.

**Список филиалов из Cloud НЕ берём.** `organizationId` приходит в каждом вебхуке;
им дозапрашиваем состав и сопоставляем с филиалом RMS через таблицу-связку
`branches.org_id ↔ branches.server_department_id` (сопоставление по имени, при первом
неизвестном `organizationId` — авто-регистрация записи филиала, имя подтягиваем из RMS).

## 3. Стек

- **Backend:** Python 3.12 + FastAPI + `python-socketio` (async) + `httpx` (вызовы iiko)
  + SQLAlchemy (async). Причина выбора Python: проверенная механика заказов уже на нём,
  переносим без переписывания — минимум риска сломать самое важное.
- **Frontend:** React + Vite, как PWA для планшетов/телефона. Крупные кнопки, крупный
  шрифт, цветовая маркировка зон (как в iikoSousChef).
- **БД:** PostgreSQL. Локально — через Docker (`docker-compose up`), без установки psql.
- **Realtime:** Socket.IO (`wss://`) с комнатами; клиент переподключается с
  экспоненциальной задержкой.
- **Хостинг:** Render (Static Site — фронт, Web Service — бэк, Render PostgreSQL).
  Сначала всё запускаем **локально на Mac**, затем деплой.

## 4. Real-time локально vs на проде

Вебхук не может достучаться до localhost напрямую. Поэтому:

- **Локально (Mac):** приёмник вебхука `POST /webhooks/iiko/cloud?key=…` пишем сразу
  «как в проде». Для проверки на живых заказах поднимаем туннель к Mac (cloudflared/ngrok)
  и регистрируем временный `webHooksUri`. Дополнительно — лёгкий ручной драйвер
  «дёрнуть `by_id` по номеру заказа» для отладки без ожидания реального заказа.
- **На Render:** регистрируем постоянный вебхук через `update_webhook_settings`.
- **Страховка:** фоновый поллер `POST /api/1/deliveries/by_revision` (раз в 10–15 c)
  доливает пропущенные изменения тем же идемпотентным upsert (по `iiko_order_id`).

## 5. Четыре экрана (React PWA)

1. **Зона «Роллы/Горячее»** и **Зона «Пицца»**: видят только позиции своей зоны своего
   филиала. Кнопка «Принял» → старт секундомера + прогресс-бар; «Готово» → фиксация времени.
2. **Сборщик:** видит весь заказ и все зоны в реальном времени. Галочка на каждой позиции.
   Когда все зоны «Готово» и все позиции отмечены — кнопка «Передать курьеру».
3. **Курьер (телефон):** отмечает взятые заказы; кнопка «Построить маршрут».
   Ключ 2GIS — **только бесплатный диплинк** → открываем по одному адресу за раз:
   `dgis://2gis.ru/routeSearch/rsType/car/from/<lon>,<lat>/to/<lon>,<lat>` (`from`
   опционально). Остальные точки показываем списком в приложении, курьер идёт по очереди.
   Платную оптимизацию (Routing API) закладываем как выключенную настройку на будущее.

## 6. Разделение позиций по зонам

Зоны: `rolls | pizza | hot | other`. Маппинг — **по категориям номенклатуры iiko
автоматически** (`GET/POST /api/1/nomenclature` → группы товаров), плюс
**админка-исключения** по конкретному `product_id`. Таблица `product_station_map`
(`product_id`/`category_id` → `station`) редактируется в админке. Каждая позиция заказа
(включая позиции из комбо — связь через `comboInformation`) присваивается зоне.

## 7. Изоляция филиалов (защита в глубину)

- JWT `{orgId, role}`; роли: `rolls`, `pizza`, `hot`, `assembler`, `courier`.
- Каждый планшет логинится под аккаунтом зоны, привязанным к одному `organizationId`
  и одной роли.
- Socket.IO комнаты `org:<id>:<zone>`; сборщик подписан на все зоны своего филиала.
  Вступление в комнату авторизуется по токену.
- **Все** SQL-запросы фильтруются по `org_id`. Чужой `organizationId` в вебхуке —
  игнорируется. Вебхук-эндпоинт проверяет общий секрет (`?key=` / `WEBHOOK_SECRET`).

## 8. Модель данных (PostgreSQL)

- `branches(id, org_id UNIQUE, server_department_id, name, lat, lon, tz)`
- `employees(id, branch_id, server_employee_id, name, role, pin_hash)`
- `orders(id, iiko_order_id UNIQUE, org_id, number, status, customer_name, phone,
  address, address_lat, address_lon, sum, created_at, accepted_at, ready_at,
  assembled_at, on_way_at, delivered_at, courier_id, revision)`
- `order_items(id, order_id, iiko_item_id, product_id, name, amount,
  station ENUM('rolls','pizza','hot','other'), combo_id, is_checked,
  station_accepted_at, station_ready_at, checked_at, checked_by)`
- `station_status(order_id, station, status, accepted_by, accepted_at, ready_at)`
- `courier_assignments(id, courier_id, order_id, sequence, assigned_at)`
- `product_station_map(product_id, category_id, station)`
- `order_events(id, order_id, type, payload, at)` — аудит/история

Аналитика считается из меток времени: среднее время готовки на сотрудника =
`avg(station_ready_at − station_accepted_at)` по `accepted_by`; время доставки =
`delivered_at − on_way_at`; дашборды фильтруются по `org_id`.

## 9. Поток данных (end-to-end)

1. iiko шлёт вебхук `DeliveryOrderUpdate` → `POST /webhooks/iiko/cloud?key=…`.
2. Бэк проверяет секрет, берёт `organizationId` + `orderId`, дозапрашивает полный заказ
   через `by_id`.
3. Идемпотентный upsert заказа + позиций (по `iiko_order_id`); каждой позиции — зона по
   `product_station_map`; комбо разбираются через `comboInformation`.
4. Socket.IO рассылает в комнаты `org:<id>:<zone>`. Планшеты получают только свой
   филиал+зону.
5. Кнопки зон ставят `station_accepted_at`/`station_ready_at`; сборщик — галочки и
   `assembled_at`; курьер — `on_way_at`/`delivered_at`.
6. Поллер `by_revision` подстраховывает от пропущенных вебхуков.

## 10. Структура проекта (монорепо)

```
при/
├─ docker-compose.yml        # PostgreSQL локально
├─ backend/                  # FastAPI + python-socketio
│  ├─ app/
│  │  ├─ iiko_cloud.py       # перенос проверенной механики: token, by_id, webhook, nomenclature
│  │  ├─ rms.py              # RMS: auth/logout, employees, departments
│  │  ├─ webhooks.py         # POST /webhooks/iiko/cloud
│  │  ├─ poller.py           # by_revision fallback
│  │  ├─ stations.py         # маппинг блюдо→зона (категории + исключения)
│  │  ├─ realtime.py         # Socket.IO, комнаты org:<id>:<zone>
│  │  ├─ models.py / db.py   # SQLAlchemy async, схема, миграции
│  │  ├─ api.py              # REST: login, заказы, история, аналитика, админка
│  │  └─ config.py           # настройки из ENV
│  └─ tests/
├─ frontend/                 # React + Vite (PWA), 4 экрана
└─ docs/superpowers/specs/   # эта спецификация
```

## 11. Секреты и деплой

- Все ключи (Cloud apiLogin, RMS login/пароль, секрет вебхука, ключ 2GIS) — только в
  ENV/`.env` (в `.gitignore`), никогда в коде/спеке/гите.
- RMS: всегда `logout` для освобождения слота лицензии.
- Cloud token обновляем до истечения 1 ч.
- Прод — платный инстанс Render (free засыпает через 15 мин, free-БД истекает через 30 дней).

## 12. План по этапам

1. **MVP (1 филиал, от и до):** Cloud auth (`access_token`) + приём вебхука + `by_id`;
   парсинг items+combos; маппинг зон по категориям; 3 зоны + сборщик
   (Принял/Готово/галочки); Socket.IO комнаты; PostgreSQL; RMS-вход сотрудников.
2. **Real-time + поллинг-страховка:** регистрация вебхука; туннель локально; таймеры/
   секундомер/просрочка (жёлтый/красный); история за день.
3. **Курьер:** координаты из заказа (или геокод), маршрут 2GIS (бесплатный диплинк),
   кнопка «Построить маршрут».
4. **Аналитика** по сотрудникам/филиалам; разворачивание всех ~13 филиалов; усиление
   ролей/безопасности.

## 13. TODO/сверить со Swagger (когда дойдём)

- Полный список Cloud delivery-статусов и точные поля фильтра вебхука — сверить с живым
  Swagger `api-ru.iiko.services` (рабочее тело уже есть в п.1, но подтвердить).
- Какие из 25 Cloud-организаций соответствуют ~13 реальным кухням RMS — определится
  автоматически по сопоставлению имён при первых вебхуках/синхронизации RMS.
