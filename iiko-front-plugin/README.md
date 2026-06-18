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
Код написан под **Resto.Front.Api V8** (методы взяты прямо из вашей сборки SDK).

Ссылки в проект (из SDK / папки iikoFront), **Copy Local = False**:
- **`Resto.Front.Api`** — основной контракт: `PluginContext`, `IFrontPlugin`,
  `IOperationService`, `INotificationService`. (у тебя в Downloads: `Resto.Front.Api.exe`)
- **`Resto.Front.Api.V8`** — модель данных V8: `IOrder`, `IOrderProductItem`,
  `OrderServiceTypes`, `IProduct`/`IProductCategory`. (у тебя: `Resto.Front.Api.V8.exe`)
- если компилятор попросит — добавь **System.Reactive** (его тянет API).

(Если терминал на другой версии — возьми `Resto.Front.Api.V7`/`V9` вместо V8.
Типы те же, IntelliSense поправит мелочи.)

.NET: как требует ваш Resto.Front.Api (обычно .NET Framework 4.7.x). Среда — Visual Studio.

## Сборка — через .NET SDK (рекомендуется, проще всего)
Resto.Front.Api.V8 собран под **netstandard 2.0**, поэтому старый csc.exe не
годится. Берём .NET SDK:

1. Поставь **.NET SDK** (один установщик): https://dotnet.microsoft.com/download
2. Сложи в ОДНУ папку: `SayaKds.csproj`, `SayaKdsForwarder.cs`,
   `Resto.Front.Api.exe`, `Resto.Front.Api.V8.exe`.
3. Запусти `build-dotnet.bat` (или в этой папке `dotnet build -c Release`).
4. Результат: `bin\Release\netstandard2.0\SayaKds.dll`.

(Старый `build.bat` через csc оставлен на крайний случай — он требует .NET
Framework Developer Pack и часто спотыкается о netstandard. Используй dotnet.)

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
