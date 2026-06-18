# Saya KDS — плагин iikoFront (форвардер заказов зала)

Заказы зала/выноса не уходят в iiko Cloud (подтверждено: 0 `TableOrderUpdate`,
`order/by_revision` запрещён для apiLogin). Поэтому забираем их прямо на кассе:
плагин периодически читает локальные заказы и POST-ит их в Saya KDS.

Принимающая сторона уже на проде:
```
POST https://saya-kds.onrender.com/ingest/front?key=saya-kds-webhook-secret
```
Контракт тела — см. `SayaKdsForwarder.BuildJson`. Идемпотентно по id заказа.

## Что нужно для сборки
- **Версия iikoFront** — от неё зависит, какой `Resto.Front.Api.dll` подключать.
  Файлы лежат в папке установки iikoFront (обычно
  `C:\Program Files\iiko\iikoRMS\Front.Net\` или `…\iikoFront\Api\`).
- .NET (под версию, которую требует ваш Resto.Front.Api — обычно .NET Framework 4.x).
- Visual Studio / `msbuild` / `dotnet build`.

## Сборка (пример)
1. Создай проект Class Library под нужный .NET Framework.
2. Добавь ссылки на `Resto.Front.Api.dll` (и зависимые `Resto.Front.Api.*.dll`)
   из папки iikoFront. Свойство ссылок: **Copy Local = False**.
3. Добавь в проект `SayaKdsForwarder.cs`.
4. Собери в `SayaKds.dll`.

## Установка на терминал
1. Создай папку: `…\iikoFront\Plugins\SayaKds\`.
2. Скопируй туда `SayaKds.dll`.
3. Перезапусти iikoFront. В логах должно появиться `[SayaKds] forwarder started`.

## Что сверить с вашей версией SDK (помечено `// TODO[SDK]`)
- метод получения заказов: `PluginContext.Operations.GetOrders()` vs `GetActiveOrders()`;
- определение типа обслуживания (зал/вынос vs доставка) — `GetServiceType`;
- геттеры модели: `order.Id/Number/Comment/Customer`, `item.Product.Name`,
  `item.Product.Category.Name`, `item.Amount`, `item.Comment`.

Эти места — единственное, что зависит от версии. HTTP-форвардинг и JSON уже готовы.

## Настройки в коде (`SayaKdsForwarder.cs`)
- `IngestUrl` — адрес приёмника (+ `key` = WEBHOOK_SECRET).
- `OrgId` — Cloud org_id филиала (Алиева 10 EXPO = `d9a4272f-65f0-4e56-8846-434f2348889c`).
- `PollSeconds` — период опроса (по умолчанию 4с).

## Быстрая проверка приёмника (без плагина)
Можно убедиться, что зал доезжает до планшета, отправив заказ вручную:
```
curl -X POST "https://saya-kds.onrender.com/ingest/front?key=saya-kds-webhook-secret" \
  -H "Content-Type: application/json" \
  -d '{"id":"test-1","org_id":"d9a4272f-65f0-4e56-8846-434f2348889c","number":"777","service_type":"DineIn","customer":"Тест","comment":"без имбиря","items":[{"name":"Сет Фаворит","category":"Роллы","amount":1,"comment":"без лука"}]}'
```
Заказ №777 должен появиться на планшете сборщика, ролл — в зоне роллов, с пометкой 📝.
