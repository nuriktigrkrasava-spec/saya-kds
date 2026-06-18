// iikoFront plugin (Resto.Front.Api V8): forwards HALL / TAKEAWAY orders from the
// terminal to Saya KDS (POST /ingest/front). Hall orders are NOT delivered via the
// iiko Cloud webhooks, so we read them locally on the cashier terminal.
//
// Strategy: POLL local orders every N seconds (more robust than event subscription).
// The receiver is idempotent by id (front-<id>): re-sending the same order just
// updates it on the board, no duplicates.
//
// Built against Resto.Front.Api V8 (members taken from the SDK assembly):
//   PluginContext.Operations.GetOrders()
//   IOrder.Id / Number / Comment / OrderType.OrderServiceType / Items
//   IOrderProductItem.Product(.Name/.Category.Name) / Amount / Comment
//   OrderServiceTypes.Common (hall) / DeliveryByClient (takeaway) / DeliveryByCourier (skip)
//
// NOTE: ASCII-only on purpose (no Cyrillic) so csc.exe can't misread encoding.

using System;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using Resto.Front.Api;
using Resto.Front.Api.Attributes;
using Resto.Front.Api.Data.Orders;

namespace SayaKds
{
    [PluginLicenseModuleId(21005108)]
    public sealed class SayaKdsForwarder : IFrontPlugin
    {
        // === SETTINGS ===
        private const string IngestUrl =
            "https://saya-kds.onrender.com/ingest/front?key=saya-kds-webhook-secret";
        // Cloud org_id of the branch (Aliyeva 10 EXPO). Change for another branch.
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
                foreach (var order in PluginContext.Operations.GetOrders())
                {
                    var serviceType = MapServiceType(order);
                    if (serviceType == null) continue; // skip delivery (handled by Cloud)
                    Post(BuildJson(order, serviceType));
                }
            }
            catch (Exception ex)
            {
                PluginContext.Log.Error("[SayaKds] tick failed", ex);
            }
        }

        // Common -> hall, DeliveryByClient -> takeaway, DeliveryByCourier -> null (Cloud).
        // Compare by name via ToString() so we don't depend on the exact enum type name.
        private static string MapServiceType(IOrder order)
        {
            if (order.OrderType == null) return "DineIn";
            var t = order.OrderType.OrderServiceType.ToString();
            if (t == "Common") return "DineIn";
            if (t == "DeliveryByClient") return "ToGo";
            return null; // DeliveryByCourier and others -> handled by Cloud
        }

        private static string BuildJson(IOrder order, string serviceType)
        {
            var sb = new StringBuilder();
            sb.Append('{');
            sb.AppendFormat("\"id\":{0},", Json(order.Id.ToString()));
            sb.AppendFormat("\"org_id\":{0},", Json(OrgId));
            sb.AppendFormat("\"number\":{0},", Json(Convert.ToString(order.Number)));
            sb.AppendFormat("\"service_type\":{0},", Json(serviceType));
            sb.AppendFormat("\"customer\":{0},", Json(""));
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
                // item.Comment is IOrderCookingItemComment (not a string). Exact text
                // property varies by SDK; skip per-item note for now (order.Comment
                // still carries the customer's note). TODO: map item note once confirmed.
                string comment = "";

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
