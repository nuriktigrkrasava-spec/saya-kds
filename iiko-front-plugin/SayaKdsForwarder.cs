// Плагин iikoFront (Resto.Front.Api): форвардит заказы ЗАЛА/ВЫНОСА с терминала
// в Saya KDS (POST /ingest/front). Заказы зала не уходят в iiko Cloud-вебхуки,
// поэтому забираем их прямо на кассе.
//
// Стратегия — ПОЛЛИНГ локальных заказов раз в N секунд и отправка активных.
// Наш приёмник идемпотентен по id (front-<id>), поэтому повторная отправка
// того же заказа просто обновляет его на планшете — дублей не будет.
//
// ВНИМАНИЕ: имена свойств модели заказа (IOrder/IOrderItem) могут немного
// отличаться между версиями Resto.Front.Api (v6/v7/v8). Места, которые надо
// сверить с твоей версией SDK, помечены  // TODO[SDK].

using System;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using Resto.Front.Api;            // PluginContext, IFrontPlugin
using Resto.Front.Api.Attributes; // [PluginLicenseModuleId]

namespace SayaKds
{
    [PluginLicenseModuleId(21005108)] // обычный модуль; при сборке сверь с примером из SDK
    public sealed class SayaKdsForwarder : IFrontPlugin
    {
        // === НАСТРОЙКИ ===
        private const string IngestUrl =
            "https://saya-kds.onrender.com/ingest/front?key=saya-kds-webhook-secret";
        // Cloud org_id филиала (Алиева 10 EXPO). Для другого филиала — поменять.
        private const string OrgId = "d9a4272f-65f0-4e56-8846-434f2348889c";
        private const int PollSeconds = 4;

        private static readonly HttpClient Http = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(15)
        };

        private readonly Timer _timer;

        public SayaKdsForwarder()
        {
            // Запускаем периодический опрос локальных заказов.
            _timer = new Timer(_ => SafeTick(), null,
                TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(PollSeconds));
            PluginContext.Log.Info("[SayaKds] forwarder started");
        }

        private void SafeTick()
        {
            try { Tick(); }
            catch (Exception ex) { PluginContext.Log.Error("[SayaKds] tick failed", ex); }
        }

        private void Tick()
        {
            // Берём текущие заказы терминала.
            // TODO[SDK]: метод может называться GetOrders()/GetActiveOrders().
            var orders = PluginContext.Operations.GetOrders();

            foreach (var order in orders)
            {
                // Пропускаем доставку — она уже идёт через Cloud-вебхуки.
                // TODO[SDK]: тип обслуживания. Нужны именно зал/вынос.
                var serviceType = GetServiceType(order); // "DineIn" / "ToGo"
                if (serviceType == null) continue; // не зал/вынос -> пропускаем

                var json = BuildJson(order, serviceType);
                Post(json);
            }
        }

        // Возвращает "DineIn" для зала, "ToGo" для выноса, null если это доставка
        // (её не трогаем) или заказ не нужно слать.
        private static string GetServiceType(object order)
        {
            // TODO[SDK]: определить тип обслуживания заказа.
            // Примерная логика: если order.OrderType.OrderServiceType == Common/DineIn -> "DineIn";
            //                   если самовывоз/навынос на кассе -> "ToGo";
            //                   если доставка курьером -> вернуть null.
            return "DineIn";
        }

        private static string BuildJson(object order, string serviceType)
        {
            // TODO[SDK]: сверь геттеры с моделью IOrder/IOrderItem твоей версии.
            // Ниже — псевдо-доступ; замени на реальные свойства SDK.
            dynamic o = order;

            var sb = new StringBuilder();
            sb.Append('{');
            sb.AppendFormat("\"id\":{0},", Json(Str(o.Id)));            // уникальный id заказа на кассе
            sb.AppendFormat("\"org_id\":{0},", Json(OrgId));
            sb.AppendFormat("\"number\":{0},", Json(Str(o.Number)));
            sb.AppendFormat("\"service_type\":{0},", Json(serviceType));
            sb.AppendFormat("\"customer\":{0},", Json(SafeCustomer(o)));
            sb.AppendFormat("\"phone\":{0},", Json(SafePhone(o)));
            sb.AppendFormat("\"comment\":{0},", Json(Str(o.Comment)));
            sb.Append("\"items\":[");

            bool first = true;
            foreach (var it in o.Items)
            {
                if (!first) sb.Append(',');
                first = false;
                dynamic item = it;
                string name = Str(item.Product.Name);
                string category = SafeCategory(item);     // категория -> зона у нас
                double amount = ToDouble(item.Amount);
                string comment = Str(item.Comment);
                sb.Append('{');
                sb.AppendFormat("\"name\":{0},", Json(name));
                sb.AppendFormat("\"category\":{0},", Json(category));
                sb.AppendFormat("\"amount\":{0},", amount.ToString(System.Globalization.CultureInfo.InvariantCulture));
                sb.AppendFormat("\"comment\":{0}", Json(comment));
                sb.Append('}');
            }
            sb.Append("]}");
            return sb.ToString();
        }

        private static string SafeCustomer(dynamic o)
        {
            try { return Str(o.Customer?.Name); } catch { return ""; }
        }
        private static string SafePhone(dynamic o)
        {
            try { return Str(o.Customer?.Phone); } catch { return ""; }
        }
        private static string SafeCategory(dynamic item)
        {
            // Категория блюда (Роллы/Пиццы/Горячее) — по ней KDS раскидывает по зонам.
            try { return Str(item.Product.Category?.Name); } catch { return ""; }
        }

        private void Post(string json)
        {
            try
            {
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                var resp = Http.PostAsync(IngestUrl, content).GetAwaiter().GetResult();
                if (!resp.IsSuccessStatusCode)
                    PluginContext.Log.Warn("[SayaKds] POST " + (int)resp.StatusCode);
            }
            catch (Exception ex)
            {
                PluginContext.Log.Error("[SayaKds] POST failed", ex);
            }
        }

        // === helpers ===
        private static string Str(object v) => v?.ToString() ?? "";
        private static double ToDouble(object v)
        {
            try { return Convert.ToDouble(v, System.Globalization.CultureInfo.InvariantCulture); }
            catch { return 1; }
        }
        // Минимальное JSON-экранирование строки.
        private static string Json(string s)
        {
            if (s == null) return "\"\"";
            var sb = new StringBuilder("\"");
            foreach (var ch in s)
            {
                switch (ch)
                {
                    case '\"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default: sb.Append(ch); break;
                }
            }
            sb.Append('"');
            return sb.ToString();
        }

        public void Dispose()
        {
            _timer?.Dispose();
            PluginContext.Log.Info("[SayaKds] forwarder stopped");
        }
    }
}
