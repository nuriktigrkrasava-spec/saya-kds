import { getOrgId } from "./auth.js";

// В Vite-деве (порт 5173) ходим на локальный backend :8000.
// Когда SPA раздаётся самим backend (ngrok/Render) — тот же origin (относительные пути),
// чтобы работало и в Telegram-мини-аппе, и в проде.
export const BASE =
  typeof window !== "undefined" && window.location.port === "5173"
    ? "http://localhost:8000"
    : "";
export const ORG_ID = "demo";

async function handle(res) {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${res.statusText} ${text}`.trim());
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

export async function get(path) {
  const res = await fetch(`${BASE}${path}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return handle(res);
}

export async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  return handle(res);
}

export async function put(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  return handle(res);
}

export async function del(path) {
  const res = await fetch(`${BASE}${path}`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  return handle(res);
}

// In the Telegram mini-app the SAME origin serves both API and app, so use
// same-origin relative fetches when running inside Telegram, else fall back to
// the dev BASE.
export function tgBase() {
  if (typeof window !== "undefined" && window.Telegram?.WebApp?.initData) {
    return window.location.origin;
  }
  return BASE;
}

async function tgGet(path) {
  const res = await fetch(`${tgBase()}${path}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return handle(res);
}

async function tgPost(path, body) {
  const res = await fetch(`${tgBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  return handle(res);
}

// Convenience API calls matching the backend contract.
export const api = {
  getOrders: () => get(`/orders?org_id=${getOrgId()}`),
  seed: () => post(`/debug/seed?org_id=${getOrgId()}`),
  login: (pin) => post("/auth/login", { pin }),
  acceptItem: (itemId, by) => post(`/items/${itemId}/accept`, { by }),
  readyItem: (itemId, by) => post(`/items/${itemId}/ready`, { by }),
  checkItem: (itemId, by) => post(`/items/${itemId}/check`, { by }),
  revertItem: (itemId, by) => post(`/items/${itemId}/revert`, { by }),
  assembleOrder: (orderId, by) => post(`/orders/${orderId}/assemble`, { by }),
  takeOrder: (orderId, by) => post(`/orders/${orderId}/take`, { by }),
  deliverOrder: (orderId, by, payment_kind, payment_type_id) => {
    const body = { by };
    if (payment_type_id != null) body.payment_type_id = payment_type_id;
    if (payment_kind != null) body.payment_kind = payment_kind;
    return post(`/orders/${orderId}/delivered`, body);
  },
  paymentTypes: (orgId) => get(`/payment-types?org_id=${encodeURIComponent(orgId)}`),
  // Что реально осталось получить с клиента (читается из iiko в момент запроса).
  closeInfo: (orderId) => get(`/orders/${orderId}/close-info`),

  // Assembler assigns a courier (also marks the order assembled).
  branchCouriers: (branchCode) =>
    get(`/branches/couriers?branch_code=${encodeURIComponent(branchCode)}`),
  assignCourier: (orderId, courier_code, courier_name, by) =>
    post(`/orders/${orderId}/assign-courier`, { courier_code, courier_name, by }),

  // Аналитика — этапы, SLA, журнал, таймлайн заказа.
  analytics: (orgId, from, to) =>
    get(`/admin/analytics?org_id=${encodeURIComponent(orgId)}&from=${from}&to=${to}`),
  summary: (orgId, from, to) =>
    get(`/admin/summary?org_id=${encodeURIComponent(orgId)}&from=${from}&to=${to}`),
  stageStats: (orgId, from, to, slaMinutes = 45) =>
    get(`/admin/stage-stats?org_id=${orgId}&from=${from}&to=${to}&sla_minutes=${slaMinutes}`),
  kitchenZones: (orgId, from, to) =>
    get(`/admin/kitchen-zones?org_id=${orgId}&from=${from}&to=${to}`),
  journal: (orgId, from, to, params = {}) => {
    const q = new URLSearchParams({ org_id: orgId, from, to, ...params }).toString();
    return get(`/admin/journal?${q}`);
  },
  orderTimeline: (orgId, orderId) =>
    get(`/admin/orders/${orderId}/timeline?org_id=${orgId}`),
  anomalies: (orgId) =>
    get(`/admin/anomalies?org_id=${orgId}`),
  zoneLoad: (orgId, from, to) =>
    get(`/admin/zone-load?org_id=${orgId}&from=${from}&to=${to}`),
  // Разрез заказов по типам: сколько в iiko против того, что дошло к нам.
  orderMix: (orgId, from, to) =>
    get(`/admin/order-mix?org_id=${encodeURIComponent(orgId)}&from=${from}&to=${to}`),
  // Смены: нагрузка кухни по дням недели против людей на смене (OLAP + явки RMS).
  staffing: (orgId, weeks = 4) =>
    get(`/admin/staffing?org_id=${encodeURIComponent(orgId)}&weeks=${weeks}`),
  shiftPlan: (orgId) =>
    get(`/admin/staffing/shift-plan?org_id=${encodeURIComponent(orgId)}`),
  // Опоздания — из OLAP-отчёта iiko, а не из наших отметок.
  lateDeliveries: (orgId, from, to) =>
    get(`/admin/late-deliveries?org_id=${encodeURIComponent(orgId)}&from=${from}&to=${to}`),
  courierZoneLoad: (orgId, from, to) =>
    get(`/admin/courier-zone-load?org_id=${orgId}&from=${from}&to=${to}`),
  deliveryZones: (orgId) =>
    get(`/admin/delivery-zones?org_id=${encodeURIComponent(orgId)}`),
  demandZones: (orgId) =>
    get(`/admin/demand-zones?org_id=${encodeURIComponent(orgId)}`),
  autoGridZones: (orgId, n = 6) =>
    post(`/admin/zones/auto-grid?org_id=${encodeURIComponent(orgId)}&n=${n}`),
  createDeliveryZone: (body) => post(`/admin/delivery-zones`, body),
  // org_id обязателен: бэкенд не даёт править/удалять зоны чужого филиала.
  updateDeliveryZone: (id, orgId, body) =>
    put(`/admin/delivery-zones/${id}?org_id=${encodeURIComponent(orgId)}`, body),
  deleteDeliveryZone: (id, orgId) =>
    del(`/admin/delivery-zones/${id}?org_id=${encodeURIComponent(orgId)}`),

  // Telegram mini-app courier helpers (same-origin in Telegram).
  tgAuth: (initData) => tgPost("/tg/auth", { initData }),
  tgShiftEmployees: () => tgGet("/tg/shift-employees"),
  tgClaim: (initData, employee_id) => tgPost("/tg/claim", { initData, employee_id }),
  tgMyOrders: (courier_code) =>
    tgGet(`/tg/my-orders?courier_code=${encodeURIComponent(courier_code)}`),
  tgDeliveries: (org_id) =>
    tgGet(`/tg/deliveries?org_id=${encodeURIComponent(org_id)}`),
  tgLocation: (initData, lat, lon) => tgPost("/tg/location", { initData, lat, lon }),
  tgUnlink: (initData) => tgPost("/tg/unlink", { initData }),
};
