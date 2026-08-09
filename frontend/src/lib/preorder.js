// Предзаказ — заказ «на время»: принят сейчас, готовить его нужно к сроку.
// Бэкенд присылает is_preorder / preorder_held / complete_before; экран держит
// придержанные отдельной пачкой, чтобы повар не начинал их в момент приёма.
import { parseServerTime } from "./time.js";

const TIME_FMT = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
});

/** Придержан: висит на экране, но в работу ещё не вошёл. */
export function isHeld(order) {
  return Boolean(order?.preorder_held);
}

/** Заказ ко времени (в том числе уже выпущенный в работу). */
export function isPreorder(order) {
  return Boolean(order?.is_preorder);
}

/** «к 16:00» / «завтра к 11:30» — подпись срока для карточки. */
export function dueLabel(order) {
  const ms = parseServerTime(order?.complete_before);
  if (ms == null) return null;
  const due = new Date(ms);
  const time = TIME_FMT.format(due);
  const today = new Date();
  const sameDay =
    due.getDate() === today.getDate() &&
    due.getMonth() === today.getMonth() &&
    due.getFullYear() === today.getFullYear();
  return sameDay ? `к ${time}` : `завтра к ${time}`;
}

/** Через сколько минут срок (для сортировки отложенных). */
export function minutesUntilDue(order) {
  const ms = parseServerTime(order?.complete_before);
  return ms == null ? Number.MAX_SAFE_INTEGER : Math.round((ms - Date.now()) / 60000);
}

/** Делит список на «в работе» и «отложенные предзаказы». */
export function splitPreorders(orders) {
  const live = [];
  const parked = [];
  for (const o of orders || []) (isHeld(o) ? parked : live).push(o);
  parked.sort((a, b) => minutesUntilDue(a) - minutesUntilDue(b));
  return { live, parked };
}
