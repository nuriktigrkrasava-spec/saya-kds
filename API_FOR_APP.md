# Saya KDS — интеграция с мобильным приложением

Документ для команды приложения. Сервис уже развёрнут и работает.

## База
- **Base URL (прод):** `https://saya-kds.onrender.com`
- Формат: JSON (UTF-8). CORS открыт (`*`).
- Real-time: Socket.IO на том же origin (`wss://saya-kds.onrender.com`).
- Большинство ответов — массивы/объекты, ошибки — `{"detail": "..."}` с HTTP-кодом (401/403/404).

## Как устроены данные (важно для понимания)
- **Заказы создаются в iiko** (сайт/колл-центр/Yandex/Glovo/Wolt). В наш сервис они приходят автоматически по вебхуку iiko — **приложению НЕ нужно создавать заказы у нас**. Если приложение оформляет заказ — оно делает это в iiko, и заказ сам прилетит к нам.
- **Филиал** идентифицируется `org_id` (для персонала = код филиала RMS; в заказах с iiko = GUID организации).
- **Сотрудник** входит по **PIN-коду** (поле `code` из iiko). Паролей в iiko нет.

---

## 1. Авторизация персонала (PIN)
`POST /auth/login`
```json
{ "pin": "2193329" }
```
Ответ:
```json
{ "token": "<JWT, 16ч>", "name": "Alua", "role": "MN0",
  "branch_code": "18PVL", "branch_name": "Павлодар…", "org_id": "18PVL" }
```
- `role` — код должности (MN0=управляющий, СУШ=сушист, ПИЦЦ=пиццер, КР=курьер и т.д.).
- Управляющие: `MN0, Администратор, УПРЭКСПО, ADM, ADM2` — им доступны разделы аналитики/сотрудников.
- Токен кладите в заголовок `Authorization: Bearer <token>` (на будущее; сейчас часть эндпоинтов открыта).

## 2. Заказы (экраны кухни/сборщика)
- `GET /orders?org_id=<org_id>` → массив заказов филиала (активные).
- Действия по позиции: `POST /items/{item_id}/accept|ready|check` тело `{ "by": "Имя" }`.
- Действия по заказу: `POST /orders/{order_id}/assemble|take|delivered` тело `{ "by": "Имя" }`.
- Назначить курьера: `POST /orders/{order_id}/assign-courier` тело `{ "courier_code","courier_name","by" }`.

**Формат заказа (order payload):**
```json
{ "id":1, "iiko_order_id":"…", "org_id":"…", "number":"56", "status":"CookingStarted",
  "customer":"Иван", "phone":"+7…", "address":"Астана…", "lat":51.16, "lon":71.47,
  "service_type":"DeliveryByCourier", "created_at":"…Z",
  "assembled":false, "on_way":false, "delivered":false,
  "items":[ {"id":1,"name":"Филадельфия","amount":2,"station":"rolls",
             "is_checked":false,"accepted":true,"ready":false,
             "accepted_at":"…Z","ready_at":null} ] }
```

## 3. Real-time (Socket.IO)
- Подключение: `io("https://saya-kds.onrender.com")`.
- Подписка: `socket.emit("join", { org_id, zone })`, где `zone` ∈ `rolls|pizza|hot|other|assembler|courier`.
- Событие: `socket.on("order:update", (orderPayload) => …)` — апдейт по одному заказу (upsert по `id`).

## 4. Трекинг заказа для клиента (публично, без авторизации) — ГЛАВНОЕ для клиентского приложения
`GET /api/track/{iiko_order_id}`
```json
{ "number":"56", "address":"Астана…", "eta":null,
  "stages":[ {"key":"accepted","label":"Принят","done":true,"at":"…Z"},
             {"key":"cooking","label":"Готовится","done":true,"at":"…Z"},
             {"key":"assembling","label":"Собирается","done":false,"at":null},
             {"key":"on_way","label":"В пути","done":false,"at":null},
             {"key":"delivered","label":"Доставлен","done":false,"at":null} ],
  "courier": null }
```
- На этапе «В пути» поле `courier` = `{ "lat","lon","minutes_ago" }` (живая точка курьера). Иначе `null`.
- Опрашивать раз в ~10 сек. `iiko_order_id` берёте из заказа (или мы дадим способ получить его по номеру/телефону).
- **Готовый веб-вариант** этой страницы уже есть: `https://saya-kds.onrender.com/track/<iiko_order_id>` — можно открыть в WebView, не рисуя экран.

## 5. Push-уведомления клиенту (рекомендуемый путь)
- Приложение регистрирует device token (FCM/APNs).
- Когда мы добавим вызов на смену статуса (`on_way`/`ready`), бэкенд дёрнет ваш push-эндпоинт **или** отправит через FCM по токену. Это решает уведомления без WhatsApp.
- **Что нужно от команды:** определиться — push шлёт ваш бэкенд приложения (мы зовём ваш webhook на смену статуса) ИЛИ мы шлём напрямую в FCM (дайте серверный ключ FCM). Любой вариант — пара часов работы.

## 6. Курьер
- Сейчас курьер работает как **Telegram Mini App** (`/tg`, вход по PIN, его заказы, маршрут 2GIS, «Доставлено», GPS).
- Эндпоинты курьера: `POST /tg/auth`, `POST /tg/link`, `GET /tg/my-orders?courier_code=`, `POST /tg/location` (используют Telegram initData).
- Если нужен **нативный курьерский экран в приложении** — добавим токен-вариант авторизации (по PIN, без Telegram) за небольшое время. Скажите — сделаем.

## 7. Управляющему (роль-гейт)
- Филиалы/сотрудники: `GET /rms/branches` (с PIN-кодами), `GET /branches/couriers?branch_code=`.
- Аналитика: `GET /admin/analytics?org_id=`, `/admin/employee-history`, `/admin/anomalies`, `/admin/demand` (карта спроса), `/admin/couriers-locations`, `/admin/dashboard` (сводка сейчас), `/admin/report.csv` (выгрузка).

## Что нужно от команды приложения, чтобы начать
1. Платформа/стек (iOS/Android нативно, Flutter, React Native?).
2. Какие экраны нужны в приложении: только клиентский трекинг? курьер? кабинет управляющего?
3. По push: ваш бэкенд + webhook от нас, или прямой FCM (серверный ключ).

Минимальная интеграция «за вечер»: показать клиенту статус заказа — это `GET /api/track/{id}` (или WebView на `/track/{id}`).
