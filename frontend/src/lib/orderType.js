// Канал заказа: доставка / зал / самовывоз / агрегатор (Wolt/Яндекс/Glovo).
// Курьера из нашего штата назначаем ТОЛЬКО на доставку (needsCourier=true);
// зал/самовывоз/агрегатор закрываются кнопкой «Выдан» (свой курьер/клиент сам).
//
// Подписи каналов несут ключ иконки (см. components/Icon.jsx), а не эмодзи:
// на кухонных планшетах эмодзи рисуются системным шрифтом и выглядят по-разному.

const AGGREGATORS = [
  { keys: ["wolt"], label: "Wolt" },
  { keys: ["яндекс", "yandex"], label: "Яндекс.Еда" },
  { keys: ["glovo"], label: "Glovo" },
  { keys: ["chocofood", "choco"], label: "Chocofood" },
];

export function orderChannel(order) {
  // Тип оплаты у агрегаторов свой («Яндекс безнал», «Wolt Онлайн») — по нему
  // заказ опознаётся, даже когда в имени клиента агрегатора нет, а
  // service_type приехал как DeliveryByCourier (прод, заказ №91 от 07.08.2026:
  // сборщику показали выбор курьера, а iiko назначение отбил).
  const hay = `${order?.customer || ""} ${order?.payment_type_name || ""}`.toLowerCase();
  const agg = AGGREGATORS.find((a) => a.keys.some((k) => hay.includes(k)));
  if (agg) {
    return {
      type: "aggregator",
      label: agg.label,
      badge: { icon: "aggregator", text: agg.label },
      needsCourier: false,
    };
  }
  const st = order?.service_type;
  if (st === "DeliveryByCourier") {
    return {
      type: "delivery",
      label: "Доставка",
      badge: { icon: "delivery", text: "Доставка" },
      needsCourier: true,
    };
  }
  if (st === "DineIn" || st === "Common") {
    return {
      type: "dinein",
      label: "Зал",
      badge: { icon: "dinein", text: "Зал" },
      needsCourier: false,
    };
  }
  if (
    st === "DeliveryByClient" ||
    st === "PickUp" ||
    st === "ToGo" ||
    st === "DeliveryByPickUp"
  ) {
    return {
      type: "pickup",
      label: "Самовывоз",
      badge: { icon: "pickup", text: "Самовывоз" },
      needsCourier: false,
    };
  }
  // Неизвестный тип: курьера не требуем (закрытие через «Выдан»).
  return { type: "other", label: "", badge: null, needsCourier: false };
}

/** {icon, text} для плашки типа заказа, либо null. */
export function channelBadge(order) {
  return orderChannel(order).badge;
}

// Подпись кнопки завершения для не-курьерских заказов.
export function completeLabel(channel) {
  if (channel.type === "dinein") return "Выдан в зал";
  if (channel.type === "aggregator") return `Передан · ${channel.label}`;
  return "Выдан клиенту";
}
