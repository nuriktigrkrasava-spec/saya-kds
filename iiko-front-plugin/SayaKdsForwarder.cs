// Плагин iikoFront (Resto.Front.Api V8): форвардит заказы ЗАЛА/ВЫНОСА с терминала
// в Saya KDS (POST /ingest/front). Заказы зала не уходят в iiko Cloud-вебхуки,
// поэтому забираем их прямо на кассе.
//
// Стратегия — ПОЛЛИНГ локальных заказов раз в N секунд (надёжнее подписки на
// события). Приёмник идемпотентен по id (front-<id>): повторная отправка того же
// заказа просто обновляет его на планшете, дублей нет.
//
// Собрано под Resto.Front.Api **V8** (методы/свойства взяты из самой сборки):
//   PluginContext.Operations.GetOrders()       — заказы зала/ресторана
//   IOrder.Id / Number / Comment / OrderType.OrderServiceType / Items
//   IOrderProductItem.Product(.Name/.Category.Name) / Amount / Comment
//   OrderServiceTypes.Common (зал) / DeliveryByClient (вынос) / DeliveryByCourier (доставка)
// Если у вас другая версия (V7/V9) — namespace'ы те же, IntelliSense поправит мелочи.

using System;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using Resto.Front.Api;
using Resto.Front.Api.Attributes;
using Resto.Front.Api.Data.Orders;       // IOrder, IOrderProductItem, OrderServiceTypes

namespace SayaKds
{
    [PluginLicenseModuleId(21005108)] // при сборке сверь номер модуля с примером из SDK
    public sealed class SayaKdsForwarder : IFrontPlugin
    {
        // === НАСТРОЙКИ ===
        private const string IngestUrl =
            "https://saya-kds.onrender.com/ingest/front?key=saya-kds-webhook-secret";
        // Cloud org_id филиала (Алиева 10 EXPO). Для другого филиала — поменять.
        private const string OrgId = "d9a4272f-65f0-4e56-8846-434f2348889c";
        private const int PollSeconds = 4;

        private static readonly HttpClient Http =
            new HttpClient { Timeout = TimeSpan.FromSeconds(15) };

        private readonly Timer _timer;

        public SayaKdsForwarder()
        {
            _timer = new Timer(_ => SafeTick(), null,
                TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(PollSeconds));
            PluginContext.Log.Info("[SayaKds] forwarder started");
        }

        private void SafeTick()
        {
            try
            {
                // Заказы зала/ресторана терминала (доставка идёт отдельно — её не берём).
                foreach (var order in PluginContext.Operations.GetOrders())
                {
                    var serviceType = MapServiceType(order);
                    if (serviceType == null) continue; // доставку пропускаем (Cloud)
                    Post(BuildJson(order, serviceType));
                }
            }
            catch (Exception ex)
            {
                PluginContext.Log.Error("[SayaKds] tick failed", ex);
            }
        }

        // Common -> зал, DeliveryByClient -> вынос/самовывоз, DeliveryByCourier -> null (Cloud).
        private static string MapServiceType(IOrder order)
        {
            var t = order.OrderType != null ? order.OrderType.OrderServiceType : OrderServiceTypes.Common;
            if (t == OrderServiceTypes.Common) return "DineIn";
            if (t == OrderServiceTypes.DeliveryByClient) return "ToGo";
            return null; // DeliveryByCourier
        }

        private static string BuildJson(IOrder order, string serviceType)
        {
            var sb = new StringBuilder();
            sb.Append('{');
            sb.AppendFormat("\"id\":{0},", Json(order.Id.ToString()));
            sb.AppendFormat("\"org_id\":{0},", Json(OrgId));
            sb.AppendFormat("\"number\":{0},", Json(Convert.ToString(order.Number)));
            sb.AppendFormat("\"service_type\":{0},", Json(serviceType));
            sb.AppendFormat("\"customer\":{0},", Json(""));   // у заказов зала клиента обычно нет
            sb.AppendFormat("\"phone\":{0},", Json(""));
            sb.AppendFormat("\"comment\":{0},", Json(order.Comment));
            sb.Append("\"items\":[");

            bool first = true;
            foreach (var item in order.Items.OfType<IOrderProductItem>())
            {
                if (!first) sb.Append(',');
                first = false;

                string name = item.Product != null ? item.Product.Name : "";
                string category = (item.Product != null && item.Product.Category != null)
                    ? item.Product.Category.Name : "";
                double amount = ToDouble(item.Amount);
                string comment = item.Comment;

                sb.Append('{');
                sb.AppendFormat("\"name\":{0},", Json(name));
                sb.AppendFormat("\"category\":{0},", Json(category));
                sb.AppendFormat("\"amount\":{0},",
                    amount.ToString(System.Globalization.CultureInfo.InvariantCulture));
                sb.AppendFormat("\"comment\":{0}", Json(comment));
                sb.Append('}');
            }
            sb.Append("]}");
            return sb.ToString();
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
        private static double ToDouble(object v)
        {
            try { return Convert.ToDouble(v, System.Globalization.CultureInfo.InvariantCulture); }
            catch { return 1; }
        }

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
