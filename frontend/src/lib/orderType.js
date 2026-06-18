// Канал заказа: доставка / зал / самовывоз / агрегатор (Wolt/Яндекс/Glovo).
// Курьера из нашего штата назначаем ТОЛЬКО на доставку (needsCourier=true);
// зал/самовывоз/агрегатор закрываются кнопкой «Выдан» (свой курьер/клиент сам).

const AGGREGATORS = [
  { keys: ["wolt"], label: "Wolt", emoji: "🟦" },
  { keys: ["яндекс", "yandex"], label: "Яндекс.Еда", emoji: "🟥" },
  { keys: ["glovo"], label: "Glovo", emoji: "🟡" },
  { keys: ["chocofood", "choco"], label: "Chocofood", emoji: "🟫" },
];

export function orderChannel(order) {
  const cust = (order?.customer || "").toLowerCase();
  const agg = AGGREGATORS.find((a) => a.keys.some((k) => cust.includes(k)));
  if (agg) {
    return {
      type: "aggregator",
      label: agg.label,
      badge: `${agg.emoji} ${agg.label}`,
      needsCourier: false,
    };
  }
  const st = order?.service_type;
  if (st === "DeliveryByCourier") {
    return { type: "delivery", label: "Доставка", badge: "🛵 Доставка", needsCourier: true };
  }
  if (st === "DineIn" || st === "Common") {
    return { type: "dinein", label: "Зал", badge: "🍽 Зал", needsCourier: false };
  }
  if (
    st === "DeliveryByClient" ||
    st === "PickUp" ||
    st === "ToGo" ||
    st === "DeliveryByPickUp"
  ) {
    return { type: "pickup", label: "Самовывоз", badge: "🏃 Самовывоз", needsCourier: false };
  }
  // Неизвестный тип: курьера не требуем (закрытие через «Выдан»).
  return { type: "other", label: "", badge: null, needsCourier: false };
}

export function channelBadge(order) {
  return orderChannel(order).badge;
}

// Подпись кнопки завершения для не-курьерских заказов.
export function completeLabel(channel) {
  if (channel.type === "dinein") return "🍽 Выдан в зал";
  if (channel.type === "aggregator") return `📦 Передан · ${channel.label}`;
  return "🏃 Выдан клиенту";
}
